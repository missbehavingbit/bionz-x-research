"""Data provider for project status visualizations.

Two independent outputs share this module:

* ``get_a6400_project_status_data()`` feeds the treemap PNG
  (``tools/research/generate_status_image.py`` -> ``docs/images/status_a6400.png``).
  Its integer "done/total" counters deliberately keep their original scope.
* ``STATUS_SNAPSHOT`` feeds the narrative status card
  (``tools/research/render_research_status.py`` -> ``docs/images/research-status.svg``)
  and is the single source of truth that ``tests/test_research_status_sync.py``
  checks against ``README.md``.

``STATUS_SNAPSHOT`` carries plain claims, evidence references and the recorded
suite count; it never converts them into a compatibility percentage.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.a6400.visualization.status_renderer import Category, Panel

ROOT = Path(__file__).resolve().parents[2]


def get_anyps5_reference_data() -> list[Panel]:
    """Reconstructs the reference AnyPS5 status dataset matching the user's reference image."""
    sys_cats = [
        Category("libc", 1480, 1620),
        Category("libkernel", 680, 740),
        Category("Agc", 290, 290),
        Category("Json2", 180, 180),
        Category("Net", 140, 155),
        Category("Http", 125, 135),
        Category("Font", 110, 120),
        Category("Pad", 85, 95),
        Category("Audi", 80, 90),
        Category("Vid", 60, 60),
        Category("Lib", 55, 60),
        Category("Usbd", 50, 58),
        Category("Ngs", 48, 55),
        Category("NpM", 42, 48),
        Category("Ajm", 38, 44),
        Category("Rtc", 35, 35),
        Category("Hmd", 30, 36),
        Category("Ssl", 25, 30),
        Category("Ampr", 40, 52),
        Category("NpUn", 35, 45),
    ]

    gpu_cats = [
        Category("VOPC", 160, 160),
        Category("DS", 145, 145),
        Category("VOP1", 135, 135),
        Category("MUBUF", 125, 125),
        Category("SOP1", 115, 115),
        Category("MIMG", 110, 110),
        Category("VOP3", 100, 100),
        Category("GLOBAL", 90, 90),
        Category("SOP2", 80, 80),
        Category("SOPP", 55, 55),
        Category("VOP3P", 50, 50),
        Category("FLAT", 45, 45),
        Category("VOP2", 40, 40),
        Category("SOPK", 30, 30),
        Category("SMEM", 25, 25),
        Category("SOPC", 20, 20),
        Category("SCRATC", 16, 16),
        Category("MTBUF", 10, 10),
    ]

    # Force exact 3480/3853 for system libraries matching the reference
    cur_done = sum(c.done for c in sys_cats)
    cur_total = sum(c.total for c in sys_cats)
    sys_cats[0].done += (3480 - cur_done)
    sys_cats[0].total += (3853 - cur_total)

    # Force exact 1166/1166 for GPU shaders matching the reference
    cur_gpu = sum(c.total for c in gpu_cats)
    gpu_cats[0].done += (1166 - cur_gpu)
    gpu_cats[0].total += (1166 - cur_gpu)

    return [
        Panel(title="System libraries*", badge_label="libraries*", categories=sys_cats),
        Panel(title="GPU shader instructions", badge_label="shaders", categories=gpu_cats),
    ]


def get_a6400_project_status_data() -> list[Panel]:
    """Compiles real evidence-backed status metrics for the Sony a6400 execution project."""
    boot_trace_path = ROOT / "artifacts/inventory/boot-mount-trace.json"
    boot_mounts_count = 22
    devnodes_count = 88
    if boot_trace_path.exists():
        try:
            with open(boot_trace_path, encoding="utf-8") as f:
                data = json.load(f)
                boot_mounts_count = len(data.get("init_mounts_table", [])) or 22
                devnodes_count = data.get("devnodes_summary", {}).get("total_entries", 88)
        except Exception:
            pass

    guest_os_cats = [
        Category("preset_backup", 4, 4, weight=18.0),        # BK4PresetState, PresetBackedBackupService, write/read dispatch, authentic adr=0x35c6000
        Category("backup_boot", 3, 3, weight=18.0),         # Backup_load=0, arch_phys_to_cache RAM map, and Backup_write_core pipeline
        Category("backup_guard", 1, 1, weight=16.0),        # Stops before unsupplied backup replies; no service or persistent-state compatibility claim
        Category("inode_meta", 2, 2, weight=16.0),          # Opt-in regular rdev and selected read-only ext2 image timestamps; no physical mount claim
        Category("clone_return", 1, 1, weight=16.0),         # Child resumes original syscall-return code; guest libc owns dispatch/exit
        Category("tls_switch", 2, 2, weight=16.0),           # Hardware TPIDRURO and software TLS agree across thread switches
        Category("anon_discard", 1, 1, weight=16.0),        # Opt-in MADV_DONTNEED on owned private anonymous pages only
        Category("boot_commands", 7, 7, weight=18.0),       # Seven original bootin phase-command dispatch windows; no process launch or full boot claimed
        Category("arm_syscalls", 36, 36, weight=35.0),       # brk, uname, open, read, close, stat64, mmap2, mprotect, munmap, set_tls, set_tid_address, set_robust_list, rt_sigaction, rt_sigprocmask, ugetrlimit, ioctl, fcntl64, fstat64, write, writev, clone, gettid, prctl, sched_setscheduler, sched_setaffinity, futex, exit, nanosleep
        Category("ext2_guest_fs", 20, 20, weight=35.0),      # image lookup, inodes, stat64, verified st_dev, preflight, devnode boundary, cdev recognition
        Category("guest_mmap", 14, 14, weight=30.0),         # page alignment, MAP_FIXED, file-backed loading, protection, initial mapping registration, uipc shared pool
        Category("uipc_ipc_cdev", 13, 13, weight=28.0),      # connect, valloc, snd_sync_msg, free_msg, reg_msg_queue_cb, snd_sync_direct, wai_sem_tmo, sig_sem, stat_sem, snd_msg, rcv_msg_tmo, stat_msgq, dynamic_id
        Category("threads_nptl", 5, 5, weight=28.0),         # main thread + 4 Sony infrastructure threads (THREAD_INFRA0..3)
        Category("guest_stdio", 3, 3, weight=25.0),          # /dev/console metadata, TCGETS isatty, stdout/stderr vector logging
        Category("task94_phases", 1, 8, weight=28.0),        # Phase 1 event dispatch observed; remaining subsystem initialization is unverified
        Category("app_milestone", 1, 1, weight=25.0),        # im.elf entry point reached at 0x9140 (Thumb 0x9141) at instr 2,073,233
        Category("kuser_helpers", 5, 5, weight=25.0),        # 0xffff0000 high vectors, __kuser_cmpxchg64, memory_barrier, get_tls, version 5
        Category("kernel_identity", 6, 6, weight=18.0),       # UTS uname strings, arch, image verification
        Category("probe_policies", 9, 9, weight=20.0),       # stop, brk, brk-uname, files, metadata, mmap, kuser, uipc, threads
        Category("linker_contracts", 12, 12, weight=30.0),    # ld-linux.so: all 12 shared libraries loaded, relocated, RELRO protected, call_init completed
        Category("event_dispatch", 8, 8, weight=30.0),       # 0x9a5c callback: phase 1..8 event IDs, authentic event injection, 094_0 state transitions
        Category("imdb_plugins", 0, 198, weight=35.0),      # 198 static definitions; dependency opens and component log markers do not establish plugin initialization
        Category("bounded_run", 1, 1, weight=40.0), # One bounded original-code execution milestone; instruction counts are not completion units
    ]

    hardware_cats = [
        Category("entropy_device", 2, 4, weight=16.0),      # Image 1:9 identity and original dispatch; runtime pool/output unverified
        Category("emmc_driver", 18, 22, weight=32.0),        # RingBuffer, Dec_Scramble, worker kthread, major 252
        Category("boot_mounts", 15, boot_mounts_count, weight=30.0), # dnflasha7, nflasha7 fallback, usr, etc
        Category("devno_nodes", 28, devnodes_count, weight=38.0),    # flash block nodes, sio, dmac, ldec
        Category("kernel_drivers", 10, 37, weight=26.0),     # drivers1 (devno, dmac, ldec, emmc) + drivers2
        Category("flash_parts", 7, 30, weight=25.0),         # nflasha1-30: verified partitions
        Category("display_subsys", 2, 4, weight=30.0),       # Read-only OSD A/B capture (E0298) then GPES BAR/IRQ contracts + opt-in engine model (E0313/E0314); live guest rendering, GPU execution and camera comparison unverified
        Category("sdhci_dma", 10, 10, weight=20.0),         # CMD17/18/24/25, SDHCI_PRESENT_STATE (0x2c), ADMA2 8-byte descriptors (0x58), scatterlist pipeline
        Category("etc_overlay", 4, 5, weight=16.0),          # vfat rw detection, empty rootfs, preload boundary
    ]

    return [
        Panel(title="Guest OS & Linux ARM Contracts", badge_label="contracts", categories=guest_os_cats),
        Panel(title="Hardware & Storage Subsystems", badge_label="hardware", categories=hardware_cats),
    ]



