"""Guarded DMP GLES kernel interface (/dev/dmpgles2).

Historical tables and assumed engine experiments remain reproducible, not
qualified hardware contracts. The opt-in kernel-tgid policy uses original
completion ownership/reset rules and guards software-shared bytes and MMIO.
"""
import struct

from src.a6400.execution.guest_memory import accessible
from src.a6400.execution.kernel_task_identity import KernelTaskObservation, UnsupportedKernelBinding

GLES_IOCTL_MAGIC = 0x82
GLES_GET_VERSION = 0x80048200
GLES_GET_CONTEXT = 0xc0048201
GLES_QUERY_RESOURCE = 0xc0048202
GLES_RELEASE_RESOURCE = 0xc0048203
GLES_RESET = 0x40048204
GLES_SET_POWER_ON = 0xc0048204
GLES_GET_SUBDEVICE_ID = 0x80048205
GLES_GET_MMIO_SIZE = 0x80048206
GLES_GET_ALIGNMENT_DECODED = 0x80048207
GLES_CLOCK_DOWN = 0xc0048208
GLES_NEXT_EVENT_SEQ = 0xc004820c
GLES_GET_RESOURCE_SUB = 0xc004820d
GLES_SIGNAL_COMPLETION = 0xc004820e
GLES_SET_POWER_OFF = 0xc004820f
GLES_WAIT_COMPLETION = 0xc0048209
GLES_LOCK_SEMAPHORE = 0xc0048211
GLES_UNLOCK_SEMAPHORE = 0xc0048212
GLES_GET_ALIGNMENT = 0x80048260
GLES_GET_POWER_STATE = 0x80048263
GLES_GET_POWER_STATE_ALT = 0x8004820a
# grm_gles.ko-decoded additional commands (E0341): kick control writes
# BAR+0xC0 with (value|0x20000000); 0x80048263 returns the driver status word.
GLES_KICK_CONTROL = 0xc004820f
GLES_DRIVER_STATUS = 0x80048263


class UnsupportedGles(RuntimeError):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = {'reason': reason, 'device': '/dev/dmpgles2', **details}


class GlesBinding:
    """Authentic process binding requiring reciprocal kernel task identity and TASK_SIZE bounds."""

    def __init__(self, task_observation=None, table=None):
        if table is None and task_observation is not None and hasattr(task_observation, 'observation_for'):
            table = task_observation
            task_observation = None
        if table is not None:
            self.table = table
            self.task = None
            self.address_limit = table.address_limit
        elif task_observation is not None:
            self.table = None
            self.task = task_observation
            self.address_limit = task_observation.address_limit
        else:
            raise ValueError('GlesBinding requires a task observation or table')
        if self.address_limit > 0x5f000000:
            raise UnsupportedKernelBinding('task_address_limit_exceeds_task_size',
                                           limit=self.address_limit)

    def validate_caller(self, guest_pid, guest_tgid, user_ranges=()):
        if self.table is not None:
            observation = self.table.observation_for(guest_pid)
            if observation is None:
                raise UnsupportedKernelBinding('kernel_task_record_not_observed',
                                               pid=guest_pid, tgid=guest_tgid)
        else:
            observation = self.task
        return observation.require_caller(guest_pid=guest_pid, guest_tgid=guest_tgid,
                                          user_ranges=user_ranges)


