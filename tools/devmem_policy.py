"""What the ILCE-6400 stock kernel lets /dev/mem reach, and what the hardware survives (E0647).

The stock kernel (3.0.27_nl-rt106+, Image SHA-256 d7a9ae09...) puts no useful guard in front
of /dev/mem:

  * no CONFIG_STRICT_DEVMEM (no devmem_is_allowed in the live symbols);
  * valid_mmap_phys_addr_range accepts any range below 4 GiB;
  * Sony's mach-cxd900x0/mmap.c attribute check (phys_mem_access_prot_allowed) is off: the
    module parameter mmap.debug is 0 (BSS default, read on the camera). When non-zero it warns
    (1), refuses (2) or BUG()s (other values) on a cached/uncached attribute conflict;
  * phys_mem_access_prot: pages the kernel tracks as RAM (sparsemem section present) become
    Normal non-cacheable with O_SYNC and *cacheable* without it; everything else is
    Strongly-ordered;
  * read()/write() on /dev/mem go through the kernel linear map (valid_phys_addr_range up to
    high_memory), so a bad address faults in kernel mode.

The hardware is the only limit, and Sony's exception monitor (em_exception_monitor) turns
any synchronous external abort -- even one taken by a user process -- into "Entering
exception monitor" followed by WARMBOOT. Measured on 2026-11-19 (persisted /proc/klog):
_ro_page reading PA 0x000F8000 -> "external abort on non-linefetch (0x008)" -> warm reboot.
The monitor is reached only from die() (kernel-mode faults) and do_coredump() (an uncaught
SIGBUS). A reader that handles SIGBUS survives a precise abort: on 2026-11-19, with an explicit
go-ahead, ro_page v2 (sigsetjmp + SIGBUS handler) read PA 0x000F8000 on the camera, the kernel
logged "Unhandled fault: external abort on non-linefetch (0x008)", ro_page reported
"bus error caught", and the camera kept running. Imprecise aborts and kernel-mode accesses
cannot be caught this way.

Policy, from that evidence:
  ALLOWED     verified passive register pages inside the kernel's own static io table
  GO          other pages inside the io table or the kernel-mapped SRAM: need an explicit
              per-address user go-ahead (registers may have read side effects; SRAM untested)
  With catching=True (the reader handles SIGBUS, like ro_page v2), the reserved first MiB and
  undecoded holes move from FORBIDDEN to GO: a precise abort is survivable, but each address
  still needs a go-ahead because an imprecise abort would not be. DRAM stays FORBIDDEN (the
  risk there is the attribute alias, not an abort).
  FORBIDDEN   everything else: the reserved first MiB, all DRAM (use process memory/ptrace
              through the owner's mapping instead, E0298), unmapped holes, SRAM beyond 1 MiB,
              multi-page/bulk ranges, read() on /dev/mem, /dev/kmem, /dev/stream
"""
from __future__ import annotations

from dataclasses import dataclass

PAGE = 0x1000

# Static io table of cxd900x0_map_io (iotable at 0x604B4AF0, 8 entries, all MT_DEVICE).
IOTABLE = (
    (0xF0000000, 0x05000000), (0xF5000000, 0x00100000), (0xF6000000, 0x00100000),
    (0xF8000000, 0x00100000), (0xF9000000, 0x00100000), (0xFA000000, 0x04000000),
    (0xBF800000, 0x00100000), (0xBFC00000, 0x00100000),
)
SRAM = (0xFE000000, 0x00100000)          # 1 MiB SRAM; the kernel maps a 16 MiB window from here
SRAM_WINDOW = (0xFE000000, 0x01000000)
DRAM_BANKS = ((0x00000000, 0x40000000), (0x80000000, 0x10000000))
RESERVED_FIRST_MIB = (0x00000000, 0x00100000)