# ---------------------------------------------------------------------------
# Documentation synchronization data (README <-> docs/images/research-status.svg)
# ---------------------------------------------------------------------------
#
# Each field is a claim that is also stated in README.md. tests/test_research_status_sync.py
# fails when the README, this snapshot and the generated SVG disagree on the latest
# evidence ID, the recorded suite count or the latest evidence record on disk.
#
# Update procedure for a new milestone:
#   1. write the evidence record under docs/evidence/,
#   2. add/refresh a card here and bump ``latest`` and ``as_of``,
#   3. run the full suite; update ``suite`` and daily history only on a passing run,
#   4. regenerate the card:  python -m tools.research.render_research_status --write
#   5. the suite must stay green (tests/test_research_status_sync.py enforces the rest).


@dataclass(frozen=True)
class EvidenceRef:
    """One dated, bounded result shown on the status card.

    ``tier`` states what kind of evidence the claim rests on; the renderer prints it
    verbatim so an offline contract can never look like a physical observation.
    """

    evidence_id: str
    path: str
    date: str
    title: str
    demonstrated: str
    boundary: str
    tier: str


@dataclass(frozen=True)
class SuiteRecord:
    """A dated passing full-suite record, which may precede the latest milestone."""

    test_count: int
    recorded_at: str
    evidence_id: str
    command: str


@dataclass(frozen=True)
class StatusSnapshot:
    """Single source of truth for the narrative status card and its README sync."""

    schema_version: int
    as_of: str
    latest: EvidenceRef
    headline: str
    cards: tuple[EvidenceRef, ...]
    suite: SuiteRecord
    footer: str

    @property
    def title(self) -> str:
        return f"Sony a6400 research status through {self.latest.evidence_id}"

    @property
    def description(self) -> str:
        return (
            f"{self.suite.test_count} tests in the recorded offline suite on {self.suite.recorded_at}. "
            "Offline original-code execution, "
            "read-only physical observation and authored demonstrations are reported separately. "
            "QEMU test-chart scanout is separate from the unverified Unicorn L3 GUI; no UVC frame is claimed."
        )