class GuestGles:
    """Emulates /dev/dmpgles2 device file operations conforming to grm_gles.ko."""

    def __init__(self, binding: GlesBinding = None, *, completion_policy='legacy'):
        if completion_policy not in ('legacy', 'kernel-tgid', 'kernel-sugilite'):
            raise ValueError(f'Unknown GLES completion policy: {completion_policy}')
        self.binding = binding
        self.completion_policy = completion_policy
        self.tgid_completions = {}
        self.shared_mapping = None
        self.shared_unknown = {}
        self.dev_open_count = 0
        self.dev_event_seq = 0
        self.shared_header_zeroed = False
        self.sems = {
            0: {'count': 1, 'waiters': [], 'ready_tid': None},
            1: {'count': 1, 'waiters': [], 'ready_tid': None},
            2: {'count': 1, 'waiters': [], 'ready_tid': None},
        }
        self.sugilite_gpu = None
        self.owners = {}
        self.transactions = []
        self.major = 248
        self.minor = 29
        # Opt-in semantics decoded from grm_gles.ko dispatch (E0337/E0341):
        # 0x8004820A reads the suspend/status word (dev+0x48, kzalloc 0) and
        # 0xC004820D is wait-for-completion (out 1 only when a completion is
        # actually pending). The historical default keeps the earlier table.
        self.decoded_semantics = completion_policy in ('kernel-tgid', 'kernel-sugilite')
        # Physical read-only evidence (E0313): GPES/Sugilite BAR word 0 reads
        # 0x000b0000 and status word 0x34 carries 0x40000000/0x80000000 during
        # operation. The BAR is an emulated zero page; seed only the observed
        # identity word and let guest writes update the image (never the host).
        self.bar = bytearray(0x4000)
        struct.pack_into('<I', self.bar, 0, 0x000b0000)
        self.bar_virtual = None
        self.bar_length = 0
        # Opt-in device-work model: a guest GPES command submission (BAR header
        # +0xE8=1/+0xEA=5, observed e0377) completed by the engine yields the
        # physically observed IRQ class 0x40000000, which the decoded IRQ ack
        # path clears. It is a device model only; no guest ctx is completed.
        self.engine_model = False
        self.engine_command_seen = False
        self.engine_completions = 0
        self.engine_event_status = 0
        self.dev_a8 = 0
        self.event_waiters = []
        self.irq_waiters = set()
        self.engine_state_events = 0
        # Opt-in DMP SMAPH/P3D + PPF + PSC BAR model (Oein/a5000-emulator RE_DISPLAY_GPU.md).
        self.p3d_model = False
        self._p3d_mmio = None
        if completion_policy == 'kernel-sugilite':
            from src.a6400.execution.sugilite_gpu import SugiliteGpuEngine
            self.sugilite_gpu = SugiliteGpuEngine()

    def __setstate__(self, state):
        """Restore old checkpoints that predate the BAR/engine-model fields."""
        state = dict(state)
        raw_backing = state.pop('_shared_backing_bytes', None)
        self.__dict__.update(state)
        if raw_backing is not None:
            import ctypes
            self._shared_backing = (ctypes.c_char * len(raw_backing)).from_buffer_copy(raw_backing)
        if not hasattr(self, 'irq_waiters'):
            self.irq_waiters = set()
        if not hasattr(self, 'completion_policy'):
            self.completion_policy = 'legacy'
        if not hasattr(self, 'tgid_completions'):
            self.tgid_completions = {}
        if not hasattr(self, 'shared_mapping'):
            self.shared_mapping = None
        if not hasattr(self, 'shared_unknown'):
            self.shared_unknown = {}
        if not hasattr(self, 'dev_open_count'):
            self.dev_open_count = len(getattr(self, 'owners', {}))
        if not hasattr(self, 'dev_event_seq'):
            self.dev_event_seq = 0
        if not hasattr(self, 'shared_header_zeroed'):
            self.shared_header_zeroed = False
        if not hasattr(self, 'sems'):
            self.sems = {
                0: {'count': 1, 'waiters': [], 'ready_tid': None},
                1: {'count': 1, 'waiters': [], 'ready_tid': None},
                2: {'count': 1, 'waiters': [], 'ready_tid': None},
            }
        if not hasattr(self, 'sugilite_gpu'):
            self.sugilite_gpu = None
        if not hasattr(self, 'bar'):
            self.bar = bytearray(0x4000)
            struct.pack_into('<I', self.bar, 0, 0x000b0000)
        if not hasattr(self, 'bar_virtual'):
            self.bar_virtual = None
        if not hasattr(self, 'bar_length'):
            self.bar_length = 0
        if not hasattr(self, 'engine_model'):
            self.engine_model = False
        if not hasattr(self, 'engine_command_seen'):
            self.engine_command_seen = False
        if not hasattr(self, 'engine_completions'):
            self.engine_completions = 0
        if not hasattr(self, 'engine_event_status'):
            self.engine_event_status = 0
        if not hasattr(self, 'dev_a8'):
            self.dev_a8 = 0
        if not hasattr(self, 'event_waiters'):
            self.event_waiters = []
        if not hasattr(self, 'irq_waiters'):
            self.irq_waiters = set()
        if not hasattr(self, 'engine_state_events'):
            self.engine_state_events = 0
        if not hasattr(self, 'engine_submitted'):
            self.engine_submitted = False
        if not hasattr(self, 'engine_last_key'):
            self.engine_last_key = None
        if not hasattr(self, 'engine_bar_completed'):
            self.engine_bar_completed = False
        if not hasattr(self, 'p3d_model'):
            self.p3d_model = False
        if not hasattr(self, '_p3d_mmio'):
            self._p3d_mmio = None

    def open(self, flags, caller_pid, caller_tgid, user_ranges=()):
        if self.binding is None:
            raise UnsupportedGles('gles_kernel_process_binding_not_supplied',
                                  flags=flags, guest_tid=caller_pid, guest_tgid=caller_tgid)

        # Validate caller task identity and user space bounds (TASK_SIZE < 0x5f000000)
        self.binding.validate_caller(caller_pid, caller_tgid, user_ranges)

        owner_id = len(self.owners) + 1
        owner = {
            'owner_id': owner_id,
            'flags': flags,
            'caller_pid': caller_pid,
            'caller_tgid': caller_tgid,
            'ref_count': 1,
            'hardware_version': 2,
            'mmio_base': 0xf1000000,
            'mmio_size': 0x4000,
            'sub_device_id': 1,
            'power_state': 1,
            'suspend_status': 0,
            'completion_pending': False,
            'ctx': {},
            'ctx_by_key': {},
            'kick_value': 0,
            'global_status': 1,
            'counter': 0,
            'resource_table': {
                0: 0x80000000,
                1: 0xf1000000,
                2: 1,
                3: 0x4000
            }
        }
        self.owners[owner_id] = owner
        self.dev_open_count += 1
        first_open = (self.dev_open_count == 1)
        if first_open and self.completion_policy == 'kernel-sugilite':
            # Verified in grm_gles.ko sugilite_open (+0x47c..+0x548):
            # atomic_inc_return(&dev->open_count) == 1 clears dev+0x44 (event_status = 0),
            # calls __memzero(dev->shared_area, 4), and resets sems[0..2] to count 1.
            self.dev_event_seq = 0
            self.shared_header_zeroed = True
            for sem_idx in (0, 1, 2):
                self.sems[sem_idx] = {'count': 1, 'waiters': [], 'ready_tid': None}
            if self.shared_mapping is not None:
                vbase = self.shared_mapping['virtual']
                for off in range(4):
                    self.shared_unknown.pop(vbase + off, None)
        if self.completion_policy in ('kernel-tgid', 'kernel-sugilite'):
            self.tgid_completions.setdefault(
                caller_tgid, {'fast': False, 'done': 0x7fffffff, 'waiters': {}})
        self.transactions.append({
            'operation': 'open', 'flags': flags, 'owner_id': owner_id,
            **({'dev_open_count': self.dev_open_count, 'first_open_zeroed_4b': first_open}
               if self.completion_policy == 'kernel-sugilite' else {}),
        })
        return owner

    def close(self, owner_id):
        """Model sugilite_release (grm_gles.ko + 0x310): decrement dev->open_count and owner ref."""
        owner = self.owners.get(owner_id)
        if owner is None:
            return
        if self.dev_open_count > 0:
            self.dev_open_count -= 1
        owner['ref_count'] = max(0, owner.get('ref_count', 1) - 1)
        if owner['ref_count'] == 0:
            tgid = owner.get('caller_tgid')
            del self.owners[owner_id]
            if (tgid is not None and self.completion_policy in ('kernel-tgid', 'kernel-sugilite')
                    and not any(o.get('caller_tgid') == tgid for o in self.owners.values())):
                self.tgid_completions.pop(tgid, None)
        self.transactions.append({'operation': 'close', 'owner_id': owner_id,
                                  'dev_open_count': self.dev_open_count})

    def ioctl(self, owner_id, cmd, arg, mem_read, mem_write, tid=None):
        owner = self.owners.get(owner_id)
        if not owner:
            raise UnsupportedGles('gles_invalid_owner_descriptor', owner_id=owner_id)

        if self.completion_policy in ('kernel-tgid', 'kernel-sugilite') and cmd in (
                GLES_RELEASE_RESOURCE, GLES_GET_RESOURCE_SUB, GLES_SIGNAL_COMPLETION):
            return self._kernel_completion_ioctl(cmd, arg, mem_read, mem_write, tid)

        if cmd == GLES_GET_VERSION:
            # Verified in E0064/E0065: returns hardware version 2 as uint32_t
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            version_bytes = struct.pack('<I', owner['hardware_version'])
            mem_write(arg, version_bytes)
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'version': owner['hardware_version']})
            return 0

        if cmd == GLES_GET_MMIO_SIZE:
            # Verified in grm_gles.ko at 0x1604 / 0xdcc: returns hardware MMIO size (0x4000 = 16 KiB)
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            size_val = owner.get('mmio_size', 0x4000)
            mem_write(arg, struct.pack('<I', size_val))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'mmio_size': size_val})
            return 0

        if cmd == GLES_GET_SUBDEVICE_ID:
            # Verified in grm_gles.ko at 0x15fc / 0xda8: returns sub-device index 0
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            sub_id = owner.get('sub_device_id', 0)
            mem_write(arg, struct.pack('<I', sub_id))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'sub_device_id': sub_id})
            return 0

        if cmd in (GLES_GET_POWER_STATE, GLES_GET_POWER_STATE_ALT):
            # Verified in grm_gles.ko at 0xedc: returns power state (1 = powered on).
            # Decoded mode: 0x8004820A is the suspend/status word dev+0x48 (0 at probe).
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            pwr = owner.get('suspend_status', 0) if self.decoded_semantics else owner.get('power_state', 1)
            mem_write(arg, struct.pack('<I', pwr))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'power_state': pwr,
                                     **({'decoded': 'suspend_status'} if self.decoded_semantics else {})})
            return 0

        if cmd == GLES_RELEASE_RESOURCE:
            # grm_gles.ko dispatch: 0xC0048203 -> handler 0xAD0, an event wait on
            # dev+0xA8 (updated by the IRQ handler from BAR+0x34 status). The
            # historical name "release resource" came from the same cmd value;
            # decoded mode uses the verified event-wait semantics.
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            raw_mask = mem_read(arg, 4)
            mask = struct.unpack('<I', raw_mask)[0]
            if not self.decoded_semantics:
                self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                          'returned': 0, 'mask': mask})
                return 0
            if mask & 0xC4000000 == 0:
                if not getattr(self, 'engine_model', False):
                    # Not an event mask: keep the historical mask-release reply used
                    # by earlier probes/tests rather than inventing event semantics.
                    self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                              'returned': 0, 'mask': mask,
                                              'decoded': 'mask_release_not_event'})
                    return 0
                # The resumed guest can be mid-loop with a zeroed mask word; the
                # documented event-wait class from the guest/kernel contract is
                # 0x04000000 (task-51/task-58), so use it for this opt-in model.
                mask = 0x04000000
            if getattr(self, 'engine_model', False):
                self._poll_engine_command(mem_read, mem_write)
                if (self.dev_a8 == 0 and getattr(self, 'engine_submitted', False)
                        and getattr(self, 'engine_state_events', 0) < 16):
                    # Bounded engine-state event source: the physical BAR+0x34
                    # poll (E0313) observed the states 0x80000000 (492x) and
                    # 0x40000000 (793x) while the engine was active; deliver at
                    # most 16 of these per submitted descriptor, matching the
                    # class the caller is waiting for.
                    self.engine_state_events += 1
                    if mask & 0x80000000:
                        event_bit = 0x80000000
                    elif mask & 0x04000000:
                        event_bit = 0x04000000
                    else:
                        event_bit = 0x80000000
                    # 0x01000000 is the event-present bit the guest's 0x72B660
                    # tests; the class bit alone keeps it in the wait loop
                    # (task-68/E0447). Include it with the selected class.
                    self.dev_a8 |= event_bit | 0x01000000
                    self.transactions.append({'operation': 'engine-state',
                                              'event_bit': event_bit,
                                              'index': self.engine_state_events,
                                              'mask': mask, 'tid': tid})
                # grm_gles.ko loop checks [dev+0xA8] for a pending status; the
                # mask selects which classes the caller cares about but any
                # pending event is reported (observed guest masks include
                # 0x80000000 before the documented 0x04000000).
                ready = self.dev_a8
                if ready == 0:
                    self.event_waiters.append(tid)
                    self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                              'returned': None, 'decoded': 'wait_event_pending',
                                              'mask': mask, 'dev_a8': self.dev_a8,
                                              'engine_completions': self.engine_completions})
                    return None
                status = self.dev_a8
                # The driver snapshot is consumed by the wait: clear the bits
                # that were reported so the next wait blocks until a new event
                # (guest ack writes may be skipped by the guest BAR-write gate).
                self.dev_a8 &= ~status
                mem_write(arg, struct.pack('<I', status))
                self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                          'returned': 0, 'decoded': 'wait_event',
                                          'mask': mask, 'status': status,
                                          'engine_completions': self.engine_completions})
                return 0
            status = getattr(self, 'engine_event_status', 0)
            if status & mask:
                self.engine_event_status &= ~mask
            mem_write(arg, struct.pack('<I', status))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                      'returned': 0, 'decoded': 'wait_event',
                                      'mask': mask, 'status': status,
                                      'engine_completions': self.engine_completions})
            return 0

        if cmd == GLES_QUERY_RESOURCE:
            # Verified in grm_gles.ko at 0x930 -> 0xa88 (sugilite_ioctl case 0xc0048202):
            # Reads uint32 resource_index from *arg
            # If index == 0: 0x80000000
            # If index != 0: *(dev + 0xfc + index * 4) -> 1: 0xf1000000 (MMIO base), 2: 0 (subdev), 3: 0x4000 (size)
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            raw_index = mem_read(arg, 4)
            index = struct.unpack('<I', raw_index)[0]
            val = owner.get('resource_table', {}).get(index, 0)
            mem_write(arg, struct.pack('<I', val))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'resource_index': index, 'resource_value': val})
            return 0

        if cmd in (GLES_GET_ALIGNMENT, GLES_GET_ALIGNMENT_DECODED):
            # Verified in grm_gles.ko at 0xdf0: returns 0x200 (512 bytes buffer alignment)
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            mem_write(arg, struct.pack('<I', 0x200))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'alignment': 0x200})
            return 0

        if cmd == GLES_CLOCK_DOWN and self.decoded_semantics:
            # Verified in grm_gles.ko at 0xe14: sets suspend_status (dev+0x48) = 1
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            prev = owner.get('suspend_status', 0)
            owner['suspend_status'] = 1
            changed = 0 if prev == 1 else 1
            mem_write(arg, struct.pack('<I', changed))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'clock_down_changed': changed})
            return 0

        if cmd == GLES_NEXT_EVENT_SEQ and self.decoded_semantics:
            # Verified in grm_gles.ko at 0xf00: atomic_inc_return(&dev->event_status) at dev+0x44
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            self.dev_event_seq = (self.dev_event_seq + 1) & 0xFFFFFFFF
            mem_write(arg, struct.pack('<I', self.dev_event_seq))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'event_seq': self.dev_event_seq})
            return 0

        if cmd == GLES_GET_CONTEXT:
            # Verified in grm_gles.ko at 0x1294: returns context 1
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            mem_write(arg, struct.pack('<I', 1))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'context': 1})
            return 0

        if cmd == GLES_RESET:
            # Verified in grm_gles.ko at 0xda0: resets and returns 0
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0})
            return 0

        if cmd == GLES_SET_POWER_ON:
            # Verified in grm_gles.ko at 0xe14: sets power_state = 1
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            prev = owner.get('power_state', 1)
            owner['power_state'] = 1
            ret = 1 if prev != 1 else 0
            mem_write(arg, struct.pack('<I', ret))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'power_changed': ret})
            return 0

        if cmd == GLES_SET_POWER_OFF:
            # Verified in grm_gles.ko at 0xe74: sets power_state = 0
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            prev = owner.get('power_state', 1)
            owner['power_state'] = 0
            ret = 1 if prev != 0 else 0
            mem_write(arg, struct.pack('<I', ret))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'power_changed': ret})
            return 0

        if cmd == GLES_LOCK_SEMAPHORE:
            # Verified in grm_gles.ko at 0xa10 -> 0x1194 (down_interruptible on dev->sems[sem_id]):
            # Reads uint32 sem_id from *arg.
            # If sem_id <= 2, acquires semaphore, writes 0 (success) back to *arg, returns 0.
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            raw_id = mem_read(arg, 4)
            sem_id = struct.unpack('<I', raw_id)[0]
            if sem_id > 2:
                return -22  # -EINVAL
            if self.completion_policy == 'kernel-sugilite' and tid is not None:
                sem = self.sems[sem_id]
                if sem.get('ready_tid') == tid:
                    sem['ready_tid'] = None
                elif tid in sem['waiters']:
                    self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                              'returned': None, 'lock_semaphore': sem_id, 'tid': tid})
                    return None
                elif sem['count'] > 0:
                    sem['count'] -= 1
                else:
                    sem['waiters'].append(tid)
                    self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                              'returned': None, 'lock_semaphore': sem_id, 'tid': tid})
                    return None
            mem_write(arg, struct.pack('<I', 0))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'lock_semaphore': sem_id})
            return 0

        if cmd == GLES_UNLOCK_SEMAPHORE:
            # Verified in grm_gles.ko at 0xa24 -> 0x11e8 (up on dev->sems[sem_id]):
            # Reads uint32 sem_id from *arg.
            # If sem_id <= 2, releases semaphore, writes 0 back to *arg, returns 0.
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            raw_id = mem_read(arg, 4)
            sem_id = struct.unpack('<I', raw_id)[0]
            if sem_id > 2:
                return -22  # -EINVAL
            woken_tids = []
            if self.completion_policy == 'kernel-sugilite':
                sem = self.sems[sem_id]
                if sem['waiters'] and sem.get('ready_tid') is None:
                    nxt = sem['waiters'].pop(0)
                    sem['ready_tid'] = nxt
                    woken_tids.append(nxt)
                else:
                    sem['count'] = 1
            mem_write(arg, struct.pack('<I', 0))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'unlock_semaphore': sem_id, 'woken_tids': woken_tids})
            return 0

        if cmd == GLES_SIGNAL_COMPLETION:
            # Verified in grm_gles.ko at 0xa08 -> 0x1004 (complete_all on dev event/completion):
            # Reads uint32 event_id from *arg. Decoded mode uses the E0345 keyed model:
            # key 0 completes the current task's ctx; a nonzero key completes only a
            # registered ctx (ctx_by_key); unknown keys write the kernel failure
            # sentinel 0x1FFFFFFF and complete nothing (guest maps it to -1).
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            raw_id = mem_read(arg, 4)
            event_id = struct.unpack('<I', raw_id)[0]
            if self.decoded_semantics:
                if tid is None:
                    raise UnsupportedGles('gles_task_identity_required', cmd=hex(cmd))
                ctx_map = owner.setdefault('ctx', {})
                # Kernel completion identity: P=[arg+0xC], ctx=[P+0x108]
                # (task-62). Fallback key is the process tgid, labelled.
                ctx_key, ctx_info = self._ctx_identity(mem_read, arg, tid, owner, 'signal')
                if event_id == 0:
                    ctx_map.setdefault(ctx_key, {'done': False})['done'] = True
                    # The kernel wait resolves its ctx from the current task
                    # (not the signal's user descriptor); the process-level key
                    # is the model proxy for that task ctx. Mark it too, as an
                    # explicit assumption (gles_ctx_process_completion_assumption),
                    # so a key-0 signal from a thread of the process can release
                    # the process' waiter.
                    process_key = owner.get('caller_tgid') or tid
                    if process_key != ctx_key:
                        ctx_map.setdefault(process_key, {'done': False})['done'] = True
                    mem_write(arg, struct.pack('<I', 0))
                    outcome = 'complete_current_task'
                else:
                    target = owner.setdefault('ctx_by_key', {}).get(event_id)
                    if target is None:
                        mem_write(arg, struct.pack('<I', 0x1FFFFFFF))
                        outcome = 'missing_key'
                    else:
                        ctx_map.setdefault(target, {'done': False})['done'] = True
                        mem_write(arg, struct.pack('<I', 0))
                        outcome = 'complete_keyed_task'
                self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                          'returned': 0, 'signal_completion': event_id,
                                          'decoded': outcome, 'tid': tid, **ctx_info})
                return 0
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'signal_completion': event_id})
            return 0

        if cmd == GLES_WAIT_COMPLETION:
            # Verified in grm_gles.ko at 0x9b4 -> 0xf40 (wait_for_completion_interruptible_timeout):
            # Writes 1 (completed) to *arg, returns 0. Decoded mode: 0xC0048209 is the
            # suspend/status consume query (out 1 only while dev+0x48 is set, then cleared).
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            if self.decoded_semantics:
                pending = owner.get('suspend_status', 0)
                owner['suspend_status'] = 0
                mem_write(arg, struct.pack('<I', 1 if pending else 0))
                self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                         'suspend_status_consume': 1 if pending else 0})
                return 0
            mem_write(arg, struct.pack('<I', 1))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'wait_completion': 1})
            return 0

        if cmd == GLES_GET_RESOURCE_SUB:
            # Verified in grm_gles.ko at 0xa50: *(dev + 0x104 + index * 4).
            # Decoded mode: the same command number 0xC004820D is wait-for-completion
            # (sugilite_ioctl 0xf40): out 1 only when a completion is pending, else 0.
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            if self.decoded_semantics:
                if tid is None:
                    raise UnsupportedGles('gles_task_identity_required', cmd=hex(cmd))
                ctx_key, ctx_info = self._ctx_identity(mem_read, arg, tid, owner, 'wait')
                ctx = owner.setdefault('ctx', {}).setdefault(ctx_key, {'done': False})
                if ctx['done']:
                    ctx['done'] = False
                    mem_write(arg, struct.pack('<I', 1))
                    self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                              'returned': 0, 'decoded': 'wait_completion',
                                              'completed': 1, 'tid': tid, **ctx_info})
                    return 0
                # Kernel blocks in wait_for_completion_interruptible_timeout here;
                # the interpreter parks the guest thread and wakes it after a
                # matching 0xC004820E signal (E0345/E0353).
                self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg,
                                          'returned': None, 'decoded': 'wait_completion',
                                          'completed': 0, 'tid': tid, **ctx_info})
                return None
            raw_index = mem_read(arg, 4)
            index = struct.unpack('<I', raw_index)[0]
            val = owner.get('sub_device_id', 1) if index == 0 else 0
            mem_write(arg, struct.pack('<I', val))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'resource_sub_index': index, 'val': val})
            return 0

        if cmd == GLES_KICK_CONTROL and self.decoded_semantics:
            # grm_gles.ko 0x1248: store value and iowrite32(value|0x20000000, BAR+0xC0).
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            value = struct.unpack('<I', mem_read(arg, 4))[0]
            if value > 1 and self.completion_policy in ('kernel-tgid', 'kernel-sugilite'):
                return -14  # grm_gles.ko + 0x1264..0x1274: value > 1 -> -EFAULT
            owner['kick_value'] = value
            if self.sugilite_gpu is not None:
                self.sugilite_gpu.write_mmio(0xC0, value | 0x20000000)
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'kick_control': value, 'decoded': True})
            return 0

        if cmd == GLES_DRIVER_STATUS and self.decoded_semantics:
            # grm_gles.ko 0x1294: put_user(driver global status word).
            if arg >= self.binding.address_limit:
                raise UnsupportedGles('user_pointer_outside_task_limit', address=arg)
            mem_write(arg, struct.pack('<I', owner.get('global_status', 1)))
            self.transactions.append({'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'returned': 0,
                                     'driver_status': owner.get('global_status', 1)})
            return 0

        raise UnsupportedGles('unsupported_gles_ioctl', cmd=hex(cmd), arg=hex(arg), owner_id=owner_id)

    def mmap2(self, mapper, addr, length, prot, flags, pgoff, owner_id):
        owner = self.owners.get(owner_id)
        if not owner:
            raise UnsupportedGles('gles_invalid_owner_descriptor', owner_id=owner_id)
        if self.completion_policy in ('kernel-tgid', 'kernel-sugilite'):
            if pgoff != 0 or length not in (0x4000, 0x20000):
                raise UnsupportedGles('gles_mapping_shape_unqualified', length=length, pgoff=pgoff)
            if length == 0x20000:
                if self.shared_mapping is not None and self.completion_policy != 'kernel-sugilite':
                    raise UnsupportedGles('gles_shared_alias_not_modelled')
                event = mapper.request_mmap2(addr, length, prot, flags | 0x20, -1, 0)
                virtual = event['returned']
                if 0 <= virtual < 0x80000000:
                    mapper.mark_device_range(virtual, length, 'dmpgles2-shared')
                    if self.completion_policy == 'kernel-sugilite':
                        import ctypes
                        if getattr(self, '_shared_backing', None) is None:
                            self._shared_backing = (ctypes.c_char * length)()
                        mapper.mu.mem_unmap(virtual, length)
                        mapper.mu.mem_map_ptr(virtual, length, prot, ctypes.addressof(self._shared_backing))
                    if self.shared_mapping is None:
                        self.shared_mapping = {'virtual': virtual, 'length': length, 'aliases': [virtual]}
                        start_unknown = 4 if (self.completion_policy == 'kernel-sugilite' and self.shared_header_zeroed) else 0
                        self.shared_unknown = {
                            virtual + offset: 'gles_shared_uninitialized'
                            for offset in range(start_unknown, length)
                        }
                    else:
                        self.shared_mapping.setdefault('aliases', []).append(virtual)
                event['gles_mapping_kind'] = 'software-shared'
                self.transactions.append({'operation': 'mmap2', 'virtual': virtual,
                                          'length': length, 'mapping_kind': 'software-shared',
                                          'physical': None, 'pgoff': pgoff})
                return event
        # sugilite_mmap_dev maps the device window (BAR 0xF1000000) at offset 0;
        # the real camera's mapping is 128 KiB with pgoff 0 (E0313).
        phys_addr = 0xf1000000 if pgoff == 0 else (pgoff * 4096)
        event = mapper.request_mmap2(addr, length, prot, flags | 0x20, -1, 0)
        virtual = event['returned']
        if 0 <= virtual < 0x80000000:
            mapper.mark_device_range(virtual, length, 'dmpgles2')
            if phys_addr == 0xf1000000:
                self.bar_virtual = virtual
                self.bar_length = min(length, len(self.bar))
        self.transactions.append({'operation': 'mmap2', 'virtual': virtual, 'length': length,
                                  'physical': phys_addr, 'pgoff': pgoff})
        return event

    def enable_tgid_completion(self):
        """A pre-open checkpoint can select the contract without inventing driver state."""
        if self.completion_policy == 'kernel-tgid':
            return
        if (self.owners or self.transactions or self.bar_virtual is not None
                or self.engine_model or self.p3d_model or self.dev_a8
                or self.engine_command_seen or self.engine_completions
                or self.engine_event_status or self.shared_mapping is not None):
            raise UnsupportedGles('gles_completion_policy_requires_preopen_state')
        self.completion_policy = 'kernel-tgid'
        self.decoded_semantics = True

    def enable_sugilite_execution(self, phys_reader=None, phys_writer=None):
        """Enable verified first-open 4-byte shared init + Sugilite GPU & IRQ 76 model."""
        if self.completion_policy == 'kernel-sugilite':
            if self.sugilite_gpu is None:
                from src.a6400.execution.sugilite_gpu import SugiliteGpuEngine
                self.sugilite_gpu = SugiliteGpuEngine(phys_reader=phys_reader, phys_writer=phys_writer)
            else:
                self.sugilite_gpu.phys_reader = phys_reader
                self.sugilite_gpu.phys_writer = phys_writer
            return
        if (self.owners or self.transactions or self.bar_virtual is not None
                or self.engine_model or self.p3d_model or self.dev_a8
                or self.engine_command_seen or self.engine_completions
                or self.engine_event_status or self.shared_mapping is not None):
            raise UnsupportedGles('gles_completion_policy_requires_preopen_state')
        from src.a6400.execution.sugilite_gpu import SugiliteGpuEngine
        self.completion_policy = 'kernel-sugilite'
        self.decoded_semantics = True
        self.sugilite_gpu = SugiliteGpuEngine(phys_reader=phys_reader, phys_writer=phys_writer)

    enable_sugilite_engine = enable_sugilite_execution

    def __getstate__(self):
        state = dict(self.__dict__)
        backing = state.pop('_shared_backing', None)
        if backing is not None:
            state['_shared_backing_bytes'] = bytes(backing)
        return state

    def seed_sugilite_bar_memory(self, mem_write):
        """Populate mapped BAR VA with initial Sugilite register state."""
        if self.bar_virtual is None or self.sugilite_gpu is None:
            return
        words = [self.sugilite_gpu.read_mmio(off) for off in range(0, 0x4000, 4)]
        mem_write(self.bar_virtual, struct.pack('<4096I', *words))

    def handle_sugilite_mmio_write(self, offset: int, size: int, value: int, mem_write=None) -> int:
        """Execute one BAR write and run sugilite_interrupt if an engine raised IRQ 76."""
        if self.sugilite_gpu is None:
            raise UnsupportedGles('sugilite_gpu_not_enabled', offset=offset)
        aligned = offset & ~3
        if size == 4 and offset == aligned:
            word = value & 0xFFFFFFFF
        else:
            cur = self.sugilite_gpu.read_mmio(aligned)
            shift = (offset & 3) * 8
            mask = ((1 << (size * 8)) - 1) << shift
            word = ((cur & ~mask) | ((value << shift) & mask)) & 0xFFFFFFFF
        self.sugilite_gpu.write_mmio(aligned, word)
        added = 0
        if self.sugilite_gpu.status != 0:
            added = self.sugilite_gpu.service_interrupt()
            if added:
                self.dev_a8 |= added
                self.engine_completions += 1
        if mem_write is not None and self.bar_virtual is not None:
            # Mirror current register values so guest MMIO loads read authentic hardware state.
            mem_write(self.bar_virtual + aligned, struct.pack('<I', self.sugilite_gpu.read_mmio(aligned)))
            mem_write(self.bar_virtual + 0x34, struct.pack('<I', self.sugilite_gpu.read_mmio(0x34)))
            for ack_off in (0x1C, 0xC18, 0x1000, 0x1800):
                mem_write(self.bar_virtual + ack_off, struct.pack('<I', self.sugilite_gpu.read_mmio(ack_off)))
        return added

    def _kernel_completion_ioctl(self, cmd, arg, mem_read, mem_write, tid):
        if self.engine_model or self.p3d_model:
            raise UnsupportedGles('gles_unqualified_engine_with_tgid_contract')
        observation = (self.binding.table.observation_for(tid)
                       if self.binding.table is not None else self.binding.task)
        if observation is None or observation.pid != tid:
            raise UnsupportedGles('gles_task_identity_required', tid=tid)
        tgid = observation.tgid
        self.binding.validate_caller(tid, tgid)
        event = {'operation': 'ioctl', 'cmd': cmd, 'arg': arg, 'tid': tid,
                 'tgid': tgid, 'completion_policy': 'kernel-tgid', 'woken_tids': []}
        if not 0 <= arg <= observation.address_limit - 4:
            self.transactions.append({**event, 'returned': -14, 'reason': 'user_word_outside_task_limit'})
            return -14
        word = None
        if cmd != GLES_GET_RESOURCE_SUB:
            if any(address in self.shared_unknown for address in range(arg, arg + 4)):
                raise UnsupportedGles('gles_shared_argument_uninitialized', address=arg)
            raw = mem_read(arg, 4)
            if len(raw) != 4:
                raise UnsupportedGles('gles_user_word_not_supplied', address=arg, length=len(raw))
            word = struct.unpack('<I', raw)[0]
        if cmd == GLES_RELEASE_RESOURCE:
            mask = word & 0xc4000000
            if mask == 0:
                self.transactions.append({**event, 'returned': -14, 'reason': 'invalid_irq_mask'})
                return -14
            if self.dev_a8 & mask:
                self.dev_a8 &= ~mask
                self.irq_waiters.discard(tid)
                mem_write(arg, struct.pack('<I', mask))
                self.transactions.append({**event, 'returned': 0, 'decoded': 'irq_wait',
                                          'mask': mask, 'status': mask})
                return 0
            if self.dev_a8:
                raise UnsupportedGles('gles_unmatched_irq_requires_retry_recovery',
                                      mask=mask, pending=self.dev_a8)
            if tid is not None:
                self.irq_waiters.add(tid)
            self.transactions.append({**event, 'returned': None, 'decoded': 'irq_wait', 'mask': mask})
            return None

        state = self.tgid_completions.get(tgid)
        if state is None:
            raise UnsupportedGles('gles_tgid_owner_not_open', tgid=tgid)
        if cmd == GLES_SIGNAL_COMPLETION:
            key = word
            target_tgid = key or tgid
            target = self.tgid_completions.get(target_tgid)
            if target is None:
                mem_write(arg, struct.pack('<I', 0x1fffffff))
                self.transactions.append({**event, 'returned': -14, 'decoded': 'missing_tgid',
                                          'key': key, 'target_tgid': target_tgid, 'woken_tids': []})
                return -14
            if target_tgid != tgid:
                raise UnsupportedGles('gles_signal_target_liveness_unverified', target_tgid=target_tgid)
            woken = []
            if target['done']:
                target['fast'] = True
            else:
                target['done'] = 0x7fffffff
                for waiter_tid, waiter in target['waiters'].items():
                    if not waiter['ready']:
                        waiter['ready'] = True
                        woken.append(waiter_tid)
            self.transactions.append({**event, 'returned': 0, 'decoded': 'signal_tgid',
                                      'key': key, 'target_tgid': target_tgid, 'woken_tids': woken})
            return 0

        waiter = state['waiters'].get(tid)
        if waiter is not None:
            if waiter['arg'] != arg:
                raise UnsupportedGles('gles_inflight_wait_argument_changed', tid=tid)
            if not waiter['ready']:
                self.transactions.append({**event, 'returned': None, 'decoded': 'wait_tgid'})
                return None
            if state['done'] == 0:
                raise UnsupportedGles('gles_completion_consumed_before_waiter', tid=tid)
            state['done'] -= 1
            del state['waiters'][tid]
        elif state['fast']:
            state['fast'] = False
        else:
            # The driver resets done only on entry, not when a parked syscall resumes.
            state['done'] = 0
            state['waiters'][tid] = {'arg': arg, 'ready': False}
            self.transactions.append({**event, 'returned': None, 'decoded': 'wait_tgid'})
            return None
        mem_write(arg, struct.pack('<I', 1))
        self.transactions.append({**event, 'returned': 0, 'decoded': 'wait_tgid', 'completed': 1})
        return 0

    def _ctx_identity(self, mem_read, arg, tid, owner, source):
        """Retain the historical, refuted user-descriptor experiment for old receipts.

        This is not the kernel lookup. The kernel-tgid policy never calls it.
        """
        task_ptr = None
        ctx_ptr = None
        try:
            raw = bytes(mem_read(arg + 0xC, 4))
            task_ptr = struct.unpack('<I', raw)[0]
            if task_ptr:
                raw = bytes(mem_read(task_ptr + 0x108, 4))
                ctx_ptr = struct.unpack('<I', raw)[0]
        except Exception:
            task_ptr = None
            ctx_ptr = None
        key = ctx_ptr if ctx_ptr else (owner.get('caller_tgid') or tid)
        return key, {'ctx_ptr': ctx_ptr, 'task_ptr': task_ptr, 'ctx_key': key,
                     'arg': arg, 'tid': tid, 'source': source,
                     'key_source': 'resolved_ctx' if ctx_ptr else 'tgid_fallback'}

    def _engine_descriptor_present(self, mem_read):
        if self.bar_virtual is None:
            return False
        try:
            descriptor = bytes(mem_read(self.bar_virtual + 0xF4, 4))
            completion = bytes(mem_read(self.bar_virtual + 0x16C, 4))
        except Exception:
            return False
        return (len(descriptor) >= 4 and descriptor[0] == 0x21
                and len(completion) >= 4 and any(completion))

    def _poll_engine_command(self, mem_read, mem_write):
        """Opt-in device-work model for the GPES command block.

        The guest writes BAR+0xE8=1/+0xEA=5 then a 0x80-byte descriptor
        (e0377). On real hardware the engine completes the command and raises
        the physically observed IRQ status class 0x40000000 (E0313), which the
        decoded IRQ ack path clears via BAR+0x1000/+0xC18. This model only
        exposes that device completion; it never completes a guest ctx.
        """
        if self.completion_policy in ('kernel-tgid', 'kernel-sugilite'):
            raise UnsupportedGles('gles_unqualified_engine_with_tgid_contract')
        if self.bar_virtual is None:
            return None
        try:
            header = bytes(mem_read(self.bar_virtual + 0xE8, 4))
            descriptor = bytes(mem_read(self.bar_virtual + 0xF4, 4))
            completion = bytes(mem_read(self.bar_virtual + 0x16C, 4))
        except Exception:
            return None
        # header_ok can only recur after a new guest write (we zero 0xE8 below);
        # the descriptor words are never cleared, so fire only when they changed.
        key = (descriptor, completion)
        header_ok = len(header) >= 4 and header[0] == 1 and header[2] == 5
        descriptor_ok = (len(descriptor) >= 4 and descriptor[0] == 0x21
                         and len(completion) >= 4 and any(completion)
                         and key != getattr(self, 'engine_last_key', None))
        if not (header_ok or descriptor_ok):
            return None
        self.engine_last_key = key
        self.engine_command_seen = True
        self.engine_submitted = True
        if getattr(self, 'engine_bar_completed', False):
            # complete_engine_command() already counted this submission.
            self.engine_bar_completed = False
        else:
            self.engine_completions += 1
            self.engine_state_events = 0
            self.engine_event_status |= 0x45000000
            self.dev_a8 |= 0x45000000
        for offset in (0x1000, 0xC18):
            try:
                mem_write(self.bar_virtual + offset, b'\x00\x00\x00\x00')
            except Exception:
                pass
        try:  # hardware consumes the ring/counter block; physical idle is 0
            mem_write(self.bar_virtual + 0xE8, b'\x00\x00\x00\x00')
        except Exception:
            pass
        self.transactions.append({'operation': 'engine-command', 'status': 0x40000000,
                                  'header': header.hex(), 'descriptor': descriptor.hex(),
                                  'completion_ptr': completion.hex()})
        return 0x40000000

    def complete_engine_command(self, source='bar-write'):
        """Device completion for a submitted command; never completes a ctx."""
        if self.completion_policy in ('kernel-tgid', 'kernel-sugilite'):
            raise UnsupportedGles('gles_unqualified_engine_with_tgid_contract')
        if getattr(self, 'engine_bar_completed', False):
            return None  # same submission (0x16C then 0x170); poll clears the flag
        self.engine_bar_completed = True
        self.engine_command_seen = True
        self.engine_submitted = True
        self.engine_completions += 1
        self.engine_state_events = 0
        self.engine_event_status |= 0x45000000
        self.dev_a8 |= 0x45000000
        self.transactions.append({'operation': 'engine-command', 'status': 0x40000000,
                                  'source': source})
        return 0x40000000

    def poll_p3d_bar(self, mem_read, mem_write, phys_reader=None, phys_writer=None):
        """Opt-in DMP P3D/PPF/PSC BAR poll using ``SugiliteMmioModel``.

        Reads ``+0x1800`` (P3D kick), ``+0xC18`` (PPF trigger), and ``+0x1C``
        (PSC ctrl) from ``bar_virtual``, runs the corresponding operation via
        ``SugiliteMmioModel``, updates ``BAR+0x34`` / ``dev_a8``, and clears the
        consumed trigger register so subsequent polls do not re-fire.
        """
        if not getattr(self, 'p3d_model', False) or self.bar_virtual is None:
            return 0
        if self.completion_policy in ('kernel-tgid', 'kernel-sugilite'):
            raise UnsupportedGles('gles_unqualified_engine_with_tgid_contract')
        from src.a6400.execution.dmp_p3d import (
            SUGI_REG_IRQ_STATUS,
            SUGI_REG_P3D_KICK,
            SUGI_REG_P3D_LIST_PA,
            SUGI_REG_PPF_COPY_SIZE,
            SUGI_REG_PPF_DIM0,
            SUGI_REG_PPF_DIM1,
            SUGI_REG_PPF_DST,
            SUGI_REG_PPF_FLAGS,
            SUGI_REG_PPF_SRC,
            SUGI_REG_PPF_TRIGGER,
            SUGI_REG_PSC_CTRL,
            SUGI_REG_PSC_END,
            SUGI_REG_PSC_START,
            SUGI_REG_PSC_VALUE,
            SugiliteMmioModel,
        )

        if self._p3d_mmio is None:
            self._p3d_mmio = SugiliteMmioModel(
                memory_reader=phys_reader,
                memory_writer=phys_writer,
            )
        else:
            if phys_reader is not None:
                self._p3d_mmio._memory_reader = phys_reader
            if phys_writer is not None:
                self._p3d_mmio._memory_writer = phys_writer

        def _rd32(off):
            raw = bytes(mem_read(self.bar_virtual + off, 4))
            return struct.unpack('<I', raw)[0] if len(raw) == 4 else 0

        status_added = 0
        p3d_kick = _rd32(SUGI_REG_P3D_KICK)
        if p3d_kick > 0:
            self._p3d_mmio.write_reg(SUGI_REG_P3D_LIST_PA, _rd32(SUGI_REG_P3D_LIST_PA))
            self._p3d_mmio.write_reg(SUGI_REG_P3D_KICK, p3d_kick)
            mem_write(self.bar_virtual + SUGI_REG_P3D_KICK, b'\x00\x00\x00\x00')
            status_added |= self._p3d_mmio.irq_status

        ppf_trig = _rd32(SUGI_REG_PPF_TRIGGER)
        if ppf_trig != 0:
            for reg_off in (
                SUGI_REG_PPF_SRC,
                SUGI_REG_PPF_DST,
                SUGI_REG_PPF_DIM0,
                SUGI_REG_PPF_DIM1,
                SUGI_REG_PPF_FLAGS,
                SUGI_REG_PPF_COPY_SIZE,
            ):
                self._p3d_mmio.write_reg(reg_off, _rd32(reg_off))
            self._p3d_mmio.write_reg(SUGI_REG_PPF_TRIGGER, ppf_trig)
            mem_write(self.bar_virtual + SUGI_REG_PPF_TRIGGER, b'\x00\x00\x00\x00')
            status_added |= self._p3d_mmio.irq_status

        psc_ctrl = _rd32(SUGI_REG_PSC_CTRL)
        if psc_ctrl & 1:
            for reg_off in (SUGI_REG_PSC_START, SUGI_REG_PSC_END, SUGI_REG_PSC_VALUE):
                self._p3d_mmio.write_reg(reg_off, _rd32(reg_off))
            self._p3d_mmio.write_reg(SUGI_REG_PSC_CTRL, psc_ctrl)
            mem_write(self.bar_virtual + SUGI_REG_PSC_CTRL, b'\x00\x00\x00\x00')
            status_added |= self._p3d_mmio.irq_status

        if status_added:
            self.dev_a8 |= status_added
            self.engine_event_status |= status_added
            mem_write(
                self.bar_virtual + SUGI_REG_IRQ_STATUS,
                struct.pack('<I', self._p3d_mmio.irq_status),
            )
            self.transactions.append({
                'operation': 'p3d-bar-poll',
                'status': status_added,
            })
        return status_added
