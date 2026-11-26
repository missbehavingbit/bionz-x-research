"""Original ARM FUSB guard replay for the captured ILCE-6400 USB kernel."""
import hashlib
import struct

from unicorn import (
    Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_MODE_ARM, UC_PROT_EXEC, UC_PROT_READ,
)
from unicorn import arm_const as arm


KERNEL_SHA256 = "d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618"
KERNEL_SIZE = 4_085_888
KERNEL_BASE = 0x60108000
FUSB_PAGE = 0x602AC000
FUSB_SET_SPEED = 0x602AC96C
FUSB_START_GADGET = 0x602ACA8C
FUSB_START_CALL = 0x602ACB04
DWC3_OTG_PROBE = 0x602B1B9C
DWC3_CORE_SOFT_RESET = 0x602AE0F4
L2X0_CACHE_SYNC = 0x6011A5D0
L2X0_SYNC_STORE = 0x6011A5E8
FUSB_CONTEXT_SLOT = 0x6051B024
FUSB_CORE_BASE = 0xF0200000
START_GATE = FUSB_CORE_BASE + 0x24

# Scratch addresses for the isolated original ARM calls, never in im.elf.
CONTEXT = 0x60010000
CALLBACK = 0x60020000
DWC3_OBJECT_UNSUPPLIED = 0x60030000
STACK = 0x61000000
RETURN = 0x62000000


def _put(machine, address, value):
    machine.mem_write(address, struct.pack("<I", value))


def _word(machine, address):
    return struct.unpack("<I", machine.mem_read(address, 4))[0]


def check_dwc3_context(context):
    fields = ('fusb', 'fusb_state', 'cid', 'vbus', 'binding', 'callback', 'dwc3',
              'registers', 'outer_sync', 'l2x0_registers', 'gctl', 'kernel_bias')
    if (not isinstance(context, dict) or context.get('kernel_sha256') != KERNEL_SHA256 or
            any(type(context.get(field)) is not int or not 0 <= context[field] <= 0xFFFFFFFF
                for field in fields)):
        raise ValueError('Malformed or mismatched native DWC3 context')
    expected = {'fusb_state': 2, 'cid': 1, 'vbus': 1, 'binding': 0x604E4E30,
                'callback': DWC3_OTG_PROBE, 'kernel_bias': 0x60000000,
                'outer_sync': L2X0_CACHE_SYNC, 'l2x0_registers': 0xF8002000}
    if (any(context[field] != value for field, value in expected.items())
            or not 0x68000000 <= context['fusb'] < 0xA0000000
            or not 0x68000000 <= context['dwc3'] < 0x9FFFFFFF - 0x2B4
            or (context['fusb'] | context['dwc3']) & 3
            or not 0xA0000000 <= context['registers'] < 0xC0000000
            or context['registers'] & 0xFFF != 0x100
            or context['gctl'] & 0x800):
        raise ValueError('Native DWC3 context is outside the bound reset profile')
    return {field: context[field] for field in fields}