STATUS_SNAPSHOT = StatusSnapshot(
    schema_version=1,
    as_of="2026-11-21",
    latest=EvidenceRef(
        evidence_id="E9103",
        path="docs/evidence/9103-a6100-mod-loader-port.md",
        date="2026-11-21",
        title="Mod Loader v2 (MLCR) & Film Simulations ported to ILCE-6100; multi-target SMOD v2 for ILCE-6400",
        demonstrated=(
            "Offline Picture Profile gate analysis + opt-in PP_UNLOCK=1 overlay (E9101), procedure record (E9102), "
            "and MLV2_TARGET=a6100 Mod Loader v2 QEMU execution with folder navigation, Film Simulations / Studio GUI, "
            "FilmSimLive picker, and FilmTexture SMOD v2 builds targeting both ILCE-6100 1.00 and ILCE-6400 2.00."
        ),
        boundary=(
            "ILCE-6100 Picture Profile unlock and MLCR port are verified in offline dumps and QEMU (no physical a6100 flash "
            "write); ILCE-6400 Ver. 2.00 film-simulation addresses are binary/offline-verified against captured av-cam.bin "
            "and libObj.so without a live hardware camera test."
        ),
        tier="OFFLINE + QEMU + ARMv7 SMOD v2",
    ),
    headline="ILCE-6100 MLCR & Film Sims (a6100/a6400) | L3 OSD surface | Unicorn SIO/GPIO oracle",
    cards=(
        EvidenceRef(
            evidence_id="E9006",
            path="docs/evidence/9006-captured-dmm-unit-4092e-authentic-osd-surface.md",
            date="2026-11-21",
            title="Captured DMM unit 0x4092e is OSD A/B; an opt-in mapping opens the authentic 640x480 surface",
            demonstrated=(
                "The preserved nflasha5 DMM table has two 0x4092e records (indices 28 and 32) whose extents are "
                "OSD A/B: 0x23A80000 and 0x23B16000, size 0x96000, extent id 0x62, handles 0x041C/0x0420. "
                "An opt-in policy returns the first record; the app allocates the full extent, "
                "maps /dev/stream to physical 0x23A80000 and writes all 614,400 bytes. viewUnified2.so is loaded."
            ),
            boundary=(
                "The surface is zero at 200M, so the full write is a clear/black frame; UI content and the draw "
                "trigger are not established. The captured table is a preserved image, not live camera state. "
                "No camera access."
            ),
            tier="OFFLINE PUBLIC UPDATERS + QEMU",
        ),
        EvidenceRef(
            evidence_id="E0677",
            path="docs/evidence/0677-unicorn-sio-gpio-oracle.md",
            date="2026-11-20",
            title="Unicorn SIO and GPIO from the QEMU oracle",
            demonstrated=(
                "A 64000-record QEMU MMIO capture identifies SIO 0xF2405600 and GPIO 0xF2910110. "
                "Unicorn now returns the oracle status word 0x18000000 and port-1 input 0x32, "
                "channel 3 replays the hash-checked 384-byte sub-micro extract, "
                "and ADC unit 1 completes with status 0x101 and released-key results 0x3FF."
            ),
            boundary=(
                "No Unicorn nflasha3 replay in this record, so ObjMic completion and a rear-LCD frame are not shown. "
                "Rear-control, SD, and lens sub-micro edits stay off."
            ),
            tier="OFFLINE UNICORN PERIPHERAL CONTRACT",
        ),
        EvidenceRef(
            evidence_id="E0676",
            path="docs/evidence/0676-unicorn-sysv-vph-frame-clock.md",
            date="2026-11-20",
            title="Unicorn SYSV/VPH frame clock and evsel ext-vd",
            demonstrated=(
                "With --qemu-peripherals, Unicorn advances the QEMU SYSV engine frame (IRQ 98), "
                "60 Hz VPH vsync (IRQ 101/102), and evsel ext-vd (IRQ 83/84) on the 1 MHz instruction tick."
            ),
            boundary=(
                "No captured nflasha3 replay in this record, so ObjMic completion and a rear-LCD frame are not shown. "
                "CPUs 1-3 stay offline. The evsel late-src phase is not ported."
            ),
            tier="OFFLINE UNICORN PERIPHERAL CONTRACT",
        ),
        EvidenceRef(
            evidence_id="E0664",
            path="docs/evidence/0664-shutter-works-drive-mode-decides.md",
            date="2026-11-21",
            title="Camera Wi-Fi: the shutter works, and the drive mode decides whether it is offered",
            demonstrated=(
                "On the owner's ILCE-6400 2.00 the camera AP serves Sony ScalarWebAPI; the packaged "
                "orb-server carries 221 camera methods and Creative Style is not served (E0658). At "
                "drive mode Continuous the camera advertises 47 methods with startContShooting and "
                "without actTakePicture (E0659); at Single it advertises 50 with the shutter, and one "
                "photograph was taken over Wi-Fi after a half-press (E0664). Sony's Android client "
                "7.6.0, decompiled and not run, guards the shutter on that list (E0660). avContent "
                "answers error 12 in 'Control with Smartphone' mode and no network call switches the "
                "mode (E0662)."
            ),
            boundary=(
                "Only drive mode and lens were shown to change the list; whether the exposure mode "
                "gates setShutterSpeed and setExposureMode is untested, and startContShooting was not "
                "re-called with a transcript. 'Send to Smartphone' mode was not observed. The 221-method "
                "count belongs to the packaged orb-server, a different point release than the unit."
            ),
            tier="PHYSICAL + OFFLINE APK",
        ),
        EvidenceRef(
            evidence_id="E0663",
            path="docs/evidence/0663-callgraph-plt-correction-and-real-ipc-table.md",
            date="2026-11-20",
            title="Correction: the IPC table of E0483 was calls to other imports",
            demonstrated=(
                "objdump shows that none of E0483's 29 'IPC call sites' is an IPC call: a fixed "
                "12-byte PLT stride gave each import its neighbour's name once Thumb-prefixed 16-byte "
                "entries appear. With the PLT decoded from each entry's GOT slot (2,547 of 2,547 "
                "agreeing with objdump), bl targets computed from the unaligned PC, Thumb bits "
                "honoured and the section walked by instruction length, the a6400 libObj.so holds "
                "763 IPC call sites, 544 with a constant; six queue ids match ids observed at runtime "
                "in the emulator. E0484's zero direct ObjDisplay_* callers holds on thirteen bodies."
            ),
            boundary=(
                "Constant tracking is straight-line over 64 bytes and does not see a branch into the "
                "window; each constant is a lead to confirm by disassembly. Wrappers, indirect calls "
                "and other libraries are outside the table, and no queue's runtime consumer is claimed."
            ),
            tier="OFFLINE PACKAGED FIRMWARE",
        ),
        EvidenceRef(
            evidence_id="E0657",
            path="docs/evidence/0657-unicorn-l3-allocator-gles-init-and-sugilite-gpu.md",
            date="2026-11-20",
            title="Unicorn L3 allocator ownership, GLES shared init, Sugilite GPU and viewRootPowerSw OSD",
            demonstrated=(
                "Unmodified im.elf runs to 1.50B insns (120 threads, 0 ownership errors without 0x50 padding); "
                "sugilite_open 4B + rm_rqueue_init 128 KiB init, 24 Sugilite GPU/IRQ 76 jobs and OSD A/B verified."
            ),
            boundary=(
                "viewRootPowerSw.uxc presents transparent 640x480 RGBA4444 OSD A/B frames because DefRootView "
                "CheckerBootState/CheckerBootType await external av-cam.bin/UPM producers; E0530 stop preserved."
            ),
            tier="OFFLINE ORIGINAL L3 PROCESS + SUGILITE GPU",
        ),
        EvidenceRef(
            evidence_id="E0656",
            path="docs/evidence/0656-unicorn-main-firmware-boot.md",
            date="2026-11-20",
            title="Unicorn boots captured nflasha3 main firmware through init, LIRO and im.elf",
            demonstrated=(
                "Captured nflasha3 boots on UnicornMachine (--mmio stop, read-only emmc.img) through stock "
                "/sbin/init, 9 mounts, 39 .ko modules, LIRO entry_main, im.elf DMP_2D_Initialize : 0, and ash."
            ),
            boundary=(
                "Single-core Cortex-A9 execution leaves av-cam CPUs 1-3 offline; preloaded Backup.bin/av-cam.bin "
                "model bootloader handoff. No Unicorn rear-LCD/GUI frame or physical camera result is claimed."
            ),
            tier="OFFLINE ORIGINAL MAIN KERNEL + INIT + LIRO + IM.ELF",
        ),
        EvidenceRef(
            evidence_id="E0649",
            path="docs/evidence/0649-ilce6400-live-lens-info-linux-side-negative.md",
            date="2026-11-20",
            title="/dev/mem abort policy, BL lens focus decode, and live lens check",
            demonstrated=(
                "Persisted /proc/klog traces a single-page /dev/mem camera reboot to PA 0x000F8000 "
                "(FSR 0x008 external abort -> exception monitor warm reboot); tools/devmem_policy.py "
                "defines physical access tiers, QEMU lowmem-abort=on models the abort, and SIGBUS-catching "
                "ro_page v2 survives PA 0x000F8000 on hardware (E0647). Static av-cam.bin decode maps BL "
                "focus commands 0x41-0x4A and 0x1C-0x3C behind the capability bitmap gate (E0648); a live "
                "read-only check with a Sigma 18-50 F2.8 DC DN confirms ORIL lens capability data is not "
                "retained in Linux im.elf memory (E0649)."
            ),
            boundary=(
                "Catching SIGBUS covers precise aborts only, not imprecise aborts or kernel reads; "
                "DRAM /dev/mem remains forbidden. Second-generation BL focus field units and the Sigma "
                "mount-bus capability bitmap require a passive mount-bus capture."
            ),
            tier="READ-ONLY + STATIC",
        ),
        EvidenceRef(
            evidence_id="E0655",
            path="docs/evidence/0655-qemu-first-view-ptp-and-native-dwc3-reset.md",
            date="2026-11-19",
            title="Original FirstView USB checks, bulk OUT and DWC3 reset entry",
            demonstrated=(
                "Early stock cable passes sampled FirstView checks. Play produces native Neutral-Off. "
                "Bulk OUT passes qtest, and original Unicorn DWC3 reset enters the PL310 sync path."
            ),
            boundary=(
                "No stock Sony descriptor or data transfer; no original-process SETCONFIG or teardown. "
                "Fixture inputs do not supply a DWC3 kernel object or a streaming capability."
            ),
            tier="OFFLINE QEMU + ORIGINAL ARM GUARD",
        ),
        EvidenceRef(
            evidence_id="E0654",
            path="docs/evidence/0654-usb-stock-attach-and-fusb-start-guard.md",
            date="2026-11-19",
            title="Original stock USB attach and FUSB startup boundaries",
            demonstrated=(
                "Stock QEMU observes the native ModelCamera NeutrOn store and original jack events. "
                "In Unicorn, opt-in original FUSB SET_MODE advances to the bound DWC3 callback."
            ),
            boundary="No stock Sony descriptor or data transfer; no original-process SETCONFIG or teardown.",
            tier="OFFLINE QEMU + ORIGINAL ARM GUARD",
        ),
        EvidenceRef(
            evidence_id="E0653",
            path="docs/evidence/0653-unicorn-updater-child-cow-and-hwcap.md",
            date="2026-11-19",
            title="Unicorn updater child/COW replay and D16 capability comparison",
            demonstrated=(
                "The original updater returns past uname, runs a BusyBox child and preserves parent "
                "state across a subshell. Separately, opt-in D16 matches QEMU's guest AT_HWCAP."
            ),
            boundary=(
                "Updater only. Unicorn still executes on Cortex-A9; main-firmware application or GUI "
                "boot is unverified on this path."
            ),
            tier="OFFLINE ORIGINAL KERNEL + QEMU CAPABILITY REFERENCE",
        ),
        EvidenceRef(
            evidence_id="E0652",
            path="docs/evidence/0652-qemu-a6400-late-black-screen-scanout-capture.md",
            date="2026-11-19",
            title="QEMU a6400 late black-screen scanout and buffer capture",
            demonstrated=(
                "The captured guest's Auto Power Save zeroes the active ch53 OSD buffer and "
                "deactivates modelCamera while the emulated VPH/SYSV scanout keeps ticking."
            ),
            boundary=(
                "A later offline replay verified stock S1 wake and persistence after an explicit native save. "
                "Automatic shutdown save remains unverified; no physical camera result is claimed."
            ),
            tier="OFFLINE QEMU",
        ),
        EvidenceRef(
            evidence_id="E0644",
            path="docs/evidence/0644-a6400-liro-ram-section-is-avcam-data.md",
            date="2026-11-19",
            title="LIRO ram section is av-cam RW data, not a sub-core image",
            demonstrated=(
                "Static scan of av-cam.bin finds 1,878 references into the LIRO ram run region [0x645ACF00, 0x6467BD64) "
                "pointing to data rather than function prologues, superseding E0642's sub-core inference; "
                "handover_guard exec_range traces 499,175 blocks in 120 s before the current stall."
            ),
            boundary=(
                "Offline av-cam.bin static analysis and bounded QEMU traces only; no camera access. "
                "av-cam never reaches the consumer code inside 120 s."
            ),
            tier="OFFLINE",
        ),
        EvidenceRef(
            evidence_id="E0637",
            path="docs/evidence/0637-camcore-f301-contributor-boot-score.md",
            date="2026-11-19",
            title="Contributor boot score for the CamCore 0xF3010000 register model",
            demonstrated=(
                "The merged CamCore window keeps the posted step-26 state while distinct unmodelled "
                "addresses fall from 2322 to 1310. Twenty-eight observed reads are named: 16 "
                "read-modify-write words and 0x5020 return the guest's last write, and 11 status words stay 0."
            ),
            boundary=(
                "Contributor emulator report only. This workspace did not re-run the scorer. The model is "
                "on by default but does not release captured step 26 on its own. No physical camera result is claimed."
            ),
            tier="EMULATOR REPORT",
        ),
        EvidenceRef(
            evidence_id="E0570",
            path="docs/evidence/0570-ilce-7rm4a-offline-sourced-profile.md",
            date="2026-11-17",
            title="ILCE-7RM4A gains a sourced reference profile",
            demonstrated=(
                "The a7R IVA row of the E0477 platform map is a registered reference profile, "
                "sourced only from that body's packaged 1.13 updater. Seven gadget-core symbols "
                "are decoded from its own vmlinux.bin, with a CXD90045 identity, an 18-pair stock "
                "USB set, and an 88-entry devno table that publishes no usb/video or usb/audio node."
            ),
            boundary=(
                "Offline packaged firmware only. No camera, USB, boot, or live dump is claimed. "
                "The kernel addresses are not the a6400's, and ilce_7rm4a is not an execution target. "
                "The E0554 A6400A dispatch map and the QEMU module 1 latch remain as recorded."
            ),
            tier="OFFLINE",
        ),
        EvidenceRef(
            evidence_id="E0554",
            path="docs/evidence/0554-a6400a-v101-jiritsu-command-dispatch.md",
            date="2026-11-17",
            title="A6400A libJiritsu dispatches GET_FILE on halfword 8",
            demonstrated=(
                "The packaged ILCE-6400A 1.01 libJiritsu.so dispatcher at 0x7015 "
                "selects GET_FILE for halfword 8, SET_FILE_PATH for 15 and SET_FILE2 "
                "for 16. The public ZV-E10 GET_FILE value 0x4008 is not that halfword."
            ),
            boundary=(
                "No Senser packet was sent. The library map stays closed, and the "
                "category store at 0x8A46 was not followed through the Senser parser."
            ),
            tier="OFFLINE FIRMWARE",
        ),
        EvidenceRef(
            evidence_id="E0553",
            path="docs/evidence/0553-qemu-a6400-module1-latch-skips-0x0105.md",
            date="2026-11-17",
            title="The module 1 latch never publishes 0x0105",
            demonstrated=(
                "0x63922304 seeds 0x64351F28 with 0x0100. 0x639267BC stores only the value "
                "returned by 0x63D3BD56, which keeps the old halfword unless the request is "
                "0x0112. At step 12 the request at 0x64350F04 is 0x0105 and the enable byte is 0."
            ),
            boundary=(
                "No device model changed. The index stays 12. A later store is not ruled out. "
                "The next measurement is the writer of the byte at 0x64354174."
            ),
            tier="OFFLINE QEMU",
        ),
        EvidenceRef(
            evidence_id="E0524",
            path="docs/evidence/0524-reproducible-captured-qemu-boot-and-liro-waits.md",
            date="2026-11-16",
            title="Reproducible captured QEMU inputs and qualified LIRO event waits",
            demonstrated=(
                "Two hash-checked preparations from the captured boot areas and user partitions have "
                "identical outputs. Correct native eMMC user-area offsets and CMD1 sector negotiation "
                "restore original mounts, four CPUs, LIRO/stella startup and shell command roundtrips. "
                "Stopped task snapshots and bounded original-code analysis identify Task 43 waiting on "
                "flag 10 (mask 0x3373); its IRQ 101/102 paths are enabled but never asserted."
            ),
            boundary=(
                "Recover the real IRQ producer/register/timing contract; do not inject completion flags. "
                "Task 65 is dormant with a finite entry target, not a proven sub-micro deadlock. Task 1's "
                "lifecycle semaphore wait is not established as the sole blocker. No full camera boot, "
                "verified emulator GUI frame or physical operation is claimed."
            ),
            tier="OFFLINE QEMU + ORIGINAL STATIC",
        ),
        EvidenceRef(
            evidence_id="E0523",
            path="docs/evidence/0523-mod-loader-ok-routing-diagnostics.md",
            date="2026-11-15",
            title="Guarded RAM-only Mod Loader and MTF2 menu touch",
            demonstrated=(
                "The owner-run physical checkpoint verifies Mod Loader OK routing, MTF2 state-init "
                "packaging, SMP cache flushing and touch through the loader in ordinary menu tabs."
            ),
            boundary=(
                "Historical physical checkpoint: RAM-only, no persistence or flash write. Its recorded "
                "My Menu, injected Mods-tab and tab-bar-focus limitations remain; no new camera test "
                "was performed for the offline QEMU work."
            ),
            tier="PHYSICAL RAM-ONLY + OFFLINE STATIC",
        ),
        EvidenceRef(
            evidence_id="E0534",
            path="docs/evidence/0534-original-process-usb-gadget-registration-and-otg-boundary.md",
            date="2026-11-18",
            title="Original-process USB registration reaches the guarded OTG startup boundary",
            demonstrated=(
                "Original im.elf startup supplies its own libc heap, pthread state, queues and threads. "
                "Original configure/start messages drive FSM states 1 -> 5 -> 6 -> 7; original bus-power "
                "gadget registration allocates and links the record/object. Configure ownership balances, "
                "and repeated 125877-observation replays preserve the same trace and pending ioctl."
            ),
            boundary=(
                "Gadget created=0: original createGadget stops at unqualified OTG SET_MODE 0x4001E30C. "
                "No SETCONFIG producer/delivery or complete configuration is claimed. Experimental boot "
                "policies and an explicit memory-only port-0 input remain; no hardware reply is invented. "
                "MSC separately returns -5 without its user initializer. No camera access."
            ),
            tier="OFFLINE ORIGINAL PROCESS + EXPLICIT INPUT FIXTURES",
        ),
        EvidenceRef(
            evidence_id="E0533",
            path="docs/evidence/0533-unicorn-native-thumb-userspace-continuation.md",
            date="2026-11-17",
            title="Native Unicorn userspace continuation reaches BusyBox and ash",
            demonstrated=(
                "Native CP15/TLB APIs remove re-entrant guest helpers and preserve banked registers. "
                "Two original updater replays execute the native linker, enter BusyBox _start in "
                "USR/Thumb and complete ash echo/pwd roundtrips. Each records 9806 identical UART bytes, "
                "zero manual Thumb recoveries and zero unmapped accesses before the 800M idle bound."
            ),
            boundary=(
                "Existing Sony handoff, VFP/fixup RAM patches, timer/GIC/SVC and MMIO aids remain. "
                "Builtin roundtrips do not qualify the separate post-uname child/COW exception or "
                "main-firmware application/GUI boot. This is Unicorn, not QEMU; no physical access."
            ),
            tier="OFFLINE ORIGINAL KERNEL + NATIVE USER INSTRUCTIONS",
        ),
        EvidenceRef(
            evidence_id="E0532",
            path="docs/evidence/0532-a6400a-dmm-metadata-bounds-and-lifetime.md",
            date="2026-11-17",
            title="A6400A DMM metadata, bounds and borrowed raw-reply lifetime",
            demonstrated=(
                "Explicit opt-in original-code replay: 24-byte DMM exchanges, separate share/access "
                "counter controls and eight repeatable packet fixtures. Raw lengths 0, 1 and 4096 "
                "serialize exactly to a memory-only sink. Failed metadata/release completions cannot "
                "authorize sending; the default seven-packet baseline remains byte-identical."
            ),
            boundary=(
                "Constructed single-extent metadata, owner state and OSAL completions; no runtime DMM "
                "allocation, physical mapping, filesystem or USB service. The physical-address fixture "
                "stays unmapped. Final owner teardown stops before extent release; multi-extent and "
                "cross-LSI replies remain unsupported. The profile remains reference."
            ),
            tier="OFFLINE ORIGINAL INSTRUCTIONS + DMM BYTE FIXTURES",
        ),
        EvidenceRef(
            evidence_id="E0526",
            path="docs/evidence/0526-stella-allocator-size-isolation-and-padding-trial.md",
            date="2026-11-17",
            title="Stella allocation-size isolation qualifies the earlier overrun explanation",
            demonstrated=(
                "Original-instruction isolation: stock request/chunk/usable sizes are 0x4C/0x50/0x4C; "
                "explicit opt-in request padding produces 0x50/0x58/0x54. The constructor writes only "
                "through offset 0x48 under both policies. Each replay passes eleven checks, repeated with "
                "identical traces. Helpers 0x79E8C2/0x79E902 dispatch allocations, not size queries; "
                "HeapAllocator delegates unchanged to malloc."
            ),
            boundary=(
                "Isolated fragments and constructed chunk/vtable/buffer fixtures, not a full allocation "
                "lifecycle or reproduction of the historical corruption. The earlier confirmed-overrun "
                "explanation is not established by these sites. Padding is not a verified correction; "
                "subtracting four from returned pointers would be invalid. No full Appframework replay "
                "or GUI claim. Its kernel UC_ERR_NOMEM failures remain historical evidence."
            ),
            tier="OFFLINE ORIGINAL INSTRUCTIONS + ALLOCATOR FIXTURES",
        ),
        EvidenceRef(
            evidence_id="E0525",
            path="docs/evidence/0525-a6400a-jiritsu-senser-offline-packets.md",
            date="2026-11-17",
            title="A6400A Jiritsu commands mapped into the original Senser packet encoder",
            demonstrated=(
                "Hash-pinned A6400A 1.01 package: all 18 Jiritsu commands route through category "
                "0x1001 and queue 0x016F0480. Original request packing, reply builders, inline reply "
                "decoding and Senser serialization reproduce seven offline packet fixtures exactly "
                "across two fresh runs. Phase-4 file-handler entry and privilege-rejection controls "
                "stop at their declared boundaries."
            ),
            boundary=(
                "Constructed buffers, streams, DMM metadata and record-only logging/locks/send sink. "
                "Raw replies stop before DMM metadata handling; no OSAL service, mount, file operation, "
                "authentication or USB command is supplied. The profile remains reference. Its "
                "follow-up full-suite run had eight unchanged kernel-fixture UC_ERR_NOMEM errors; "
                "the fully passing suite record remains the dated E0523 baseline."
            ),
            tier="OFFLINE ORIGINAL INSTRUCTIONS + CONSTRUCTED PACKETS",
        ),
        EvidenceRef(
            evidence_id="E0524",
            path="docs/evidence/0524-original-local-osal-setconfig-route.md",
            date="2026-11-17",
            title="Original process-local OSAL SETCONFIG route reaches the USB FSM",
            demonstrated=(
                "Isolated hash-pinned original ARM/Thumb replay: EventListner -> SendEvt -> local "
                "osal_snd_sync_msg -> preProcess -> PostMsg -> futex wake -> UsbControl::Main -> "
                "FSM event 0x13 and 0x0A4002 notification construction. Original envelopes, message "
                "copy/destructors and checked fixture-heap ownership balance. Removing the sync "
                "registration returns -1020 without waking or dispatching."
            ),
            boundary=(
                "Constructed process/thread, heap and pthread-mutex fixtures; not full im.elf startup, "
                "guest glibc/stella or a state-valid gadget producer. Startup FSM state 0 does not handle "
                "the delivered event and remains unchanged; no USB mode transition or physical access. "
                "Its full-suite run had 17 kernel-fixture UC_ERR_NOMEM errors; the passing-suite "
                "record remains the dated E0523 baseline."
            ),
            tier="OFFLINE ORIGINAL INSTRUCTIONS + CHECKED FIXTURES",
        ),
        EvidenceRef(
            evidence_id="E0675",
            path="docs/evidence/0675-a6400a-expanded-bootstrap-review-and-full-dump-boundary.md",
            date="2026-11-16",
            title="Local a6400A capture integrity and expanded-scope review",
            demonstrated=(
                "The local E0673 60 MiB nflasha5 and E0674 2 MiB MSC LUN 1 captures matched "
                "their recorded host hashes. E0675 reverified native identity and postflight "
                "without acquiring any new bytes."
            ),
            boundary=(
                "LUN backing is unverified. Neither a general partition reader nor a qualified "
                "temporary bootstrap or complete firmware capture exists in this record."
            ),
            tier="LOCAL READ-ONLY PHYSICAL + OFFLINE REVIEW",
        ),
        EvidenceRef(
            evidence_id="E0509",
            path="docs/evidence/0509-a6400a-msc-reply-lifetime-and-verified-native-identity.md",
            date="2026-11-13",
            title="a6400A normal identity query succeeds within its reply window",
            demonstrated=(
                "Original ARM/Thumb controls identify a finite 10,000 ms reply timeout. The client "
                "now keeps one request and bounded polling together and refuses stale continuation. "
                "One owner-run transaction returns ILCE-6400A and opaque product code 0032858730 "
                "in 2.727 ms through unchanged Windows USBSTOR, with three SCSI operations, no "
                "BUSY, no request replay, no driver/mode change and no sector/file operation."
            ),
            boundary=(
                "This verifies normal model information, not a debugger, live kernel identity, "
                "/proc, /setting or physical dumps. The exact first-BUSY cause and cold-session "
                "repeatability remain unproven; the delayed E0508 test could miss a finite reply. "
                "The target remains reference."
            ),
            tier="READ-ONLY PHYSICAL + OFFLINE",
        ),
        EvidenceRef(
            evidence_id="E0505",
            path="docs/evidence/0505-av-cam-cc-3dlut-and-ec-gamma-pipeline.md",
            date="2026-11-14",
            title="av-cam.bin CC: 3D LUT (YCbCr 5x9x9) and EC_Gamma pre-8-bit pipeline analysis + sensor tables",
            demonstrated=(
                "Offline static analysis (Ghidra/REA + Capstone, no camera). Decoded the CC: 3D LUT controller "
                "(FUN_63ad0fc8 / FUN_63ad0930) as a YCbCr-domain 5x9x9 LUT with Q14 BT.601/BT.709 conversion, "
                "the EC_Gamma pre-8-bit gamma/knee pipeline (FUN_63ba8158 / FUN_63b1f614), and added typed sensor/LUT "
                "table models in src/a6400/sensor/ with 7 new tests in tests/test_sensor_tables.py. Full suite: 1748 OK."
            ),
            boundary=(
                "Static firmware/table decoding only; no live ISP register capture or runtime frame rendering claimed."
            ),
            tier="OFFLINE STATIC IMAGE",
        ),
        EvidenceRef(
            evidence_id="E0504",
            path="docs/evidence/0504-cxd90045-0x0402f666-pc-ldr-decode-fast-r0-vfp-and-busybox-entry.md",
            date="2026-11-14",
            title="0x0402F666 Thumb USR (T=1) PC-relative ldr.w decode, fast-syscall r0 preserve, ARM VFP (__sigsetjmp), uaccess/high-vector PT walk, and BusyBox _start (0x0000C439) + libc.so.6 execution (svc_hits = 71)",
            demonstrated=(
                "Offline (no camera). Added cpsr to fault_classification_trace and e0504_trace hook at 0x0402F660..0x0402F670: "
                "every fetch at 0x0402F666 enters in Thumb USR mode (cpsr=0x60000030, T=1, M=0x10, sp=0x5E864B50, lr=0x0403D7D1). "
                "Fixed four root causes: (1) _decode_fault_address now handles base_name=='pc' (Thumb/ARM alignment) and Capstone "
                "register aliases (sb/sl/fp/ip), decoding 0x0402F666 (ldr.w r4,[pc,#0xbec]) as data-decoded on 0x04030254 so "
                "0x0402F666 executes once after demand-paging 0x04030000; (2) _capture_fast_syscall_frame at 0x6010DEC0 preserves "
                "live r0 in frame_words[0] so sys_brk(0) returns 0x00055000 instead of entry 0; (3) enabled UC_ARM_REG_FPEXC=0x40000000 "
                "and preserved ARM mode on T=0 recovery so genuine ARM routines in ld-2.11.2.so (__sigsetjmp vstmia ip!,{d8-d15} at "
                "0x0403F034) execute natively; (4) added __ex_table uaccess helpers (__strncpy_from_user, etc.) to demand_page_helpers "
                "and consulted _walk_kernel_pt for vaddr>=0xFFFF0000 so timer IRQs at 0xFFFF0018 use the real vector page (PA 0x1FFFE000). "
                "Result: ld-2.11.2.so maps /lib/libc.so.6 (0x040FC000..0x041F4000) and jumps to BusyBox _start (0x0000C438/0x0000C439); "
                "BusyBox and libc.so.6 execute in USR mode reaching svc_hits=71, kuser_hits=35, pt_frame_gen=330, user_restores=214, "
                "tlb_fills=530,636. Suite 1741 OK."
            ),
            boundary=(
                "No shell yet (serial_bytes=0): at the end of the 800M run, libc.so.6 (LR_usr=0x0412EB07, SP_usr=0x5E864530) branches "
                "to 0x041F2000 (libc.so.6 RW .got/.data page), triggering a fatal user prefetch abort into em_exception_monitor "
                "(__delay+0x8). Next: inspect libc.so.6 around 0x0412EAE0..0x0412EB20 and the GOT/PLT slot holding 0x041F2000."
            ),
            tier="OFFLINE BOUNDED UNICORN BRING-UP",
        ),
        EvidenceRef(
            evidence_id="E0476",
            path="docs/evidence/0476-status-prearm-fix-build-audit-unicorn.md",
            date="2026-11-13",
            title="Status pre-arm fix implemented in the bridge; build, static audit and Unicorn verification pass",
            demonstrated=(
                "Offline only, no camera action. The E0469 root-cause fix (arm the EP0 OUT status TRB at class "
                "data-stage completion, ep0state=3 written before the native call) was implemented in the UVC "
                "callback bridge, rebuilt (module 83,644 B), static-audited (117/8/0, no failures) and verified in "
                "Unicorn (8/8 pass: single arm, ordering, late NRDY ignored, status completion dispatch, error-status "
                "guard). New tests added; the full suite passes with 1576 tests."
            ),
            boundary=(
                "No hardware run: Run B26 needs an explicit user GO. If the 5-second stall persists after the "
                "pre-arm, the host/controller not re-driving the NAKed status stage (H2) is confirmed."
            ),
            tier="OFFLINE + UNICORN",
        ),
        EvidenceRef(
            evidence_id="E0406",
            path="docs/evidence/0406-bcm4339-wifi-bt-tuning-surface-readonly.md",
            date="2026-11-12",
            title="BCM4339 Wi-Fi/BT static surface",
            demonstrated=(
                "Hash-identified BCM4339 firmware and kernel modules from preserved images; string/symbol/table tuning "
                "surface recorded offline."
            ),
            boundary=(
                "No live runtime defaults, regulatory state or radio behaviour claimed; no camera access."
            ),
            tier="OFFLINE STATIC IMAGE",
        ),
        EvidenceRef(
            evidence_id="E0405",
            path="docs/evidence/0405-option-a-offline-preflight.md",
            date="2026-11-12",
            title="Option A offline pre-flight",
            demonstrated=(
                "Full gadgetcore+0x84 reference audit: only the 0xEE/compat MS OS handlers and the gadgetcore ioctl "
                "start/reconfiguration path touch the byte; no MSC or endpoint path does. Root entry layout confirmed as an "
                "internal per-speed record, so bcdUSB is generated at request time."
            ),
            boundary=(
                "bcdUSB source, tree extension, EP0 vendor queue, /dev/a6400_dbg ABI and MS OS 2.0 configuration subset "
                "remain unimplemented; B2/B3 were not started."
            ),
            tier="OFFLINE PRE-FLIGHT + BLOCKERS",
        ),
        EvidenceRef(
            evidence_id="E0404",
            path="docs/evidence/0404-b1b-mode-gate-and-b2-b3-blockers.md",
            date="2026-11-12",
            title="B1b mode-gate inspection",
            demonstrated=(
                "Read-only probe of the live gadgetcore state: MSC tree topology decoded (one interface, Bulk-Only, two "
                "bulk endpoints, max_speed=1) and the Microsoft OS mode gate gadgetcore+0x84 read as 0."
            ),
            boundary=(
                "The zero mode gate blocks the MS OS string/compat path in Mass Storage, so WinUSB auto-binding cannot "
                "proceed without an additional approved kernel write/hook or a different transport class."
            ),
            tier="READ-ONLY PHYSICAL + BLOCKER",
        ),
        EvidenceRef(
            evidence_id="E0403",
            path="docs/evidence/0403-winusb-control-transport-msc-verification.md",
            date="2026-11-12",
            title="WinUSB control transport (B1c)",
            demonstrated=(
                "Bounded 18-byte MS OS string patch in volatile kernel data, executed by a RAM-only helper module with "
                "save/CRC32/readback/restore; two Mass Storage load-unload cycles verified stock->patched->stock CRCs, with "
                "cycle 2 independently proving restoration."
            ),
            boundary=(
                "No vendor interface or /dev/a6400_dbg node; WinUSB was not bound and the winusb transport is untested. "
                "EP0 chunking and bcdUSB gates remain open; PC Remote untouched."
            ),
            tier="PHYSICAL B1C + STATIC AUDIT",
        ),
        EvidenceRef(
            evidence_id="E0402",
            path="docs/evidence/0402-always-on-winusb-debugger-feasibility-and-phase-a.md",
            date="2026-11-12",
            title="Always-on WinUSB debugger (Phase A)",
            demonstrated=(
                "Decoded gadgetcore driver registration/callback/descriptor-tree ABI; built vendor-interface + MS OS 1.0 "
                "descriptor contracts with 16 offline checks, a fail-closed host WinUSB transport, and daemon v2.1 with ping "
                "and a dual-transport loop; staged upgrade preserved a rollback copy."
            ),
            boundary=(
                "Bulk OUT completion and device-recipient vendor dispatch are still undecoded; no gadget module, no "
                "composite interface, no PC Remote reachability yet."
            ),
            tier="OFFLINE ABI + PHASE A DAEMON",
        ),
        EvidenceRef(
            evidence_id="E0401",
            path="docs/evidence/0401-usb-debugger-v2-ring-recover-and-mode-boundary.md",
            date="2026-11-12",
            title="USB Debugger v2",
            demonstrated=(
                "Warm klog ring readout with magic/write-position/CRC32 verification over USB (two boots captured); "
                "non-destructive dmesg, usb-state and process-context recover; staged v1->v2 upgrade with preserved "
                "rollback; same pid through an MSC->PC Remote->MSC round trip; Unicorn trampoline with byte-identical rollback."
            ),
            boundary=(
                "PC Remote exposes no debugger transport; composite-gadget work is required for always-on access there. "
                "No kernel-text write and no loaded-module teardown on hardware."
            ),
            tier="PHYSICAL + UNICORN",
        ),
        EvidenceRef(
            evidence_id="E0398",
            path="docs/evidence/0398-runb21-status-stage-and-disconnect-verdict.md",
            date="2026-11-12",
            title="Run B21 status/disconnect",
            demonstrated=(
                "GET_CUR data completes at 138.358552 act=26, SET_CUR class starts 5.011194 s later and completes act=0; "
                "GET_DESCRIPTOR/GET_CUR share flg=02, so status OUT remains the divergence. ev/trb fields still zero; no status "
                "records. Disconnect hook fired but synchronous restore hung (battery pull)."
            ),
            boundary=(
                "Open: deferred workqueue restore and status-stage hook."
            ),
            tier="PHYSICAL STATUS/DISCONNECT",
        ),
        EvidenceRef(
            evidence_id="E0395",
            path="docs/evidence/0395-runb19-reqflags-fix-verdict.md",
            date="2026-11-12",
            title="Run B19 reqflags verdict",
            demonstrated=(
                "req+0x1a bit1 is read by DWC3 complete_data, but setting it did not remove the 5 s wait: GET data actual=26, SET "
                "class 5.007 s later actual=0. Flag fix rejected; 0x8ac8 is standard config/interface, clear_halt_ep0 only gc+0x83. "
                "Battery pull at final MSC switch."
            ),
            boundary=(
                "Open: status-stage path with silent ring; no flag fix."
            ),
            tier="PHYSICAL FLAG-FIX TEST",
        ),        EvidenceRef(
            evidence_id="E0332",
            path="docs/evidence/0332-sync-callback-delivery-first-invocation.md",
            date="2026-11-11",
            title="UIPC / USB",
            demonstrated=(
                "Kernel-to-user sync callback delivery runs the original h4109 callback "
                "and completes it without fault."
            ),
            boundary=(
                "Open: ADJ packet format, the 0x0A4000 producer and the USB FSM "
                "transition (sync_callback_packet_format_unmodelled)."
            ),
            tier="OFFLINE ORIGINAL CODE",
        ),
        EvidenceRef(
            evidence_id="E0314",
            path="docs/evidence/0314-gpes-bar-irq-contract-engine-model.md",
            date="2026-11-10",
            title="GPES engine / display",
            demonstrated=(
                "GPES BAR and IRQ contracts decoded from read-only observations; the opt-in "
                "engine model reaches a second original guest signal."
            ),
            boundary=(
                "Open: keyed 0x820E completion, guest condition release, OSD mapping and "
                "the rasterizer."
            ),
            tier="READ-ONLY PHYSICAL + OPT-IN MODEL",
        ),
        EvidenceRef(
            evidence_id="E0349",
            path="docs/evidence/0349-p1-foreign-get-jpeg-succeeds-in-pc-remote.md",
            date="2026-11-11",
            title="PTP / composite USB",
            demonstrated=(
                "Pipelined PTP liveview: a foreign GET_JPEG sees real frames from the DMM "
                "unit at 29.8 FPS while the host stream runs."
            ),
            boundary=(
                "Open: the composite enumerates (E0350) but DirectShow cannot connect the "
                "UVC pins, so no UVC frame is claimed."
            ),
            tier="PHYSICAL OBSERVATION",
        ),
        EvidenceRef(
            evidence_id="E0386",
            path="docs/evidence/0386-runb16-depcmd-cmdstatus-and-ring-verdict.md",
            date="2026-11-12",
            title="Run B16 DEPCMD CmdStatus and ring verdict",
            demonstrated=(
                "Fixed ev double-deref gives valid lpos/cnt (GET complete 0x294/c, SET pre 0x2a0/c, SET complete 0x2a8/8); "
                "DEPCMD0/1 CmdStatus=0, XferRscIdx 0/1 at every sample -> no rejected STARTTRANSFER, missing check unexercised; "
                "CLASS lr=5f5694b4, no shadow in B16; SET actual=0 with bounce HWO=1 reproduced; 5 s host-side. Battery pull was "
                "needed; camera healthy MSC, logs/ETL preserved, receipt not clean."
            ),
            boundary=(
                "No firmware fix supported; host/XHC or word-level event trace remains. Battery pull required."
            ),
            tier="PHYSICAL + DEPCMD",
        ),
    ),
    suite=SuiteRecord(
        test_count=2091,
        recorded_at="2026-11-18",
        evidence_id="E0578",
        command="PowerShell here-string | .\\.venv\\Scripts\\python.exe - (physical-volume denied + per-case GC; docs/TOOLCHAIN.md rebase recipe)",
    ),
    footer=(
        "Independent results, not a compatibility percentage. "
        "Evidence and limits are linked from README.md."
    ),
)


