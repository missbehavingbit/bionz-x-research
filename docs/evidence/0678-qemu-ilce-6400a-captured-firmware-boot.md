# E0678 — QEMU boots captured ILCE-6400A 1.01 firmware to the stock rear-LCD UI and interactive menu

Date: 2026-11-21. **Strictly offline.** Emulates the captured `ILCE-6400A` 1.01 raw partition set
([E0643 §8](0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md#8-read-only-cache-reclamation-atomic-ipc-and-all-17-allowed-partitions))
on the pinned QEMU 11.1.3 `sony-a6400` board (`CXD90045` / `ARM-CXD900X0`). No camera, SD card,
or USB device was accessed, no guest code or ready flag was patched, no cross-model file stand-in
was used (`ULOGIO=` preserves the camera's own captured `/setting/ulogio.bin`), and `emmc.img`
remained byte-identical (`ed231fc0…`) across all runs via disposable `qcow2` overlays.

Labels: `[C]` confirmed by run receipts or verified artifact bytes, `[I]` inference, `[A]` explicit
model assumption.

## 1. Summary of verified results

1. **Four-core Linux 3.0.27 (`8.2.7.6`), 9 mounts, 40 live `.ko` modules, `LIRO`, and `im.elf`
   `SetContentTarget` / `EEStart` complete with zero exceptions `[C]`:**
   In `probe-serial-02` (`status = "completed"`, `9/9` guest commands exit `0`, `0` exceptions),
   the unmodified `ILCE-6400A` 1.01 main kernel (`9d51ab60…`, `Fri Apr 10 15:09:44 JST 2026`)
   activates all 4 Cortex-A5 cores (`ARM-CXD900X0`, `vfpv3d16`), enumerates all 16 user
   `nflasha*` partitions plus `nflashaB0`/`B1` on the 2,040 MiB eMMC backing image, mounts
   `/dev/nflasha7` (`/`), `/dev/nflasha15` (`/usr`), `/dev/nflasha10` (`/tmp`, `/etc`),
   `/dev/nflasha3` (`/system`), `/dev/nflasha2` (`/setting`), `/dev/nflasha11` (`/log`),
   `/dev/nflasha18` (`/lens`), and `/dev/nflasha12` (`/cert`), loads 40 kernel modules
   (`devno` through `cec` plus `wlanMemoryAllocator` across `0x5f000000..0x5f550000`), runs
   `LIRO (liro) rev:4-MUSASHI` through `entry_pre(635c6044)` and `entry_main(635c6058)`,
   initializes `DMP_2D_Initialize : 0` and stella `HeapAllocator`, and completes
   `modelCamera ActionSetInit` (`2,993,213 us`), `modelStillRec InitLOOP` (`3,003,019 us`),
   `SetContentTarget` (`3,094,323 us`), `EEStart` (`3,144,066 us`), and `EV PIN_OPEN`
   (`3,505,330 us`) alongside interactive BusyBox `v1.34.1` `/bin/ash`.
2. **Self-contained WBI1 sub-micro RX frames `[C]` + `[A]`:**
   `ILCE-6400A`'s captured `nflasha5.bin` (`d131b73f…`) stores the three 128-byte SIO ch3
   sub-micro RX slots A/C/B at the same physical window `0x034433A4..0x03443523` as `ILCE-6400`
   ([E0651](0651-qemu-a6400-stock-finder-monitor-rear-lcd.md)), with all three CRC-16/CCITT-FALSE
   checksums valid (`wbi-raw-384.bin` `b4117710…`, Slot A idle `CAkey1 = 01 02`, Slot B TLV `0x03`
   hibernate `00 40 00 02`) `[C]`. Applying the documented E0651 Slot B TLV `0x03 -> 00 00 00 03`
   powered transformation and restamping the CRC produces `wbi-powered-384.bin` (`5effe8d4…`) `[A]`.
3. **Stock rear-LCD shooting OSD, Auto Power Save + S1 wake, and interactive 1.01 menu `[C]`:**
   In `panel-live-03` (`exception_count = 0`), VPH `ch53` double-buffers between `0x23a80000` and
   `0x23b16000` (`640x480` RGBA4444) and VPH `ch51` alternates between `0x14e90000` and
   `0x14fbc000` (`en144 = 0x01000233`). Without an NTSC region-mismatch dialog, the camera boots
   directly to the `P`-mode shooting OSD (`P` + blinking `NO CARD`), enters stock Auto Power Save
   after idle, wakes cleanly on body S1 half-press (`P0.1`), opens `MENU` to `Setup3 (3/7)`,
   navigates across `Setup7 (7/7)` (including 1.01's `Privacy Notice` row) and `Camera Settings1`
   (`Quality/Image Size1 (1/14)` displaying the unit's captured `File Format: RAW`,
   `JPEG Quality: Extra fine`, `JPEG Image Size: S: 6.0M`, `Aspect Ratio: 3:2`).
4. **Experimental live-view `bars` + `SUBMICRO_LENS_POWER=1` boundary isolated `[C]`:**
   With `LIVEVIEW_SOURCE=bars` and `SUBMICRO_LENS_POWER=1` (`ui-capture-02`), VPH `ch51` renders
   the 8-bar / ramp / grille test pattern behind the composited `P` + `NO CARD` OSD (`751ff93f…`,
   `306,717` non-black pixels) and pressing `center` opens `CautionConfig.so`'s modal
   `This operation or setting not available as follows / Focus Mode / Manual Focus` (`169a6937…`),
   but at `~5.77 s` guest time (`panel-live-01`, `panel-live-02`) kernel thread `liro-kliro_101`
   faults at `pc = 0x63909ba8` (`r7 = 0`, vector exception `0x000`) when no `ILCE-6400A`-qualified
   `idt_cam.bin` preload is supplied. Omitting `SUBMICRO_LENS_POWER=1` / `LIVEVIEW_SOURCE=bars`
   keeps `exception_count = 0`.

## 2. Input, tool, and receipt identities

| Artifact / Binary | Bytes | SHA-256 | Notes |
| --- | ---: | --- | --- |
| `boot-manifest.json` | 9,854 | `2e8c6d1adda2fee5abad75952699517c3e70fd4e6bca1b3fcba5e10fce5b5eae` | Verified by `qemu_boot_inputs verify --sources` |
| `emmc.img` (before & after) | 2,147,483,648 | `ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f` | 17 captured `nflasha*` partitions + 1.01 `partinf.conf` SDM header |
| `nflasha3-Image` | 4,117,760 | `9d51ab60df8246f10cb2afb12fb504d7f148fae437ee404f5b9837cc3b8f85ca` | `Linux 3.0.27_nl-rt106+` (`Sony CE Linux 8.2.7.6`, 2026-04-10) |
| `nflasha3-initrd` | 2,183,168 | `fdb5d00794b4c69328d61cc8ff2ee2b6e792ab3159e1f123161b0e53433d1d84` | Captured ext2 initrd (`/sbin/init`, BusyBox `1.34.1`) |
| `av-cam.bin` | 16,822,380 | `3029a174b393fee9b2624d27737c20bc60e71cbbb33736b2a9363b57184ee03d` | Preloaded at `0x035C6000` (`entry_pre = 0x635c6044`, `entry_main = 0x635c6058`) |
| `Backup.bin` | 1,172,696 | `a9bcdb4fbf524af09afd0da387d4fdc9ad952f42ae3e1d1e70d492f01548d2c5` | Preloaded at `0x02E80000` |
| `ulogio.bin` (in `nflasha2`) | 3,772 | `a7b1aa1919c3f3273baa1e4a9ece71c620e0d755b9d9c197e49be74c4a790a44` | Camera's own `/setting/ulogio.bin` (`ULOGIO=`) |
| `nflasha5.bin` (WBI1) | 62,914,560 | `d131b73f9c4610bdc3f549a65e94d70c11cc92d3071b878d65ef46a8fe13b3b6` | Source of `0x034433A4..0x03443523` sub-micro RX slots |
| `wbi-raw-384.bin` | 384 | `b41177102c16d5189d148b72f69f9ef5f257d5aff165f8b82694512e8a50bd7d` | Unmodified WBI1 sub-micro RX frames (3/3 CRCs valid) |
| `wbi-powered-384.bin` | 384 | `5effe8d44b670f71fd7b537e44276718b57f072e48a10be45e621cb73c207695` | Slot B TLV `0x03` powered (`00 00 00 03`) + restamped CRC |
| `qemu-system-arm` (`11.1.3`) | — | `10d67b878a7250294c37015062c31ba5e6b50797e0b2e2096510acab8a33907f` | Built from pinned commit `005c61c0ef6f` + `sony-a6400` patch (`ea4def47…`), `tests.test_qemu_emmc` `9/9 PASS` |
| `probe-serial-02/console.log` | — | `a44c9f098c8d221f74c2d249f4dd44aef21794a5ec8f89d02285b62fbb7163ed` | `95.175 s` bounded probe (`9/9` guest commands exit `0`, `0` exceptions) |

Captured PNG receipts under `artifacts/qemu-a6400a/boot-20261121/`:

- `panel-stock-captures/01_shooting.png`: `1485c676f00770765412cd0a320411bfcfe910892bdb50742789bfbabb7c6f69` (`P` OSD)
- `panel-stock-captures/02_shooting_disp.png`: `488451976bf0295a2bab4b6311e178f8a3cf7caebe4f2f8397e4ca65d0f89137` (`P` + `NO CARD` OSD)
- `panel-stock-captures/03_menu_open.png`: `861e9fc5d990bfea5318871e3349d962993032647190b3ca9d4bac211b27528f` (`Setup3` `3/7`)
- `panel-stock-captures/04_setup7.png`: `708bfb19efc5e183f2a9d513e758553c414795fa1d4e68ebbb79313375969c11` (`Setup7` `7/7`, `Privacy Notice`)
- `panel-stock-captures/05_setup7_version_selected.png`: `f02aa8734018a2e0e6aa8405829ec613a10431866736107113ef08861c648cf9` (`Version` selected)
- `panel-stock-captures/08_after_down.png`: `0fe57ad657106a72442949559d95bbb9fa2b7e3d3c1f4233ce5b6fc6c1092eb7` (`Setting Reset` selected)
- `panel-stock-captures/09_camera_settings1.png`: `3d5a57794fe34569b4f1b8b4cb542f0bc7493cc0154fae4ae322fdc9af50b131` (`Quality/Image Size1` `1/14`)
- `ui-capture-02/01_first_ui_screen.png`: `751ff93fa98a78ca802b151346e7bdfe9385f2f276e137b0b194ec97f3aa3016` (`P` + `NO CARD` over `bars`)
- `ui-capture-02/02_after_center.png`: `169a6937ef3722c9b4de5c25cb2abddfeae02161013f598b04ef03039a8a951c` (`Focus Mode / Manual Focus` caution over `bars`)

All intermediate and diagnostic receipts (`probe-serial-01`, `ui-capture-01`..`03`,
`panel-live-01`..`03`) are preserved unmodified under `artifacts/qemu-a6400a/boot-20261121/`.

## 3. Reproduction (WSL2)

From the repository root inside WSL2, after staging inputs to Linux ext4
(`~/a6400-inputs/a6400a-v101-captured`) and extracting `wbi-powered-384.bin`:

```bash
ULOGIO= \
EVSEL_LATE_US=500 \
SUBMICRO_FRAMES="$HOME/a6400-inputs/submicro-a6400a/wbi-powered-384.bin" \
SUBMICRO_FRAMES_SHA=5effe8d44b670f71fd7b537e44276718b57f072e48a10be45e621cb73c207695 \
python3 -u qemu-a6400/tools/rear_panel.py \
  --port 8641 \
  --inputs "$HOME/a6400-inputs/a6400a-v101-captured" \
  --log artifacts/qemu-a6400a/NEW-PANEL.log
```

Open `http://127.0.0.1:8641/` in a browser to view the live `ILCE-6400A` 1.01 LCD and operate the
rear controls (press **S1 / Half-press shutter** to wake the screen if Auto Power Save blanks it).

## 4. Retained assumptions and next boundaries

- **Bootloader handoff and WBI sub-micro fixture `[A]`:** Preloading `Backup.bin` (`0x02E80000`),
  `av-cam.bin` (`0x035C6000`), and `kemco` (`0x00103000`) plus replaying the WBI-derived powered
  Slot B frame (`5effe8d4…`) models bootloader and sub-micro state rather than executing `DGU5` or
  a full sub-micro microcontroller firmware.
- **`Setup7 -> Version` modal and `bars` + `SUBMICRO_LENS_POWER=1` `0x63909ba8` fault:** Pressing
  `center` on `Setup7 -> Version` does not open the version modal in the default no-rail profile
  (while menu navigation remains live), and enabling `SUBMICRO_LENS_POWER=1` +
  `LIVEVIEW_SOURCE=bars` without qualifying `IDT_CAM` (`sabin/idt_cam.bin`, `d21e2c0b…`) for
  `ILCE-6400A` 1.01 triggers a null-pointer write at `0x63909ba8` in `liro-kliro_101` at `~5.77 s`
  guest time. Qualifying `ILCE-6400A` `IDT_CAM` and tracing `0x63909ba8` in `av-cam.bin`
  (`3029a174…`) are the next steps for sustained live-view `bars` on `ILCE-6400A`.
- **Target registry tier:** `ILCE_6400A` remains `reference` in `src/a6400/targets/` until
  `UnicornMachine` / `InterpreterProbe` target-input selection is wired cleanly.