# Pages read on the physical camera without harm, with their evidence.
VERIFIED_PAGES = {
    0xF2915000: "CXD90045 MISCREG/eFuse ID registers, single-page read OK (E0643)",
}
# Pages proven to reboot the camera.
PROVEN_FATAL = {
    0x000F8000: "AXI DECERR (FSR 0x008): uncaught SIGBUS -> core dump -> exception monitor -> WARMBOOT; "
                "caught by ro_page v2 on the camera without a reboot (E0647)",
}


@dataclass(frozen=True)
class Verdict:
    level: str        # "ALLOWED", "GO", "FORBIDDEN"
    reason: str


def _inside(pa: int, size: int, region: tuple[int, int]) -> bool:
    return region[0] <= pa and pa + size <= region[0] + region[1]


def _overlaps(pa: int, size: int, region: tuple[int, int]) -> bool:
    return pa < region[0] + region[1] and region[0] < pa + size


def classify(pa: int, size: int = PAGE, method: str = "mmap", catching: bool = False) -> Verdict:
    """Classify one /dev/mem access of [pa, pa + size) made with `method` ("mmap" or "read").

    `catching` means the reader handles SIGBUS (ro_page v2), so a precise abort does not reboot.
    """
    if method != "mmap":
        return Verdict("FORBIDDEN", "read()/write() on /dev/mem fault in kernel mode via the linear map")
    if pa % PAGE or size != PAGE:
        return Verdict("FORBIDDEN", "only single, page-aligned 4 KiB mappings are considered (no bulk ranges)")
    if pa in PROVEN_FATAL:
        return Verdict("GO" if catching else "FORBIDDEN", PROVEN_FATAL[pa])
    if _overlaps(pa, size, RESERVED_FIRST_MIB):
        return Verdict("GO" if catching else "FORBIDDEN",
                       "reserved first MiB (memrsv=1M@0) is not plain DRAM: 0x000F8000 is an AXI decode "
                       "error (E0647); only a SIGBUS-catching reader, one address per go-ahead")
    for bank in DRAM_BANKS:
        if _overlaps(pa, size, bank):
            return Verdict("FORBIDDEN", "DRAM (precautionary, not proven per page): /dev/mem would add a "
                                        "differently-attributed alias; a /dev/stream DRAM read (E0297) and a "
                                        "bulk /dev/mem scan (E0314) both rebooted the camera. Read through the "
                                        "owning process (ptrace / debugger mem, E0298) instead")
    if pa in VERIFIED_PAGES:
        return Verdict("ALLOWED", VERIFIED_PAGES[pa])
    if any(_inside(pa, size, r) for r in IOTABLE):
        return Verdict("GO", "inside the kernel's static device map, but registers may have read side "
                             "effects (FIFO pops, read-to-clear status): needs a per-address go-ahead")
    if _inside(pa, size, SRAM):
        return Verdict("GO", "kernel-mapped SRAM (SMP mailbox at 0xFE005010); not yet read from user space "
                             "on the camera: needs a per-address go-ahead")
    if _inside(pa, size, SRAM_WINDOW):
        return Verdict("FORBIDDEN", "inside the 16 MiB SRAM window but beyond the 1 MiB SRAM: backing unknown")
    return Verdict("GO" if catching else "FORBIDDEN",
                   "not RAM and not in the kernel's device map: likely an undecoded hole (bus error); only a "
                   "SIGBUS-catching reader, one address per go-ahead")


def check_pages(pages: list[int], go: set[int] = frozenset(),
                catching: bool = False) -> list[tuple[int, Verdict, bool]]:
    """Return (page, verdict, permitted) for each page; GO pages are permitted only if listed in `go`."""
    out = []
    for pa in pages:
        v = classify(pa, catching=catching)
        out.append((pa, v, v.level == "ALLOWED" or (v.level == "GO" and pa in go)))
    return out


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:] or ["0x0", "0xF8000", "0xFE005000", "0xF2915000", "0xF2402000", "0x10000000"]:
        v = classify(int(arg, 0))
        print(f"0x{int(arg, 0):08X}  {v.level:9}  {v.reason}")