def latest_evidence_id() -> str:
    """Latest evidence ID the status documentation is synchronized to."""
    return STATUS_SNAPSHOT.latest.evidence_id


def recorded_test_count() -> int:
    """Full-suite test count recorded for this documentation refresh."""
    return STATUS_SNAPSHOT.suite.test_count


# ===========================================================================
# Telemetry dashboard data (single source of truth for docs/images/research-status.svg)
# ===========================================================================
#
# Every grid cell, bar, KPI value and boundary count below is derived from a tracked
# inventory: the STATUS_SNAPSHOT above, the treemap category counters, the evidence
# directory, or the multi-device target registry. Nothing is a percentage of "firmware
# compatibility", and every group carries the evidence record that backs it.
#
# Cell states:
#   verified  -> hardware-verified AND/OR an offline contract that passes
#   decoded   -> read-only physical observation or a decoded contract
#   boundary  -> partially modelled; the named gate is still open
#   unmapped  -> uninitialised / unmapped inventory item

CELL_VERIFIED = "verified"
CELL_DECODED = "decoded"
CELL_BOUNDARY = "boundary"
CELL_UNMAPPED = "unmapped"
CELL_STATES = (CELL_VERIFIED, CELL_DECODED, CELL_BOUNDARY, CELL_UNMAPPED)

