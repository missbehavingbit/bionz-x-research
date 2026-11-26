"""Authentic Graphics Resource Manager (GRM-MA) service contract.

Verified against Sony firmware modules /usr/kmod/grm_ma.ko, /usr/kmod/dmm.ko,
userland library /usr/lib/libObj.so, and Evidence records E0044 through E0055.

Pool policies
-------------
``pool_policy='gl-common-only'`` (default) keeps the single verified
``gl_common`` pool contract used by the earlier milestones: every open/lookup
reports physical base 0x202e0000, size 0x02000000 and kernel handle 0x68a96220,
and all allocations come from that one pool. This path is unchanged.

``pool_policy='captured-e0044'`` is an explicit opt-in policy built from the six
captured allocator objects of E0044. Lookup answers are taken from the captured
reciprocal list (first name match in captured list order, matching
``ma_list_findbyname`` traversal); ``ma_dmm_open_impl`` answers only unit 0x280
(``gl_common``), because E0046 shows the original open path contains no name
lookup. Unknown names, units and allocation handles are reported as explicit
failures instead of being mapped to ``gl_common``. Allocation offsets are kept
per captured pool, because the captured objects are separate allocators.

Telemetry policy
----------------
``telemetry_policy='off'`` (default) adds nothing: replies, events and framing
are byte-identical to the pool policy alone.

``telemetry_policy='e0302-grm'`` is an explicit opt-in observer. It records one
structured entry per synchronous open/findbyname/malloc/close request and per
asynchronous DMM request in ``self.telemetry``: opcode, transport, raw request
bytes, unit/name/handle/size, selected captured pool (base/size/kernel handle/
allocation offset), result, explicit error reason and provenance. Records are
also attached to the returned event as ``event['telemetry']`` and summarised by
``telemetry_report()``. Telemetry never changes a reply: an unsupported DMM
unit is recorded as unsupported (the existing legacy DMM reply is unchanged and
flagged with its provenance), and no pool is invented for it.
"""

import struct
from typing import Any, Dict, List, NamedTuple, Optional

# Verified queue ID for synchronous GRM-MA requests (ma_init)
GRM_MA_SERVICE_QUEUE = 0x014c0424
DMM_SERVICE_QUEUE = 0x0061003c

# Verified gl_common allocation parameters (E0044, E0045, E0047, E0050)
GRM_POOL_PHYS_BASE = 0x202e0000
GRM_POOL_SIZE = 0x02000000       # 32 MiB
GRM_KERNEL_HANDLE = 0x68a96220    # Captured gl_common kernel object pointer
GRM_DMM_HANDLE = 0x0417          # SearchUnitTypeToHandle(0x280) handle
MA_ERR_NONE = 0                  # MA_ErrNone (0)
# E0045 negative control: an absent name returns status -9 with zero base/size/
# handle and no retain-count write. The captured-e0044 policy reuses this
# negative status for unit/handle refusals that have no executed evidence.
MA_ERR_NOT_FOUND = -9

# E0046: the startup-shaped original open uses unit 0x280 (gl_common) only.
GRM_UNIT_GL_COMMON = 0x0280

POOL_POLICY_GL_COMMON_ONLY = 'gl-common-only'
POOL_POLICY_CAPTURED_E0044 = 'captured-e0044'
POOL_POLICY_CAPTURED_WBI_DMM = 'captured-wbi-dmm'
POOL_POLICIES = (
    POOL_POLICY_GL_COMMON_ONLY,
    POOL_POLICY_CAPTURED_E0044,
    POOL_POLICY_CAPTURED_WBI_DMM,
)

TELEMETRY_OFF = 'off'
TELEMETRY_E0302 = 'e0302-grm'
TELEMETRY_POLICIES = (TELEMETRY_OFF, TELEMETRY_E0302)

# Verified OSD A/B /dev/stream/display physical bases (E0074, E0297, E0298).
# They equal the two zero-named 'empty' allocator pools of the captured E0044
# list, so a request selecting either of those pools is an OSD-pool request.
OSD_POOL_PHYS_BASES = (0x23A80000, 0x23B16000)

# E0044 records each allocator object's physical address; embedded list links
# are cached kernel pointers equal to object physical + 0x60000000 + 0x14.
# The E0045 executed lookup returns gl_common's cached object pointer
# 0x68a96220, which agrees with 0x08a96220 + 0x60000000 and with E0044's
# cached link 0x68a96234. The same captured conversion is applied to the other
# captured objects to obtain their kernel handles.
CAPTURED_E0044_CACHED_OFFSET = 0x60000000


class CapturedPool(NamedTuple):
    """One E0044 allocator object as captured in the reciprocal list."""

    name: str
    object_physical: int
    phys_base: int
    pool_size: int

    @property
    def kernel_handle(self) -> int:
        return (self.object_physical + CAPTURED_E0044_CACHED_OFFSET) & 0xFFFFFFFF


# Captured E0044 reciprocal list, in traversal order from the candidate head's
# forward link. The two zero-named records are listed as E0044 labels them.
CAPTURED_E0044_POOLS = (
    CapturedPool('empty', 0x00B7F2A0, 0x23B16000, 0x96000),
    CapturedPool('mpMemoryHeap2', 0x08ABD560, 0x226E0000, 0x1A0000),
    CapturedPool('mpTextHeap', 0x08ABD5A0, 0x22880000, 0x200000),
    CapturedPool('mpMemoryHeap', 0x00B7F2E0, 0x222E0000, 0x400000),
    CapturedPool('empty', 0x00B7F360, 0x23A80000, 0x96000),
    CapturedPool('gl_common', 0x08A96220, 0x202E0000, 0x02000000),
)


# Verified in packaged WBI nflasha5 (SHA-256 19a702b0...) + dmm.ko unit table
# VA 0x5f1709e0 (rows 23, 28..32) + grm_ma.ko allocator objects (E0044/E0047):
#   idx 23: handle 0x0417, unit 0x00280 -> gl_common     (0x202E0000, 0x2000000, mspace=1)
#   idx 28: handle 0x041C, unit 0x4092E -> empty (OSD A) (0x23A80000, 0x0096000, mspace=0)
#   idx 29: handle 0x041D, unit 0x40803 -> mpMemoryHeap  (0x222E0000, 0x0400000, mspace=1)
#   idx 30: handle 0x041E, unit 0x4087B -> mpTextHeap    (0x22880000, 0x0200000, mspace=1)
#   idx 31: handle 0x041F, unit 0x00256 -> mpMemoryHeap2 (0x226E0000, 0x01A0000, mspace=1)
#   idx 32: handle 0x0420, unit 0x4092E -> empty (OSD B) (0x23B16000, 0x0096000, mspace=0)
class CapturedWbiDmmPool(NamedTuple):
    name: str
    object_physical: int
    phys_base: int
    pool_size: int
    dmm_unit: int
    dmm_handle: int
    has_mspace: bool

    @property
    def kernel_handle(self) -> int:
        return (self.object_physical + CAPTURED_E0044_CACHED_OFFSET) & 0xFFFFFFFF