class CapturedFusbStartup:
    """SET_MODE leaf plus the E0597 connected guard, not a UDC or kernel."""

    def __init__(self, kernel, *, gate_clear_control=False, qemu_context=None):
        digest = hashlib.sha256(kernel).hexdigest()
        if len(kernel) != KERNEL_SIZE or digest != KERNEL_SHA256:
            raise ValueError("Captured FUSB kernel identity mismatch")
        # fusb_otg_init publishes this operations vector; dwc3_gadget_init
        # binds the second object at fusb+0xbc.
        for address, expected in ((0x602ADA50, 0x604E4CF4),
                                  (0x604E4D04, FUSB_START_GADGET),
                                  (0x604E4D30, FUSB_SET_SPEED),
                                  (0x604E4E38, DWC3_OTG_PROBE)):
            if struct.unpack_from("<I", kernel, address - KERNEL_BASE)[0] != expected:
                raise ValueError(f"Unexpected captured FUSB anchor at {address:#x}")
        self.sha256 = digest
        offset = FUSB_PAGE - KERNEL_BASE
        self.code = bytes(kernel[offset:offset + 4096])
        self.start_gate = 0 if gate_clear_control else 1
        self.requested_mode = None
        self.speed_selector = None
        self.qemu_context = None
        if qemu_context is not None:
            if gate_clear_control:
                raise ValueError('The DWC3 context is only for the connected guard')
            self.qemu_context = check_dwc3_context(qemu_context)
            pages = {address & ~0xFFF for address in (DWC3_OTG_PROBE, DWC3_CORE_SOFT_RESET,
                                                       L2X0_CACHE_SYNC)}
            self.reset_code = {page: bytes(kernel[page - KERNEL_BASE:page - KERNEL_BASE + 4096])
                               for page in pages}

    def _machine(self):
        machine = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        machine.mem_map(FUSB_PAGE, 4096, UC_PROT_READ | UC_PROT_EXEC)
        machine.mem_write(FUSB_PAGE, self.code)
        machine.mem_map(CONTEXT, 4096)
        machine.mem_map(STACK, 4096)
        machine.reg_write(arm.UC_ARM_REG_R0, CONTEXT + 8)
        machine.reg_write(arm.UC_ARM_REG_SP, STACK + 0xF00)
        machine.reg_write(arm.UC_ARM_REG_LR, RETURN)
        return machine

    def set_mode(self, mode):
        """Run ops[+0x3c] and retain its requested speed, not link speed."""
        if mode not in (1, 2):
            raise ValueError("FUSB mode must pass the ioctl's original 1/2 guard")
        machine = self._machine()
        selector = 2 - mode  # native 0x602B7C24..0x602B7C38
        machine.reg_write(arm.UC_ARM_REG_R1, selector)
        _put(machine, CONTEXT + 0x90, 0xFFFFFFFF)
        machine.emu_start(FUSB_SET_SPEED, RETURN, count=16)
        result = machine.reg_read(arm.UC_ARM_REG_R0)
        stored = _word(machine, CONTEXT + 0x90)
        if (machine.reg_read(arm.UC_ARM_REG_PC), result, stored) != (RETURN, 0, selector):
            raise ValueError("Original FUSB SET_MODE callback did not return and store")
        self.requested_mode, self.speed_selector = mode, stored
        return {"requested_mode": mode, "speed_selector": self.speed_selector, "returned": result,
                "callback_pc": FUSB_SET_SPEED, "kernel_sha256": self.sha256}

    def start_guard(self):
        """Return the original early EBUSY, or stop at the bound DWC3 call."""
        if self.requested_mode != 2 or self.speed_selector != 0:
            raise ValueError("FUSB start requires the original SET_MODE(2) store")
        machine = self._machine()
        machine.mem_map(FUSB_CONTEXT_SLOT & ~0xFFF, 4096, UC_PROT_READ)
        machine.mem_map(CALLBACK, 4096, UC_PROT_READ)
        machine.mem_map(FUSB_CORE_BASE, 4096, UC_PROT_READ)
        _put(machine, FUSB_CONTEXT_SLOT, CONTEXT)
        _put(machine, CONTEXT + 0x1C, FUSB_CORE_BASE)
        _put(machine, CONTEXT + 0x80, 2)  # E0597: FUSB control is idle.
        _put(machine, CONTEXT + 0x84, 1)  # FUSB CID
        _put(machine, CONTEXT + 0x88, 1)  # FUSB VBUS, from fusb_notify_vbus
        _put(machine, CONTEXT + 0x90, self.speed_selector)
        _put(machine, CONTEXT + 0xBC, CALLBACK)
        dwc3 = (self.qemu_context['dwc3'] if self.qemu_context is not None
                else DWC3_OBJECT_UNSUPPLIED)
        _put(machine, CALLBACK + 4, dwc3)
        _put(machine, CALLBACK + 8, DWC3_OTG_PROBE)
        _put(machine, START_GATE, self.start_gate)  # E0597 snapshot or negative control.

        last_instruction = None

        def observe(uc, address, size, data):
            nonlocal last_instruction
            last_instruction = address
            if address == FUSB_START_CALL:
                uc.emu_stop()

        hook = machine.hook_add(UC_HOOK_CODE, observe)
        machine.emu_start(FUSB_START_GADGET, RETURN, count=48)
        machine.hook_del(hook)
        pc = machine.reg_read(arm.UC_ARM_REG_PC)
        target = machine.reg_read(arm.UC_ARM_REG_R3)
        r0 = machine.reg_read(arm.UC_ARM_REG_R0)
        state = _word(machine, CONTEXT + 0x80)
        observation = {"kernel_pc": last_instruction, "fusb_state": state, "cid": 1, "vbus": 1,
                       "start_gate": self.start_gate, "start_gate_address": START_GATE,
                       "speed_selector": self.speed_selector, "kernel_sha256": self.sha256}
        if (self.start_gate == 0 and
                (pc, last_instruction, r0, state) == (RETURN, 0x602ACB2C, 0xFFFFFFF0, 2)):
            return {**observation, "returned": r0 - (1 << 32)}
        if (self.start_gate != 1 or
                (pc, target, r0, state) != (FUSB_START_CALL, DWC3_OTG_PROBE,
                                           dwc3, 2)):
            raise ValueError("Original FUSB start did not reach the bound DWC3 callback")
        if self.qemu_context is not None:
            return {**observation, 'fusb_call_pc': pc, **self._run_reset_prefix(machine)}
        return {**observation, "callback_pc": target,
                "callback_argument": "DWC3 kernel object not supplied"}

    def _run_reset_prefix(self, machine):
        context = self.qemu_context
        words = {
            context['dwc3'] + 0x2B4: context['registers'],
            context['registers'] + 0x10: context['gctl'],
            0x604C92EC: context['outer_sync'],
            0x604F7824: context['l2x0_registers'],
        }
        for page, data in self.reset_code.items():
            machine.mem_map(page, 4096, UC_PROT_READ | UC_PROT_EXEC)
            machine.mem_write(page, data)
        for page in {address & ~0xFFF for address in words}:
            machine.mem_map(page, 4096, UC_PROT_READ)
        for address, value in words.items():
            _put(machine, address, value)

        def guard_read(uc, access, address, size, value, data):
            if (size == 4 and address in words) or address & ~0xFFF in self.reset_code:
                return
            raise ValueError(f'Native DWC3 reset read outside declared fields at {address:#x}')

        def stop_before_sync(uc, address, size, data):
            uc.emu_stop()

        machine.hook_add(UC_HOOK_MEM_READ, guard_read)
        machine.hook_add(UC_HOOK_CODE, stop_before_sync, begin=L2X0_SYNC_STORE, end=L2X0_SYNC_STORE)
        machine.emu_start(FUSB_START_CALL, RETURN, count=48)
        pc = machine.reg_read(arm.UC_ARM_REG_PC)
        base = machine.reg_read(arm.UC_ARM_REG_R3)
        word = machine.reg_read(arm.UC_ARM_REG_R2)
        prepared_gctl = machine.reg_read(arm.UC_ARM_REG_R6)
        if (pc, base, word, prepared_gctl) != (
                L2X0_SYNC_STORE, context['l2x0_registers'], 0, context['gctl'] | 0x800):
            raise ValueError('Original DWC3 reset did not reach the expected cache-sync store')
        return {'kernel_pc': pc, 'callback_pc': DWC3_OTG_PROBE,
                'callback_argument': context['dwc3'], 'old_gctl': context['gctl'],
                'prepared_gctl': prepared_gctl, 'gctl_write_completed': False,
                'l2x0_sync_address': base + 0x730, 'l2x0_sync_value': word}