CELL_LABELS = {
    CELL_VERIFIED: "Verified",
    CELL_DECODED: "Decoded / Read-Only",
    CELL_BOUNDARY: "Active Boundary",
    CELL_UNMAPPED: "Unmapped Inventory",
}

DASHBOARD_TITLE = "Status & Bounded Telemetry"
DASHBOARD_SUBTITLE = (
    "Sony a6400 · BIONZ X CXD90045 · bounded contract inventories — not a compatibility score"
)
DASHBOARD_FOOTNOTE = (
    "* Counts reflect bounded contract inventories in a6400_status_data.py, "
    "not an overall firmware compatibility percentage."
)

# Recorded constants: each one is a fact stated by the named evidence record.
TEARDOWN_PHASES = 9          # E0353: [a6400_td] phases=0x1ff = all nine phases ran
TEARDOWN_PHASE_MASK = "0x1ff"
EP0_CLASS_CASES = 12         # E0351: 12/12 Unicorn CLASS-path cases
TRANSPORT_AUDIT_CHECKS = 117  # E0350: encoded transport audit, 117 checks
PIPELINE_CORES = "3.0.27_nl-rt106+"

EVIDENCE_RECORD_RE = re.compile(r"^(\d{4})-[^/]+\.md$")


@dataclass(frozen=True)
class KpiPill:
    """One two-tone shield badge: dark label segment + coloured value segment."""

    label: str
    value: str
    tone: str
    source: str