CAPTURED_WBI_DMM_POOLS = (
    CapturedWbiDmmPool('gl_common', 0x08A96220, 0x202E0000, 0x02000000, 0x00280, 0x0417, True),
    CapturedWbiDmmPool('empty', 0x00B7F360, 0x23A80000, 0x00096000, 0x4092E, 0x041C, False),
    CapturedWbiDmmPool('mpMemoryHeap', 0x00B7F2E0, 0x222E0000, 0x00400000, 0x40803, 0x041D, True),
    CapturedWbiDmmPool('mpTextHeap', 0x08ABD5A0, 0x22880000, 0x00200000, 0x4087B, 0x041E, True),
    CapturedWbiDmmPool('mpMemoryHeap2', 0x08ABD560, 0x226E0000, 0x001A0000, 0x00256, 0x041F, True),
    CapturedWbiDmmPool('empty', 0x00B7F2A0, 0x23B16000, 0x00096000, 0x4092E, 0x0420, False),
)


def _captured_pool_by_name(name: str) -> Optional[CapturedPool]:
    """First captured list match, mirroring circular-list traversal order."""
    for pool in CAPTURED_E0044_POOLS:
        if pool.name == name:
            return pool
    return None


def _captured_pool_by_handle(handle: int) -> Optional[CapturedPool]:
    for pool in CAPTURED_E0044_POOLS:
        if pool.kernel_handle == handle:
            return pool
    return None


def _captured_pool_by_unit(unit: int) -> Optional[CapturedPool]:
    """Only unit 0x280 has executed open-path evidence (E0046)."""
    if unit != GRM_UNIT_GL_COMMON:
        return None
    return _captured_pool_by_name('gl_common')


def _captured_pool_by_base(phys_base: Optional[int]) -> Optional[CapturedPool]:
    """Unique captured-pool lookup by physical base (the 'empty' rows differ)."""
    for pool in CAPTURED_E0044_POOLS:
        if pool.phys_base == phys_base:
            return pool
    return None


def _hex_int(value: Any) -> Optional[int]:
    """Convert the service's hex-string address fields back to ints."""
    if isinstance(value, str):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return value