@dataclass(frozen=True)
class CellGroup:
    """A labelled sub-region of a cellular grid."""

    title: str
    cells: tuple[str, ...]
    source: str

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError(f"cell group {self.title!r} has no cells")
        for cell in self.cells:
            if cell not in CELL_STATES:
                raise ValueError(f"cell group {self.title!r} has unknown state {cell!r}")

    def counts(self) -> dict[str, int]:
        counts = {state: 0 for state in CELL_STATES}
        for cell in self.cells:
            counts[cell] += 1
        return counts

    def done(self) -> int:
        return self.counts()[CELL_VERIFIED]

    def total(self) -> int:
        return len(self.cells)

    def summary(self) -> str:
        counts = self.counts()
        text = f"{counts[CELL_VERIFIED]}/{len(self.cells)} verified"
        if counts[CELL_BOUNDARY]:
            text += f" · {counts[CELL_BOUNDARY]} boundary"
        return text


@dataclass(frozen=True)
class ContractBar:
    """A horizontal 'verified contracts' bar; ratios are bounded inventory counts."""

    label: str
    done: int
    total: int
    detail: str
    source: str
    amber: int = 0

    def __post_init__(self) -> None:
        if self.total <= 0 or not 0 <= self.done <= self.total:
            raise ValueError(f"bar {self.label!r} has an invalid ratio {self.done}/{self.total}")
        if not 0 <= self.amber <= self.total - self.done:
            raise ValueError(f"bar {self.label!r} has an invalid amber segment {self.amber}")

    def ratio(self) -> float:
        return self.done / self.total