class GrmMaService:
    """Services synchronous UIPC requests to GRM-MA (queue 0x014c0424).

    Implements the authentic ma_user_sync_cb dispatch contract:
    - Opcode 1: ma_dmm_open_impl (unit 0x280 / gl_common)
    - Opcode 2: ma_dmm_close_impl
    - Opcode 7: ma_findbyname_impl (lookup gl_common)
    - Opcode 9: ma_malloc_impl (allocate buffer from gl_common pool)

    ``pool_policy`` selects the pool contract and ``telemetry_policy`` selects
    optional observation; see the module docstring. Defaults keep the
    ``gl-common-only`` behaviour and all reply offsets byte-identical.
    """

    def __init__(self,
                 phys_base: int = GRM_POOL_PHYS_BASE,
                 pool_size: int = GRM_POOL_SIZE,
                 kernel_handle: int = GRM_KERNEL_HANDLE,
                 pool_policy: str = POOL_POLICY_GL_COMMON_ONLY,
                 telemetry_policy: str = TELEMETRY_OFF):
        if pool_policy not in POOL_POLICIES:
            raise ValueError('Unknown GRM pool policy')
        if telemetry_policy not in TELEMETRY_POLICIES:
            raise ValueError('Unknown GRM telemetry policy')
        if pool_policy in (POOL_POLICY_CAPTURED_E0044, POOL_POLICY_CAPTURED_WBI_DMM) and (
                phys_base, pool_size, kernel_handle) != (
                    GRM_POOL_PHYS_BASE, GRM_POOL_SIZE, GRM_KERNEL_HANDLE):
            raise ValueError(
                f'{pool_policy} policy uses the captured pool table; '
                'custom gl_common parameters are not supported')
        self.phys_base = phys_base
        self.pool_size = pool_size
        self.kernel_handle = kernel_handle
        self.pool_policy = pool_policy
        self.telemetry_policy = telemetry_policy
        self.next_alloc_offset = 0
        # Captured-e0044 allocation offsets, keyed by captured object physical.
        self.pool_alloc_offsets: Dict[int, int] = {}
        # Captured-wbi-dmm state: open DMM units, whole-unit claims, mspace free blocks, dynamic msps.
        self.open_wbi_objects: set = set()
        self.claimed_whole_units: set = set()
        self.mspace_allocations: Dict[Tuple[int, int], int] = {}
        self.mspace_free_blocks: Dict[int, List[Tuple[int, int]]] = {}
        self.dynamic_wbi_pools: List[CapturedWbiDmmPool] = []
        # Opt-in e0302-grm request observer; empty unless telemetry is enabled.
        self.telemetry: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []

    def __setstate__(self, state):
        # Checkpoints created before the telemetry policy existed must restore
        # without changing their reply behavior.
        self.__dict__.update(state)
        if 'telemetry_policy' not in self.__dict__:
            self.telemetry_policy = TELEMETRY_OFF
        if 'telemetry' not in self.__dict__:
            self.telemetry = []
        if 'open_wbi_objects' not in self.__dict__:
            self.open_wbi_objects = set()
        if 'claimed_whole_units' not in self.__dict__:
            self.claimed_whole_units = set()
        if 'mspace_allocations' not in self.__dict__:
            self.mspace_allocations = {}
        if 'mspace_free_blocks' not in self.__dict__:
            self.mspace_free_blocks = {}
        if 'dynamic_wbi_pools' not in self.__dict__:
            self.dynamic_wbi_pools = []

    def upgrade_to_wbi_dmm(self) -> None:
        """Promote a pre-OSD captured-e0044 instance on checkpoint resume."""
        if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
            return
        if any(ev.get('status', 0) != 0 for ev in self.events):
            raise ValueError('Cannot upgrade GRM policy after an error reply was already delivered')
        self.pool_policy = POOL_POLICY_CAPTURED_WBI_DMM

    def _wbi_pools(self) -> Tuple[CapturedWbiDmmPool, ...]:
        return tuple(self.dynamic_wbi_pools) + CAPTURED_WBI_DMM_POOLS

    def _wbi_pool_by_handle(self, handle: int) -> Optional[CapturedWbiDmmPool]:
        for pool in self._wbi_pools():
            if pool.kernel_handle == handle:
                return pool
        return None

    def _wbi_pool_by_name(self, name: str) -> Optional[CapturedWbiDmmPool]:
        # Follow E0044 reciprocal-list traversal order for static pools after dynamic head entries.
        for pool in self.dynamic_wbi_pools:
            if pool.name == name:
                return pool
        for e0044 in CAPTURED_E0044_POOLS:
            if e0044.name == name:
                for pool in CAPTURED_WBI_DMM_POOLS:
                    if pool.object_physical == e0044.object_physical:
                        return pool
        return None

    def _wbi_pool_by_phys(self, phys_base: int) -> Optional[CapturedWbiDmmPool]:
        for pool in self._wbi_pools():
            if pool.phys_base == phys_base:
                return pool
        return None

    def _wbi_open_unit(self, unit: int) -> Optional[CapturedWbiDmmPool]:
        matches = [p for p in CAPTURED_WBI_DMM_POOLS if p.dmm_unit == unit]
        if not matches:
            return None
        for pool in matches:
            if pool.object_physical not in self.open_wbi_objects:
                self.open_wbi_objects.add(pool.object_physical)
                return pool
        # Single-instance mspace units (e.g. 0x0280 gl_common) can be retained repeatedly.
        if len(matches) == 1 and matches[0].has_mspace:
            return matches[0]
        return None

    def handle_uipc_sync_message(self, memory_reader, memory_writer, msg_ptr: int, alloc_size: int = 64) -> int:
        """Dispatches an incoming synchronous UIPC message to GRM-MA."""
        raw_msg = memory_reader(msg_ptr, min(alloc_size, 64))
        if len(raw_msg) < 4:
            return -1

        opcode = struct.unpack_from('<H', raw_msg, 0)[0]
        event: Dict[str, Any] = {'msg_ptr': msg_ptr, 'opcode': opcode}

        if opcode == 1:
            # Opcode 1: ma_dmm_open_impl(param_1 + 2, param_1 + 0xe)
            # param_1 + 2 (byte 4): unit (uint32)
            # param_1 + 4 (byte 8): name string (up to 16 bytes)
            # Reply written to param_1 + 0xe (byte 28):
            #   offset +28: status (0 = MA_ErrNone)
            #   offset +32: phys_base (0x202e0000)
            #   offset +36: size (32 MiB)
            #   offset +40: kernel_handle (0x68a96220)
            unit = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            # (int)param_1 + 5 in ma_dmm_open_impl points to byte offset 9
            if len(raw_msg) > 9 and raw_msg[9] != 0:
                raw_name = raw_msg[9:25].split(b'\x00')[0]
            else:
                raw_name = raw_msg[8:24].split(b'\x00')[0] if len(raw_msg) >= 24 else b''
            name_str = raw_name.decode('latin1', errors='ignore')

            if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
                wbi_pool = self._wbi_open_unit(unit)
                if wbi_pool is None:
                    if memory_writer:
                        memory_writer(msg_ptr + 28,
                                      struct.pack('<iIII', MA_ERR_NOT_FOUND, 0, 0, 0))
                    event.update({
                        'action': 'ma_dmm_open_impl_unsupported_unit',
                        'unit': f'0x{unit:04x}',
                        'name': name_str,
                        'status': MA_ERR_NOT_FOUND,
                        'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                    })
                else:
                    reply = struct.pack('<IIII', MA_ERR_NONE, wbi_pool.phys_base,
                                        wbi_pool.pool_size, wbi_pool.kernel_handle)
                    if memory_writer:
                        memory_writer(msg_ptr + 28, reply)
                    event.update({
                        'action': 'ma_dmm_open_impl',
                        'unit': f'0x{unit:04x}',
                        'name': name_str or wbi_pool.name,
                        'status': MA_ERR_NONE,
                        'phys_base': f'0x{wbi_pool.phys_base:08x}',
                        'pool_size': wbi_pool.pool_size,
                        'handle': f'0x{wbi_pool.kernel_handle:08x}',
                        'dmm_handle': f'0x{wbi_pool.dmm_handle:04x}',
                        'pool_name': wbi_pool.name,
                        'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                    })
            elif self.pool_policy == POOL_POLICY_CAPTURED_E0044:
                # E0046: the original open contains no name lookup; only unit
                # 0x280 has executed evidence. Do not fall back to gl_common
                # for any other unit.
                pool = _captured_pool_by_unit(unit)
                if pool is None:
                    if memory_writer:
                        memory_writer(msg_ptr + 28,
                                      struct.pack('<iIII', MA_ERR_NOT_FOUND, 0, 0, 0))
                    event.update({
                        'action': 'ma_dmm_open_impl_unsupported_unit',
                        'unit': f'0x{unit:04x}',
                        'name': name_str,
                        'status': MA_ERR_NOT_FOUND,
                        'pool_policy': POOL_POLICY_CAPTURED_E0044
                    })
                else:
                    reply = struct.pack('<IIII', MA_ERR_NONE, pool.phys_base,
                                        pool.pool_size, pool.kernel_handle)
                    if memory_writer:
                        memory_writer(msg_ptr + 28, reply)

                    event.update({
                        'action': 'ma_dmm_open_impl',
                        'unit': f'0x{unit:04x}',
                        'name': name_str,
                        'status': MA_ERR_NONE,
                        'phys_base': f'0x{pool.phys_base:08x}',
                        'pool_size': pool.pool_size,
                        'handle': f'0x{pool.kernel_handle:08x}',
                        'pool_name': pool.name,
                        'pool_policy': POOL_POLICY_CAPTURED_E0044
                    })
            else:
                reply = struct.pack('<IIII', MA_ERR_NONE, self.phys_base, self.pool_size, self.kernel_handle)
                if memory_writer:
                    memory_writer(msg_ptr + 28, reply)

                event.update({
                    'action': 'ma_dmm_open_impl',
                    'unit': f'0x{unit:04x}',
                    'name': name_str,
                    'status': MA_ERR_NONE,
                    'phys_base': f'0x{self.phys_base:08x}',
                    'pool_size': self.pool_size,
                    'handle': f'0x{self.kernel_handle:08x}'
                })

        elif opcode == 7:
            # Opcode 7: ma_findbyname_impl(param_1 + 2, param_1 + 10)
            # param_1 + 2 (byte 4): name string (16 bytes)
            # Reply written to param_1 + 10 (byte 20):
            #   offset +20: status (0 = MA_ErrNone)
            #   offset +24: phys_base
            #   offset +28: size
            #   offset +32: kernel_handle
            raw_name = raw_msg[4:20].split(b'\x00')[0] if len(raw_msg) >= 20 else b''
            name_str = raw_name.decode('latin1', errors='ignore')

            if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
                wbi_pool = self._wbi_pool_by_name(name_str)
                if wbi_pool is None:
                    if memory_writer:
                        memory_writer(msg_ptr + 20,
                                      struct.pack('<iIII', MA_ERR_NOT_FOUND, 0, 0, 0))
                    event.update({
                        'action': 'ma_findbyname_impl_unknown_name',
                        'name': name_str,
                        'status': MA_ERR_NOT_FOUND,
                        'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                    })
                else:
                    reply = struct.pack('<IIII', MA_ERR_NONE, wbi_pool.phys_base,
                                        wbi_pool.pool_size, wbi_pool.kernel_handle)
                    if memory_writer:
                        memory_writer(msg_ptr + 20, reply)
                    event.update({
                        'action': 'ma_findbyname_impl',
                        'name': name_str,
                        'status': MA_ERR_NONE,
                        'phys_base': f'0x{wbi_pool.phys_base:08x}',
                        'pool_size': wbi_pool.pool_size,
                        'handle': f'0x{wbi_pool.kernel_handle:08x}',
                        'pool_name': wbi_pool.name,
                        'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                    })
            elif self.pool_policy == POOL_POLICY_CAPTURED_E0044:
                pool = _captured_pool_by_name(name_str)
                if pool is None:
                    # E0045 negative control: absent name -> status -9, empty
                    # base/size/handle. Never substitute the gl_common row.
                    if memory_writer:
                        memory_writer(msg_ptr + 20,
                                      struct.pack('<iIII', MA_ERR_NOT_FOUND, 0, 0, 0))
                    event.update({
                        'action': 'ma_findbyname_impl_unknown_name',
                        'name': name_str,
                        'status': MA_ERR_NOT_FOUND,
                        'pool_policy': POOL_POLICY_CAPTURED_E0044
                    })
                else:
                    reply = struct.pack('<IIII', MA_ERR_NONE, pool.phys_base,
                                        pool.pool_size, pool.kernel_handle)
                    if memory_writer:
                        memory_writer(msg_ptr + 20, reply)

                    event.update({
                        'action': 'ma_findbyname_impl',
                        'name': name_str,
                        'status': MA_ERR_NONE,
                        'phys_base': f'0x{pool.phys_base:08x}',
                        'pool_size': pool.pool_size,
                        'handle': f'0x{pool.kernel_handle:08x}',
                        'pool_name': pool.name,
                        'pool_policy': POOL_POLICY_CAPTURED_E0044
                    })
            else:
                reply = struct.pack('<IIII', MA_ERR_NONE, self.phys_base, self.pool_size, self.kernel_handle)
                if memory_writer:
                    memory_writer(msg_ptr + 20, reply)

                event.update({
                    'action': 'ma_findbyname_impl',
                    'name': name_str,
                    'status': MA_ERR_NONE,
                    'phys_base': f'0x{self.phys_base:08x}',
                    'pool_size': self.pool_size,
                    'handle': f'0x{self.kernel_handle:08x}'
                })

        elif opcode == 9:
            # Opcode 9: ma_malloc_impl(param_1 + 2, param_1 + 8)
            # param_1 + 2 (byte 4): handle
            # param_1 + 4 (byte 8): alloc size
            # Reply written to param_1 + 8 (byte 16):
            #   offset +16: status (0 = MA_ErrNone)
            #   offset +20: allocated address (phys_base + offset)
            handle = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            size = struct.unpack_from('<I', raw_msg, 8)[0] if len(raw_msg) >= 12 else 0

            if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
                req_align = struct.unpack_from('<I', raw_msg, 12)[0] if len(raw_msg) >= 16 else 0
                wbi_pool = self._wbi_pool_by_handle(handle)
                if wbi_pool is None:
                    if memory_writer:
                        memory_writer(msg_ptr + 16,
                                      struct.pack('<iI', MA_ERR_NOT_FOUND, 0))
                    event.update({
                        'action': 'ma_malloc_impl_unknown_handle',
                        'handle': f'0x{handle:08x}',
                        'req_size': size,
                        'status': MA_ERR_NOT_FOUND,
                        'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                    })
                elif not wbi_pool.has_mspace:
                    # grm_ma.ko + 0x834..0x874: flag44 == 0 claims the whole DMM unit once.
                    if (wbi_pool.object_physical in self.claimed_whole_units
                            or size > wbi_pool.pool_size):
                        if memory_writer:
                            memory_writer(msg_ptr + 16, struct.pack('<iI', -1, 0))
                        event.update({
                            'action': 'ma_malloc_impl_whole_unit_busy_or_oversize',
                            'handle': f'0x{handle:08x}',
                            'req_size': size,
                            'status': -1,
                            'pool_name': wbi_pool.name,
                            'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                        })
                    else:
                        self.claimed_whole_units.add(wbi_pool.object_physical)
                        alloc_addr = wbi_pool.phys_base
                        if memory_writer:
                            memory_writer(msg_ptr + 16, struct.pack('<II', MA_ERR_NONE, alloc_addr))
                        event.update({
                            'action': 'ma_malloc_impl',
                            'handle': f'0x{handle:08x}',
                            'req_size': size,
                            'alloc_addr': f'0x{alloc_addr:08x}',
                            'status': MA_ERR_NONE,
                            'pool_name': wbi_pool.name,
                            'pool_offset': 0,
                            'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                        })
                else:
                    align = 512
                    if req_align > align and (req_align & (req_align - 1)) == 0:
                        align = req_align
                    aligned_size = max(align, (size + align - 1) & ~(align - 1))
                    free_list = self.mspace_free_blocks.setdefault(wbi_pool.object_physical, [])
                    reused_idx = next(
                        (idx for idx, (f_off, f_sz) in enumerate(free_list)
                         if f_sz >= aligned_size and ((wbi_pool.phys_base + f_off) & (align - 1)) == 0),
                        None,
                    )
                    if reused_idx is not None:
                        f_off, f_sz = free_list.pop(reused_idx)
                        offset = f_off
                        if f_sz > aligned_size:
                            free_list.append((f_off + aligned_size, f_sz - aligned_size))
                    else:
                        raw_off = self.pool_alloc_offsets.get(wbi_pool.object_physical, 0)
                        aligned_phys = (wbi_pool.phys_base + raw_off + align - 1) & ~(align - 1)
                        offset = aligned_phys - wbi_pool.phys_base
                        if offset + aligned_size > wbi_pool.pool_size:
                            if memory_writer:
                                memory_writer(msg_ptr + 16, struct.pack('<iI', -1, 0))
                            event.update({
                                'action': 'ma_malloc_impl_out_of_memory',
                                'handle': f'0x{handle:08x}',
                                'req_size': size,
                                'status': -1,
                                'pool_name': wbi_pool.name,
                                'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                            })
                            if self.telemetry_policy != TELEMETRY_OFF:
                                event['telemetry'] = self._record_sync_telemetry(opcode, raw_msg, event)
                            self.events.append(event)
                            return 0
                        self.pool_alloc_offsets[wbi_pool.object_physical] = offset + aligned_size
                    alloc_addr = wbi_pool.phys_base + offset
                    self.mspace_allocations[(wbi_pool.object_physical, alloc_addr)] = aligned_size
                    if memory_writer:
                        memory_writer(msg_ptr + 16, struct.pack('<II', MA_ERR_NONE, alloc_addr))
                    event.update({
                        'action': 'ma_malloc_impl',
                        'handle': f'0x{handle:08x}',
                        'req_size': size,
                        'alloc_addr': f'0x{alloc_addr:08x}',
                        'status': MA_ERR_NONE,
                        'pool_name': wbi_pool.name,
                        'pool_offset': offset,
                        'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                    })
            elif self.pool_policy == POOL_POLICY_CAPTURED_E0044:
                pool = _captured_pool_by_handle(handle)
                if pool is None:
                    if memory_writer:
                        memory_writer(msg_ptr + 16,
                                      struct.pack('<iI', MA_ERR_NOT_FOUND, 0))
                    event.update({
                        'action': 'ma_malloc_impl_unknown_handle',
                        'handle': f'0x{handle:08x}',
                        'req_size': size,
                        'status': MA_ERR_NOT_FOUND,
                        'pool_policy': POOL_POLICY_CAPTURED_E0044
                    })
                else:
                    # The captured objects are separate allocators, so each
                    # pool tracks its own allocation offset; a single global
                    # offset would move requests outside their declared pool.
                    offset = self.pool_alloc_offsets.get(pool.object_physical, 0)
                    alloc_addr = pool.phys_base + offset
                    # Align next allocation to 4096-byte page boundary
                    aligned_size = (size + 4095) & ~4095
                    self.pool_alloc_offsets[pool.object_physical] = offset + aligned_size

                    reply = struct.pack('<II', MA_ERR_NONE, alloc_addr)
                    if memory_writer:
                        memory_writer(msg_ptr + 16, reply)

                    event.update({
                        'action': 'ma_malloc_impl',
                        'handle': f'0x{handle:08x}',
                        'req_size': size,
                        'alloc_addr': f'0x{alloc_addr:08x}',
                        'status': MA_ERR_NONE,
                        'pool_name': pool.name,
                        'pool_offset': offset,
                        'pool_policy': POOL_POLICY_CAPTURED_E0044
                    })
            else:
                alloc_addr = self.phys_base + self.next_alloc_offset
                # Align next allocation to 4096-byte page boundary
                aligned_size = (size + 4095) & ~4095
                self.next_alloc_offset += aligned_size

                reply = struct.pack('<II', MA_ERR_NONE, alloc_addr)
                if memory_writer:
                    memory_writer(msg_ptr + 16, reply)

                event.update({
                    'action': 'ma_malloc_impl',
                    'handle': f'0x{handle:08x}',
                    'req_size': size,
                    'alloc_addr': f'0x{alloc_addr:08x}',
                    'status': MA_ERR_NONE
                })

        elif opcode == 2:
            # Opcode 2: ma_dmm_close_impl(param_1 + 2, param_1 + 4)
            # Reply written to param_1 + 4 (byte 8):
            #   offset +8: status (0 = MA_ErrNone)
            handle = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM and handle:
                wbi_pool = self._wbi_pool_by_handle(handle)
                if wbi_pool is not None:
                    self.open_wbi_objects.discard(wbi_pool.object_physical)
                    self.claimed_whole_units.discard(wbi_pool.object_physical)
            if memory_writer:
                memory_writer(msg_ptr + 8, struct.pack('<I', MA_ERR_NONE))

            event.update({
                'action': 'ma_dmm_close_impl',
                'status': MA_ERR_NONE
            })

        elif opcode == 3 and self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
            # Opcode 3: ma_create_msp_impl(msg + 4, msg + 32)
            pbase, psize = struct.unpack_from('<II', raw_msg, 4) if len(raw_msg) >= 12 else (0, 0)
            has_msp = bool(raw_msg[12]) if len(raw_msg) > 12 else True
            raw_name = raw_msg[13:29].split(b'\x00')[0] if len(raw_msg) >= 14 else b''
            name_str = raw_name.decode('latin1', errors='ignore')
            existing = self._wbi_pool_by_phys(pbase)
            if existing is None:
                dyn_obj = 0x08AC0000 + len(self.dynamic_wbi_pools) * 0x40
                existing = CapturedWbiDmmPool(name_str, dyn_obj, pbase, psize, 0xFFFF, 0xFFFF, has_msp)
                self.dynamic_wbi_pools.insert(0, existing)
            if memory_writer:
                memory_writer(msg_ptr + 32, struct.pack('<II', MA_ERR_NONE, existing.kernel_handle))
            event.update({
                'action': 'ma_create_msp_impl',
                'name': name_str,
                'phys_base': f'0x{pbase:08x}',
                'pool_size': psize,
                'handle': f'0x{existing.kernel_handle:08x}',
                'status': MA_ERR_NONE,
                'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
            })

        elif opcode in (5, 6) and self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
            # Opcode 5/6: ma_cache_flush_impl / ma_cache_invalidate_impl (coherent host RAM)
            count = struct.unpack_from('<I', raw_msg, 8)[0] if len(raw_msg) >= 12 else 0
            event.update({
                'action': 'ma_cache_flush_impl' if opcode == 5 else 'ma_cache_invalidate_impl',
                'count': count,
                'status': MA_ERR_NONE,
                'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
            })

        elif opcode == 8 and self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
            # Opcode 8: ma_findbyphys_impl(msg + 4, msg + 8)
            phys = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            wbi_pool = self._wbi_pool_by_phys(phys)
            if wbi_pool is None:
                if memory_writer:
                    memory_writer(msg_ptr + 8, struct.pack('<iII16sI', MA_ERR_NOT_FOUND, 0, 0, b'\x00' * 16, 0))
                event.update({
                    'action': 'ma_findbyphys_impl_not_found',
                    'phys_base': f'0x{phys:08x}',
                    'status': MA_ERR_NOT_FOUND,
                    'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                })
            else:
                padded_name = wbi_pool.name.encode('latin1', errors='ignore')[:15].ljust(16, b'\x00')
                if memory_writer:
                    memory_writer(
                        msg_ptr + 8,
                        struct.pack('<III16sI', MA_ERR_NONE, wbi_pool.phys_base,
                                    wbi_pool.pool_size, padded_name, wbi_pool.kernel_handle),
                    )
                event.update({
                    'action': 'ma_findbyphys_impl',
                    'phys_base': f'0x{wbi_pool.phys_base:08x}',
                    'pool_size': wbi_pool.pool_size,
                    'handle': f'0x{wbi_pool.kernel_handle:08x}',
                    'pool_name': wbi_pool.name,
                    'status': MA_ERR_NONE,
                    'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                })

        elif opcode == 10 and self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
            # Opcode 10: ma_free_impl(msg + 4, msg + 16)
            handle = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            alloc_addr = struct.unpack_from('<I', raw_msg, 8)[0] if len(raw_msg) >= 12 else 0
            wbi_pool = self._wbi_pool_by_handle(handle)
            if wbi_pool is None:
                if memory_writer:
                    memory_writer(msg_ptr + 16, struct.pack('<i', MA_ERR_NOT_FOUND))
                event.update({
                    'action': 'ma_free_impl_unknown_handle',
                    'handle': f'0x{handle:08x}',
                    'status': MA_ERR_NOT_FOUND,
                    'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                })
            else:
                if not wbi_pool.has_mspace:
                    self.claimed_whole_units.discard(wbi_pool.object_physical)
                else:
                    sz = self.mspace_allocations.pop((wbi_pool.object_physical, alloc_addr), None)
                    if sz is not None and alloc_addr >= wbi_pool.phys_base:
                        self.mspace_free_blocks.setdefault(wbi_pool.object_physical, []).append(
                            (alloc_addr - wbi_pool.phys_base, sz)
                        )
                if memory_writer:
                    memory_writer(msg_ptr + 16, struct.pack('<I', MA_ERR_NONE))
                event.update({
                    'action': 'ma_free_impl',
                    'handle': f'0x{handle:08x}',
                    'alloc_addr': f'0x{alloc_addr:08x}',
                    'pool_name': wbi_pool.name,
                    'status': MA_ERR_NONE,
                    'pool_policy': POOL_POLICY_CAPTURED_WBI_DMM,
                })

        else:
            # Unsupported opcode
            event.update({
                'action': 'unsupported_opcode',
                'status': -1
            })

        if self.telemetry_policy != TELEMETRY_OFF:
            event['telemetry'] = self._record_sync_telemetry(opcode, raw_msg, event)
        self.events.append(event)
        return 0

    def _record_sync_telemetry(self, opcode: int, raw_msg: bytes,
                               event: Dict[str, Any]) -> Dict[str, Any]:
        """Record one synchronous request; never modifies the reply."""
        request: Dict[str, Any] = {'unit': None, 'name': None, 'handle': None, 'size': None}
        if opcode == 1:
            request['unit'] = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            if len(raw_msg) > 9 and raw_msg[9] != 0:
                raw_name = raw_msg[9:25].split(b'\x00')[0]
            else:
                raw_name = raw_msg[8:24].split(b'\x00')[0] if len(raw_msg) >= 24 else b''
            request['name'] = raw_name.decode('latin1', errors='ignore')
        elif opcode == 7:
            raw_name = raw_msg[4:20].split(b'\x00')[0] if len(raw_msg) >= 20 else b''
            request['name'] = raw_name.decode('latin1', errors='ignore')
        elif opcode == 9:
            request['handle'] = struct.unpack_from('<I', raw_msg, 4)[0] if len(raw_msg) >= 8 else 0
            request['size'] = struct.unpack_from('<I', raw_msg, 8)[0] if len(raw_msg) >= 12 else 0

        selection: Dict[str, Any] = {'pool_name': None, 'phys_base': None, 'pool_size': None,
                                     'handle': None, 'alloc_offset': None, 'alloc_addr': None}
        action = event.get('action')
        result, error_reason = 'served', None
        if action == 'ma_dmm_open_impl':
            selection.update(pool_name=event.get('pool_name') or 'gl_common',
                             phys_base=_hex_int(event.get('phys_base')),
                             pool_size=event.get('pool_size'),
                             handle=_hex_int(event.get('handle')))
            provenance = ('captured-wbi-dmm-unit-table'
                          if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM
                          else 'captured-e0044-unit-0x0280'
                          if self.pool_policy == POOL_POLICY_CAPTURED_E0044
                          else 'default-gl-common-contract')
        elif action == 'ma_findbyname_impl':
            selection.update(pool_name=event.get('pool_name') or 'gl_common',
                             phys_base=_hex_int(event.get('phys_base')),
                             pool_size=event.get('pool_size'),
                             handle=_hex_int(event.get('handle')))
            provenance = ('captured-wbi-dmm-name-table'
                          if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM
                          else 'captured-e0044-name-table-first-match'
                          if self.pool_policy == POOL_POLICY_CAPTURED_E0044
                          else 'default-gl-common-contract')
        elif action == 'ma_findbyphys_impl':
            selection.update(pool_name=event.get('pool_name'),
                             phys_base=_hex_int(event.get('phys_base')),
                             pool_size=event.get('pool_size'),
                             handle=_hex_int(event.get('handle')))
            provenance = 'captured-wbi-dmm-phys-table'
        elif action == 'ma_malloc_impl':
            alloc_addr = _hex_int(event.get('alloc_addr'))
            if self.pool_policy in (POOL_POLICY_CAPTURED_E0044, POOL_POLICY_CAPTURED_WBI_DMM):
                offset = event.get('pool_offset')
                phys_base = (alloc_addr - offset
                             if alloc_addr is not None and offset is not None else None)
                pool = _captured_pool_by_base(phys_base)
                selection.update(pool_name=event.get('pool_name'), phys_base=phys_base,
                                 pool_size=pool.pool_size if pool else None,
                                 handle=pool.kernel_handle if pool else None,
                                 alloc_offset=offset, alloc_addr=alloc_addr)
                provenance = ('captured-wbi-dmm-per-pool-allocator'
                              if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM
                              else 'captured-e0044-per-pool-allocator')
            else:
                offset = (alloc_addr - self.phys_base if alloc_addr is not None else None)
                selection.update(pool_name='gl_common', phys_base=self.phys_base,
                                 pool_size=self.pool_size, handle=self.kernel_handle,
                                 alloc_offset=offset, alloc_addr=alloc_addr)
                provenance = 'default-gl-common-allocator'
        elif action in ('ma_create_msp_impl', 'ma_cache_flush_impl',
                        'ma_cache_invalidate_impl', 'ma_free_impl'):
            selection.update(pool_name=event.get('pool_name'),
                             phys_base=_hex_int(event.get('phys_base')),
                             handle=_hex_int(event.get('handle')),
                             alloc_addr=_hex_int(event.get('alloc_addr')))
            provenance = 'captured-wbi-dmm-contract'
        elif action == 'ma_dmm_open_impl_unsupported_unit':
            result = 'error'
            error_reason = ('unit_not_mapped_by_captured_e0044_policy'
                            if self.pool_policy == POOL_POLICY_CAPTURED_E0044
                            else action)
            provenance = f'{self.pool_policy}-policy-refusal'
        elif action == 'ma_findbyname_impl_unknown_name':
            result = 'error'
            error_reason = ('name_not_in_captured_e0044_table'
                            if self.pool_policy == POOL_POLICY_CAPTURED_E0044
                            else action)
            provenance = f'{self.pool_policy}-policy-refusal'
        elif action == 'ma_malloc_impl_unknown_handle':
            result = 'error'
            error_reason = ('handle_not_in_captured_e0044_table'
                            if self.pool_policy == POOL_POLICY_CAPTURED_E0044
                            else action)
            provenance = f'{self.pool_policy}-policy-refusal'
        elif action in ('ma_dmm_open_impl_exhausted_unit', 'ma_findbyphys_impl_not_found',
                        'ma_malloc_impl_whole_unit_busy', 'ma_malloc_impl_out_of_memory',
                        'ma_free_impl_unknown_handle'):
            result, error_reason = 'error', action
            provenance = f'{self.pool_policy}-policy-refusal'
        elif action == 'ma_dmm_close_impl':
            provenance = 'close-acknowledgement; no pool selected'
        else:
            result, error_reason = 'error', 'unsupported_opcode'
            provenance = 'dispatch-refusal'
        osd_pool = (selection['pool_name'] == 'empty'
                    or selection['phys_base'] in OSD_POOL_PHYS_BASES)
        record = {
            'seq': len(self.telemetry),
            'transport': 'sync',
            'opcode': opcode,
            'msg_ptr': event.get('msg_ptr'),
            'action': action,
            'request_hex': bytes(raw_msg[:24]).hex(),
            'request': request,
            'pool_policy': self.pool_policy,
            'selection': selection,
            'result': result,
            'error': error_reason,
            'status': event.get('status'),
            'unsupported': result == 'error' and action != 'unsupported_opcode',
            'osd_pool': osd_pool,
            'provenance': provenance,
        }
        self.telemetry.append(record)
        return record

    def telemetry_report(self) -> Dict[str, Any]:
        """Summary of the opt-in telemetry records for the Lead's runner."""
        by_action: Dict[str, int] = {}
        by_result: Dict[str, int] = {}
        pools = set()
        for record in self.telemetry:
            by_action[record['action']] = by_action.get(record['action'], 0) + 1
            by_result[record['result']] = by_result.get(record['result'], 0) + 1
            name = (record.get('selection') or {}).get('pool_name')
            if name:
                pools.add(name)
        unsupported = [record for record in self.telemetry
                       if record.get('unsupported') or record.get('result') == 'error']
        return {
            'telemetry_policy': self.telemetry_policy,
            'pool_policy': self.pool_policy,
            'request_count': len(self.telemetry),
            'by_action': by_action,
            'by_result': by_result,
            'pools_selected': sorted(pools),
            'unsupported_request_count': len(unsupported),
            'unsupported_requests': unsupported,
            'osd_pool_requests': [record for record in self.telemetry if record.get('osd_pool')],
            'requests': self.telemetry,
        }

    def _record_dmm_telemetry(self, event: Dict[str, Any], raw_req: bytes,
                              msg_ptr: int) -> Dict[str, Any]:
        """Record one asynchronous DMM request; never modifies the reply."""
        unit = _hex_int(event.get('unit'))
        selection: Dict[str, Any] = {'pool_name': None, 'phys_base': None,
                                     'pool_size': None, 'handle': None}
        result, error_reason = 'served', None
        unsupported = False
        if self.pool_policy == POOL_POLICY_CAPTURED_E0044:
            pool = _captured_pool_by_unit(unit)
            if pool is None:
                # The legacy DMM completion reply is unit-independent; it is
                # not evidence that this unit selected gl_common. Keep the
                # verified wire behaviour and expose the missing mapping.
                result = 'served_legacy_unit_independent'
                error_reason = 'dmm_unit_not_mapped_by_captured_e0044_policy'
                unsupported = True
                provenance = ('legacy-dmm-unit-independent-gl_common reply; '
                              'captured-e0044 has no DMM unit table')
            else:
                selection.update(pool_name=pool.name, phys_base=pool.phys_base,
                                 pool_size=pool.pool_size, handle=GRM_DMM_HANDLE)
                provenance = 'captured-e0044-unit-0x0280-dmm-handle'
        else:
            selection.update(pool_name='gl_common', phys_base=self.phys_base,
                             pool_size=self.pool_size, handle=GRM_DMM_HANDLE)
            provenance = 'default-gl-common-dmm-contract'
        osd_pool = (selection['pool_name'] == 'empty'
                    or selection['phys_base'] in OSD_POOL_PHYS_BASES)
        record = {
            'seq': len(self.telemetry),
            'transport': 'dmm',
            'opcode': event.get('opcode'),
            'action': 'dmm_ctrl_AllocUnitNewHdlr',
            'msg_ptr': msg_ptr,
            'queue': event.get('queue'),
            'reply_queue': event.get('reply_queue'),
            'req_id': event.get('req_id'),
            'request_hex': bytes(raw_req[:24]).hex(),
            'request': {'unit': unit, 'name': None, 'handle': None, 'size': None},
            'pool_policy': self.pool_policy,
            'reply': {'ret_code': event.get('ret_code'),
                      'handle': _hex_int(event.get('handle')),
                      'phys_base': self.phys_base, 'pool_size': self.pool_size,
                      'reply_ptr': event.get('reply_ptr')},
            'selection': selection,
            'result': result,
            'error': error_reason,
            'unsupported': unsupported,
            'osd_pool': osd_pool,
            'provenance': provenance,
        }
        self.telemetry.append(record)
        return record

    def _record_dmm_rejection(self, raw_req: bytes, msg_ptr: int, reason: str) -> None:
        opcode = struct.unpack_from('<H', raw_req, 0)[0] if len(raw_req) >= 2 else None
        self.telemetry.append({
            'seq': len(self.telemetry),
            'transport': 'dmm',
            'opcode': opcode,
            'msg_ptr': msg_ptr,
            'action': 'dmm_request_rejected',
            'request_hex': bytes(raw_req[:24]).hex(),
            'request': {'unit': None, 'name': None, 'handle': None, 'size': None},
            'pool_policy': self.pool_policy,
            'selection': None,
            'result': 'error',
            'error': reason,
            'unsupported': True,
            'osd_pool': False,
            'provenance': 'legacy-dmm-contract-guard',
        })

    def handle_dmm_message(self, memory_reader, memory_writer, msg_ptr: int, uipc) -> Optional[Dict[str, Any]]:
        """Handles an asynchronous DMM service request (queue 0x0061003c).

        Verified against Sony firmware module /usr/kmod/dmm.ko (dmm_ctrl_AllocUnitNewHdlr)
        and libObj.so (dmpNative::InternalCmd::alloc_sync).
        """
        raw_req = memory_reader(msg_ptr, min(64, uipc.allocated_msgs.get(msg_ptr, 32)))
        if len(raw_req) < 24:
            if self.telemetry_policy != TELEMETRY_OFF:
                self._record_dmm_rejection(raw_req, msg_ptr, 'short_dmm_request')
            return None

        opcode = struct.unpack_from('<H', raw_req, 0)[0]
        unit = struct.unpack_from('<I', raw_req, 4)[0]
        reply_queue = struct.unpack_from('<I', raw_req, 16)[0]
        req_id = struct.unpack_from('<H', raw_req, 20)[0]

        if reply_queue == 0:
            if self.telemetry_policy != TELEMETRY_OFF:
                self._record_dmm_rejection(raw_req, msg_ptr, 'missing_reply_queue')
            return None

        # Allocate reply message in shared UIPC pool
        reply_ptr = uipc.allocate_message(size=32)

        # Write sender queue ID at reply_ptr - 4 for osal_get_msg_src(reply_ptr) == 0x0061003c
        memory_writer(reply_ptr - 4, struct.pack('<I', DMM_SERVICE_QUEUE))

        # Build completion packet (dmm_comp):
        # offset 0: opcode (uint16)
        # offset 2: ret_code = 0 (int16, OSAL_ERR_OK)
        # offset 4: handle = GRM_DMM_HANDLE (0x0417)
        # offset 6: req_id = req_id (uint16, matching request)
        # offset 8: phys_base (uint32, 0x202e0000)
        # offset 12: size (uint32, 0x02000000)
        # offset 16 (0x10): phys_base (uint32, 0x202e0000) - consumed by this->mem.map
        # offset 20 (0x14): size (uint32, 0x02000000) - consumed by this->mem.map
        # offset 24: unused (uint32, 0)
        # offset 28: unused (uint32, 0)
        reply_handle = GRM_DMM_HANDLE
        reply_phys = self.phys_base
        reply_size = self.pool_size
        if self.pool_policy == POOL_POLICY_CAPTURED_WBI_DMM:
            claimed = self.__dict__.setdefault('claimed_dmm_handles', set())
            candidates = [p for p in CAPTURED_WBI_DMM_POOLS if p.dmm_unit == unit]
            if candidates:
                chosen = next((p for p in candidates if p.dmm_handle not in claimed), candidates[0])
                claimed.add(chosen.dmm_handle)
                reply_handle = chosen.dmm_handle
                reply_phys = chosen.phys_base
                reply_size = chosen.pool_size

        reply_data = struct.pack('<hhHHIIIIII', opcode, 0, reply_handle, req_id,
                                 reply_phys, reply_size,
                                 reply_phys, reply_size,
                                 0, 0)
        memory_writer(reply_ptr, reply_data)

        # Enqueue reply into caller's dynamic reply queue
        uipc.enqueue_message(reply_queue, reply_ptr)

        event = {
            'action': 'dmm_ctrl_AllocUnitNewHdlr',
            'queue': '0x0061003c',
            'reply_queue': f'0x{reply_queue:08x}',
            'opcode': opcode,
            'unit': f'0x{unit:08x}',
            'req_id': req_id,
            'handle': f'0x{reply_handle:04x}',
            'phys_base': f'0x{reply_phys:08x}',
            'pool_size': reply_size,
            'ret_code': 0,
            'reply_ptr': reply_ptr
        }
        if self.telemetry_policy != TELEMETRY_OFF:
            event['telemetry'] = self._record_dmm_telemetry(event, raw_req, msg_ptr)
        self.events.append(event)
        return event