@dataclass(frozen=True)
class BoundaryGroup:
    """Tracked open boundaries for one area; the bar length is len(items)."""

    label: str
    items: tuple[str, ...]
    source: str

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError(f"boundary group {self.label!r} has no items")
        if len(set(self.items)) != len(self.items):
            raise ValueError(f"boundary group {self.label!r} repeats an item")

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class TelemetryDashboard:
    """Frozen dashboard payload; the renderer owns geometry, this owns facts."""

    title: str
    subtitle: str
    as_of: str
    latest_evidence: str
    test_count: int
    receipt_count: int
    pills: tuple[KpiPill, ...]
    firmware_groups: tuple[CellGroup, ...]
    pipeline_groups: tuple[CellGroup, ...]
    pipeline_summary: str
    contract_bars: tuple[ContractBar, ...]
    boundaries: tuple[BoundaryGroup, ...]
    legend: tuple[tuple[str, str], ...]
    footnote: str

    def description(self) -> str:
        return (
            f"{DASHBOARD_TITLE}: {self.test_count:,} tests in the recorded suite, "
            f"{self.receipt_count} evidence receipts through {self.latest_evidence}. "
            "Bounded telemetry, not a firmware compatibility percentage."
        )


def status_categories() -> dict[str, "Category"]:
    """Treemap category counters keyed by category name (bounded inventories)."""
    categories: dict[str, Category] = {}
    for panel in get_a6400_project_status_data():
        for category in panel.categories:
            categories[category.name] = category
    return categories


def evidence_record_ids(root: Path | None = None) -> tuple[int, ...]:
    """Numeric IDs of the evidence record files that exist on disk (gaps preserved)."""
    base = root if root is not None else ROOT / "docs" / "evidence"
    ids = []
    for entry in sorted(base.iterdir()):
        match = EVIDENCE_RECORD_RE.match(entry.name)
        if match:
            ids.append(int(match.group(1)))
    return tuple(ids)


def evidence_receipt_count(root: Path | None = None) -> int:
    """How many dated evidence records are actually stored (not the highest ID)."""
    return len(evidence_record_ids(root))


def registered_target_label() -> str:
    """Every target with real evidence, short model names, in registry order."""
    from src.a6400.targets import STATUS_REFERENCE, STATUS_VERIFIED, list_profiles

    names = []
    for profile in list_profiles():
        if profile.status in (STATUS_VERIFIED, STATUS_REFERENCE):
            short = profile.model_name.replace("Sony ", "").split(" (")[0]
            names.append(short)
    if not names:
        raise ValueError("the target registry has no verified or reference profile")
    return " + ".join(names)


def _build_telemetry() -> TelemetryDashboard:
    categories = status_categories()
    phases = categories["task94_phases"]
    plugins = categories["imdb_plugins"]
    display = categories["display_subsys"]
    if plugins.total != 198:
        raise ValueError(f"static-plugin inventory changed ({plugins.total}); update the dashboard copy")
    if phases.total != 8:
        raise ValueError(f"boot task-phase inventory changed ({phases.total}); update the dashboard copy")

    receipt_count = evidence_receipt_count()
    test_count = STATUS_SNAPSHOT.suite.test_count

    pills = (
        KpiPill("contract tests", f"{test_count:,} recorded", "green",
                f"recorded suite: {STATUS_SNAPSHOT.suite.recorded_at}"),
        KpiPill("evidence receipts", f"{receipt_count} recorded", "blue", "docs/evidence/"),
        KpiPill("teardown sequencer", f"{TEARDOWN_PHASES}/{TEARDOWN_PHASES} phases ({TEARDOWN_PHASE_MASK})", "green", "E0353"),
        KpiPill("task phases", f"{phases.done}/{phases.total} observed", "amber", "task94_phases"),
        KpiPill("BIONZ X targets", registered_target_label(), "violet", "src/a6400/targets/"),
    )

    firmware_groups = (
        CellGroup(
            "UIPC / OSAL",
            (CELL_VERIFIED,) * 13 + (CELL_BOUNDARY,),
            "E0310 · E0331",
        ),
        CellGroup(
            "USB / Gadgetcore",
            (
                CELL_VERIFIED,   # MSC soft-disconnect teardown
                CELL_DECODED,    # gadgetcore ioctl dispatch -> FSM map
                CELL_DECODED,    # otgcore descriptor table
                CELL_DECODED,    # /dev/usb/event ABI + bridge
                CELL_DECODED,    # device-node namespace (248:32/33)
                CELL_DECODED,    # module inventory (usbg_video absent)
                CELL_BOUNDARY,   # host enumeration / inbound SET_CONFIG
                CELL_BOUNDARY,   # usbg_video buffer contract (ctx+0x2D)
                CELL_BOUNDARY,   # ZV-E10 overlay declared, non-executable
            ),
            "E0320 · E0323 · E0337 · E0353",
        ),
        CellGroup(
            "DMM / JPEG",
            (
                CELL_VERIFIED,   # P1 foreign GET_JPEG at 29.8 FPS
                CELL_VERIFIED,   # DMM Rule5 / SET_PATH
                CELL_DECODED,    # OSAL queue 0x0167046a
                CELL_DECODED,    # DMM UnitType 0x274
                CELL_BOUNDARY,   # sensor feeder still unwired
            ),
            "E0340 · E0349",
        ),
        CellGroup(
            "GPES / Display",
            (
                CELL_VERIFIED,   # OSD A/B ptrace capture
                CELL_DECODED,    # BAR / IRQ contract map
                CELL_BOUNDARY,   # opt-in engine model (labelled)
                CELL_BOUNDARY,   # keyed 0x820E completion
                CELL_UNMAPPED,   # GPU rasterizer semantics
                CELL_UNMAPPED,   # THREAD_APP / view tier
            ),
            "E0298 · E0313 · E0314",
        ),
        CellGroup(
            "Boot Task Phases (8)",
            (CELL_VERIFIED,) * phases.done + (CELL_UNMAPPED,) * (phases.total - phases.done),
            "task94_phases",
        ),
        CellGroup(
            "Static Plugins (198)",
            (CELL_UNMAPPED,) * plugins.total,
            "imdb_plugins",
        ),
    )

    pipeline_groups = (
        CellGroup(
            "Track A: PTP LiveView & 0x9207 Controls",
            (CELL_VERIFIED,) * 6,
            "E0151 · E0343 · E0349",
        ),
        CellGroup(
            "Teardown 9-Phase Sequencer (0x1ff)",
            (CELL_VERIFIED,) * TEARDOWN_PHASES,
            "E0353",
        ),
        CellGroup(
            "EP0 Class / Probe / Commit",
            (CELL_VERIFIED,) * EP0_CLASS_CASES,
            "E0351",
        ),
        CellGroup(
            "DMM 0x0167046a Frame Source",
            (CELL_VERIFIED,) * 4,
            "E0349",
        ),
        CellGroup(
            "Track B: DirectShow Pin / 0x84 Start",
            (CELL_VERIFIED,) + (CELL_BOUNDARY,) * 2,
            "E0350 · E0354",
        ),
    )

    pipeline_cells = sum(len(group.cells) for group in pipeline_groups)
    pipeline_verified = sum(group.done() for group in pipeline_groups)
    pipeline_boundary = sum(group.counts()[CELL_BOUNDARY] for group in pipeline_groups)
    pipeline_summary = (
        f"{pipeline_cells} pipeline cells · {pipeline_verified} verified · "
        f"{pipeline_boundary} active boundaries"
    )

    contract_bars = (
        ContractBar("USB Soft-Disconnect Teardown", TEARDOWN_PHASES, TEARDOWN_PHASES,
                    f"{TEARDOWN_PHASES}/{TEARDOWN_PHASES} phases ({TEARDOWN_PHASE_MASK})", "E0352 · E0353"),
        ContractBar("PTP LiveView + 0x9207 Controls", 1, 1, "verified · 29.8 FPS", "E0151 · E0349"),
        ContractBar("EP0 UVC Class & Descriptor Audit", TRANSPORT_AUDIT_CHECKS, TRANSPORT_AUDIT_CHECKS,
                    f"{TRANSPORT_AUDIT_CHECKS}/{TRANSPORT_AUDIT_CHECKS} checks", "E0350 · E0351"),
        ContractBar("UIPC / MWF Sync Callback Path", 1, 2, "h4109 reached", "E0332 · E0334", amber=1),
        ContractBar("GPES / SMAPH-S30 Display Model", display.done, display.total,
                    "OSD A/B + BAR/IRQ", "E0298 · E0313 · E0314", amber=1),
        ContractBar("im.elf Boot Task Phases", phases.done, phases.total,
                    f"{phases.done}/{phases.total} phases", "task94_phases"),
    )

    boundaries = tuple(sorted((
        BoundaryGroup(
            "GPES / Display (condvar, 0x820E, OSD map)",
            (
                "guest condvar release",
                "keyed 0x820E completion",
                "OSD A/B mapping",
                "GPU rasterizer semantics",
            ),
            "E0312 · E0314",
        ),
        BoundaryGroup(
            "UIPC / USB FSM (h4109 packet, 0x0A4000)",
            (
                "h4109/h4110 packet payload",
                "0x0A4000 producer",
                "USB FSM transition",
            ),
            "E0332 · E0334 · E0338",
        ),
        BoundaryGroup(
            "Track B UVC (DirectShow pins, WPD MI_00)",
            (
                "DirectShow RenderStream pin connect",
                "composite MI_00 WPD binding",
                "MSC recovery after failed pin connect",
            ),
            "E0350 · E0354",
        ),
        BoundaryGroup(
            "Boot / Runtime (tzset, THREAD_APP, plugins)",
            (
                "fresh-path tzset",
                "THREAD_APP / view tier",
                "198 static plugin inits",
            ),
            "E0302 · E0309 · status data",
        ),
    ), key=lambda group: (-group.count, group.label)))

    return TelemetryDashboard(
        title=DASHBOARD_TITLE,
        subtitle=DASHBOARD_SUBTITLE,
        as_of=STATUS_SNAPSHOT.as_of,
        latest_evidence=STATUS_SNAPSHOT.latest.evidence_id,
        test_count=test_count,
        receipt_count=receipt_count,
        pills=pills,
        firmware_groups=firmware_groups,
        pipeline_groups=pipeline_groups,
        pipeline_summary=pipeline_summary,
        contract_bars=contract_bars,
        boundaries=boundaries,
        legend=tuple((state, CELL_LABELS[state]) for state in CELL_STATES),
        footnote=DASHBOARD_FOOTNOTE,
    )


TELEMETRY = _build_telemetry()


# ===========================================================================
# Daily test suite history (single source of truth for docs/images/test-growth.svg)
# ===========================================================================


@dataclass(frozen=True)
class DailyTestPoint:
    """One calendar day's recorded cumulative suite count and net additions."""

    date: str
    total: int
    delta: int
    evidence_id: str
    milestone: str = ""


DAILY_TEST_HISTORY: tuple[DailyTestPoint, ...] = (
    DailyTestPoint("2026-10-18", 204, 204, "E0022", "ARM loader, UIPC & task94 boot"),
    DailyTestPoint("2026-10-19", 331, 127, "E0041", "DMM/timer ABI & preset backup"),
    DailyTestPoint("2026-10-20", 362, 31, "E0058", "SD DMA/IOCTL & GRM contracts"),
    DailyTestPoint("2026-10-21", 362, 0, "E0058"),
    DailyTestPoint("2026-10-22", 362, 0, "E0058"),
    DailyTestPoint("2026-10-23", 362, 0, "E0058"),
    DailyTestPoint("2026-10-24", 446, 84, "E0066", "GLES kernel binding & backup decoder"),
    DailyTestPoint("2026-10-25", 446, 0, "E0066"),
    DailyTestPoint("2026-10-26", 446, 0, "E0066"),
    DailyTestPoint("2026-10-27", 468, 22, "E0067", "GPIO contract & GLES ownership"),
    DailyTestPoint("2026-10-28", 476, 8, "E0068", "USB buspower module boundary"),
    DailyTestPoint("2026-10-29", 486, 10, "E0070", "Sugilite render loop & surface"),
    DailyTestPoint("2026-10-30", 486, 0, "E0070"),
    DailyTestPoint("2026-10-31", 486, 0, "E0070"),
    DailyTestPoint("2026-11-01", 486, 0, "E0070"),
    DailyTestPoint("2026-11-02", 498, 12, "E0071", "GuestUsbEvent & MediaCommon"),
    DailyTestPoint("2026-11-03", 506, 8, "E0072", "GuestUsbOtgCore & HDMI boundary"),
    DailyTestPoint("2026-11-04", 546, 40, "E0082", "DMP PICA200 rasterizer & CEC/pipe"),
    DailyTestPoint("2026-11-05", 561, 15, "E0095", "Root USB debugger & menu model"),
    DailyTestPoint("2026-11-06", 566, 5, "E0116", "Native row entry & allocator"),
    DailyTestPoint("2026-11-07", 599, 33, "E0150", "Authentic 720p30 CinemaHUD UVC"),
    DailyTestPoint("2026-11-08", 605, 6, "E0204", "Entry bridge & caller drain"),
    DailyTestPoint("2026-11-09", 778, 173, "E0298", "Safe OSD capture & monitor rings"),
    DailyTestPoint("2026-11-10", 881, 103, "E0314", "GPES BAR/IRQ model & UIPC wire"),
    DailyTestPoint("2026-11-11", 1312, 431, "E0369", "Multi-body targets & sync callbacks"),
    DailyTestPoint("2026-11-12", 1510, 198, "E0440", "WinUSB debugger v2 & nflasha dump"),
    DailyTestPoint("2026-11-13", 1727, 217, "E0500", "UC_TLB_VIRTUAL & 13-body map"),
    DailyTestPoint("2026-11-14", 1895, 168, "E0510", "BusyBox ash shell, A5KU & UI boot"),
    DailyTestPoint("2026-11-15", 1903, 8, "E0523", "MTF2 touch mod, guarded Mod Loader & QEMU a6400 boot/SMP"),
    DailyTestPoint("2026-11-16", 1905, 2, "E0524", "Captured QEMU boot inputs, eMMC addressing & qualified LIRO waits"),
    DailyTestPoint("2026-11-17", 1992, 87, "E0577", "QEMU Ydet boundary, mod reload and QEMU mod hot reload"),
    DailyTestPoint("2026-11-18", 2091, 99, "E0578", "Integrated upstream, native Unicorn and guarded USB/GPU research"),
)


def _validate_daily_test_history(
    history: tuple[DailyTestPoint, ...] = DAILY_TEST_HISTORY,
) -> None:
    if not history:
        raise ValueError("DAILY_TEST_HISTORY must not be empty")
    prev_total = 0
    for index, point in enumerate(history):
        if point.total < prev_total:
            raise ValueError(f"DAILY_TEST_HISTORY regressed at {point.date}: {point.total} < {prev_total}")
        expected_delta = point.total - prev_total
        if point.delta != expected_delta:
            raise ValueError(
                f"DAILY_TEST_HISTORY delta mismatch at {point.date}: "
                f"got {point.delta}, expected {expected_delta}"
            )
        if index > 0 and point.date <= history[index - 1].date:
            raise ValueError(f"DAILY_TEST_HISTORY dates not strictly increasing at {point.date}")
        prev_total = point.total
    if history[-1].total != STATUS_SNAPSHOT.suite.test_count:
        raise ValueError(
            f"DAILY_TEST_HISTORY[-1].total ({history[-1].total}) != "
            f"STATUS_SNAPSHOT.suite.test_count ({STATUS_SNAPSHOT.suite.test_count})"
        )
    if history[-1].date != STATUS_SNAPSHOT.suite.recorded_at:
        raise ValueError(
            f"DAILY_TEST_HISTORY[-1].date ({history[-1].date}) != "
            f"STATUS_SNAPSHOT.suite.recorded_at ({STATUS_SNAPSHOT.suite.recorded_at})"
        )


_validate_daily_test_history()
