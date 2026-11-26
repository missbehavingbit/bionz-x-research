# E0650 - Virtual SD hotplug and an offline USB Mass Storage baseline

Indexed: 2026-11-19.

Date: 2026-11-18. No camera, physical SD, or USB passthrough. The preserved
eMMC image was only used as the backing for disposable guest overlays.

## Result

| Gate | Result |
| --- | --- |
| Removable SD controller | **Pass, native QEMU test:** empty, insert, remove, and fresh-process reinsert; SD and eMMC each returned their own sector. |
| SD persistence | **Pass at block level:** CMD24 through 32-bit ADMA2 completed; CMD17 returned the changed sector after a QEMU restart. The eMMC SHA-256 did not change. |
| Sony playback | **Blocked in the tested boot:** QEMU reported the card present, but original firmware did not register the SD partitions or send an SD command. The displayed pixels did not show the supplied JPEG. |
| Sony-originated capture | **Not run:** fresh live frames are not established in this replay. The new-file JPEG verifier was tested with constructed files only. |
| USB Mass Storage | **Pass on a separate fixture:** virtual connect/reset, descriptors, address and configuration, BOT readiness/sense, capacity, one sector read, and disconnect. |
| Sony USB gadget | **Not enumerated:** the Sony kernel registered `dwc3.0`, but no DWC3/VBUS/ID interrupt was observed and the board lacks a device-mode link to a virtual host. No UVC or PTP liveview was attempted. |

## Controller identity and input handling

`mmc_drv.ko` from the a6400 update package
(`b150c666ee29567e920e156b8a5def8307d04c51b799956cdd74fcf5c55211cf`)
maps `0xF2A00000` in `mmc_hw_init` (`.text+0xC964`), but that is the
common window, not the per-slot SD host. `mmc_hw_open` reads the slot
pointer at `.text+0xE104` and stores it at `.text+0xE10C`.
`mmc_os_register_int_handler` at `.text+0x1BF4` to `0x1C0C` indexes the
`udif_devices+0xDC` group with stride `0x5C` and passes its IRQ record
at `+0x18` to `__udif_request_irq`. The decoded captured main kernel's
`__ksymtab_udif_devices` at `0x60477918` points to `0x604D9084`;
the group is at `0x604DA01C`. Its records are:

| Resource | Register base | GIC interrupt |
| --- | --- | --- |
| `mmc0` (SD, minor 10/11) | `0xF0300000` | 218 |
| `mmc1` (SDIO, not modeled here) | `0xF0301000` | 221 |
| `mmc2` (unchanged) | `0xF0302000` | 224 |
| `emmc0` (internal) | `0xF0304000` | 227 |

The new model uses the standard SDHCI and SD card, with provisional
3.3 V/ADMA2 capabilities. It only creates MMC0 when the named backend is
present. The launcher checks an explicitly designated raw file, refuses
the firmware input and disposable run directories, and starts the SD slot
empty. The operator inserts the already opened image through local QMP;
the media operation takes no host file path. No Sony GPIO card-detect or
vendor register behavior is inferred from this standard-controller test.

## Original firmware attempts

The stock-display run used the captured E0524 inputs with
`SUBMICRO_PAGE=` and `ULOGIO=` (the qualified E0527 page and E0525
stand-in were unavailable), `REAR_CONTROLS=1`, `REAR_LCD_DIAGNOSTIC=0`,
`EVSEL_LATE_US=500` and `-icount shift=0,sleep=on`. A 64 MiB image
with a FAT16 MBR partition at sector 2048 contained a constructed
640x480 JPEG at `DCIM/100MSDCF/DSC00001.JPG`. Its SHA-256 was
`c5695c86b73dd26d25b2ff5c64ca1085afac931cdf9cfff55578ba71fba6a1aa`.

After the guest's `Created HeapAllocator:` marker, the run sampled
empty, sent Play (QMP qcode `p`, hold 250 ms), inserted the card by
QMP, sent Play again, and ejected. SDHCI present-state read
`0x01FA0000` empty/ejected and `0x01FF0000` inserted. The two
frame digests did not change across the five samples. The trace has
4,576 `sdcard_normal_command` records, all eMMC. `/proc/partitions`
had no `mmca10` or `mmca10p1`, although `/dev/mmca*` nodes existed.
The requested Play input is not proof of guest receipt: it uses the
sub-micro path and this run omitted the replay page. A second attempt
staged a small ARM probe for `ioctl(0x01)` on `/dev/mmca-char`.
It acknowledged decoding the helper but not the mount command before
the observation bound; the trace again contained only eMMC commands.
This second attempt does not establish an ioctl return or driver error.
Both SD copies retained the input hash
`12364e0b83b6d92ebeac4ea3b50cb7951dc03550799596e6b585ab8efd90e2b2`.

The offline USB control used the same QEMU binary on `orangepi-pc`
without executing guest CPUs (`qtest` accelerator). A read-only disk fixture was
attached to the OHCI root port with QMP. The host read the original QEMU
device descriptors (`46F4:0001`, MSC/SCSI/BOT), checked bulk endpoints,
handled Unit Attention with REQUEST SENSE, verified READ CAPACITY(10)
and compared the READ(10) data with an independent fixture sector.
Both BOT status and the sector comparison were required. This is not a
Sony VID/PID, firmware gadget, or camera USB mode.

An independent Sony replay used `qemu_boot_probe --seconds 65
--wait-for "Created HeapAllocator:"`. `ls -l /sys/class/udc` showed
`dwc3.0`; its platform `uevent` reported `DRIVER=dwc3`. The sampled IRQ
counts for 229 (`dwc3`), 230 (`fusb_usb_vbus`) and 231 (`fusb_usb_id`)
were all zero. Driver registration alone does not demonstrate a cable
or enumerate a device; upstream QEMU's DWC3 model is host-side.

## Reproduction and identities

Use the [scenario instructions](../../qemu-a6400/README.md#offline-sd-and-usb-scenarios)
with a newly built overlay. QEMU source `005c61c0ef6f6ae615283440b4bb7be51b2c6ddd`,
binary `9f7021132009a374d0f9b96e3813a440953cfdb6892004766f0af3f838ab236b`.
The guest main `Image` was
`d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618`,
validated by prepared manifest
`68ff406b8279eeae8c9f9ae2647de3a201d89000df875dba40719ec5e866a6b5`.
Prepared eMMC before and after:
`18e37ec3b3e56d26d31c0b0a4fd91ff10d0d1cb8a23308e49c35abd5cd3cc94b`.

- WSL `python3 -m unittest tests.test_qemu_media.QemuRemovableSdTests
  tests.test_qemu_media.QemuUsbMassStorageTests tests.test_qemu_emmc -v`:
  11 passed on the private build.
- Windows `.\venv\Scripts\python.exe -m unittest discover -s tests -q`:
  Final run collected 2,099 tests, with 105 skips and no failures or errors.
  The earlier status-card sync failures were fixed by regenerating the
  status assets and updating the snapshot.
- Local receipts in `artifacts/qemu-a6400/media-stock-playback-01/`,
  `media-original-sd-ioctl-02/`, and `media-sony-usb-03/`; full Python
  test receipt at `artifacts/tests/qemu-media-full-3-20261118.log`.

## Follow-up: card present from reset and original device admission

The boot observer now supports `--sd-at-boot`. It starts the CPUs paused,
inserts the launcher-qualified image through QMP, records its starting
digest, then releases the CPUs. A repeat with the original kernel and
initrd, the same base eMMC and a fresh overlay used prepared manifest
`bf12cfa60d64560d6db6327e42e81fd5d81b96c883c90879228ed67364cdde6d`.
No matching E0527 page or RX extract was found in the local emulator
receipts; these runs still used `SUBMICRO_PAGE=` and `ULOGIO=`.

Two independent 115-second boot observations staged small ARM helpers
under the disposable guest `/tmp` through paced serial commands. Helper
output records the raw syscall result separately from the exit status
of the probe:

| Observation | Result |
| --- | --- |
| SD already present at boot, `open("/dev/mmca", 0x90002)` | `-6` (`ENXIO`). The block node was not usable, so the `0x10e` mount ioctl was not issued. No `mmca10`/`mmca10p1` was registered. All 4,951 card commands were eMMC, none SD. SD and base eMMC hashes were unchanged. |
| Sony USB, `open("/dev/usb/otgcore", O_RDWR)` | `-16` (`EBUSY`). No `SET_MODE` was issued. The UDC state and speed were `not attached` and `UNKNOWN` both before and after the probe; IRQs 229-231 remained at zero. |

The SD diagnostic used the original open flags and the `0x74`-byte
`0x10e` request layout from [E0058](0058-sd-read-ioctl-and-preparation-state.md).
The original packaged `mcmn_drv_mmc_mount4sd` at `0x12EF8` sets request
byte 0 and status word `+4` to zero, selects command `0x10e` at `0x12F10`
and calls `ioctl` at `0x12F1A`. This does not imply the guest completed
that call. The replay helpers and invocations are retained under
`artifacts/qemu-a6400/media-native-result-probe.c` and
`media-native-pipe-probe.py`; use receipts `media-pipe-sd-09/` and
`media-pipe-otg-09/`. Earlier attempts in `media-sd-preinsert-04/`,
`media-old-otg-05/`, `media-initrd-*-06/` and `media-pipe-*-08/` retain
the transport and setup failures; they are not successful mounts or
mode changes.

The new playback comparison independently rejected both Sony QEMU heads
from the original hotplug run. With a fixed full-frame comparison against
the high-contrast fixture, worst-channel mean absolute error was
127.52 on head 0 and 127.51 on head 1, both before and after insertion
(acceptance limit 12). Unit controls pass for a matching image and
reject an OSD-only change or a matching empty-card screen.

Next: recover the qualified sub-micro input and trace what causes the
original SD driver to admit the card. Once that path is usable and fresh
live frames are verified, require a new decodable firmware capture. Separately,
the Sony DWC3 device and OTG cable path still need an original gadget
enumeration and basic MSC or PTP read before streaming.

## Follow-up: native SD card-detect status, still no original mount

The captured `mcmn_drv.ko`
(`1c65f07dad20e3b77ea8cb2e9807d4c3198610382cc4f6b9a0faabd336fa5b4f`)
matches the packaged driver's instructions for this path. For SD0
(`0x20` and `0x101`), `mcmnd_core_getmmccddata` maps `0xF2915010`,
selects bit 24 at `.text+0x74C`, and loads the register at `+0x784`.
The SD0 `.rodata+0x3C` relocation selects this callback; at
`.text+0x5E8–0x5FC`, zero becomes inserted media type 3. This is
MISCREG `MODEREAD` ([E0643](0643-cxd90045-miscreg-efuse-and-a6400a-jiritsu-chunk-planner.md)),
not a CXD GPIO port. The trial that drove GPIO P5.24 did not change it.

The opt-in SD slot now returns bit 24 from the same removable card as
MMC0 (`1` empty, `0` inserted). No other chip/mode strap or second slot
is modeled by this change. The native QEMU test requires the bit to
agree with the card through attach, eject and a fresh process, while
also verifying SD sector persistence and unchanged eMMC.

| Original firmware test (powered sub-micro replay, fresh overlay) | Final sequencer | SD commands | Result |
| --- | --- | --- | --- |
| Captured ORIL/settings, insert over QMP | step 26 | 0 (5,349 eMMC commands) | `MODEREAD` `0x01000000 -> 0`, SDHCI `0x01FA0000 -> 0x01FF0000`; Play showed `No memory card. Cannot play.` |
| Captured ORIL/settings, SD at reset | step 26 | 0 (5,284 eMMC commands) | Present from reset; independent S1 then S2 trial left the SD image bit-identical. |
| Reconstructed packaged boot with bars, SD at reset | step 42 | 0 (5,227 eMMC commands) | Play showed the same no-card dialog. Later S1/S2 stimuli produced no SD write; a return to the shooting view was not verified. |

All three used QEMU `0a9b0834…` with `-icount shift=0,sleep=on -net none
-no-reboot` and the existing synthetic live-view option; all quit with
exit 0. None played the seeded JPEG or created a photo to reopen on a
restart. Clearing the sequencer by itself is not enough to admit SD.
The next original boundary is media admission: `mcmnd_rm_init`
(`.text+0x3BC8`) registers its front-media callback on queue `0x0028017C`.
Captured av-cam's `0x63A7554A` sends that queue a 7-byte message. A
runtime match to the SD detector and its sub-micro/status inputs remains
open; the emulator does not synthesize the queue or report mount success.

Receipts are in `artifacts/qemu-a6400/bringup-sd-moderead-01/`,
`bringup-sd-present-at-reset-01/` and `bringup-sd-beyond-startup-01/`.

## Follow-up: stock firmware mounts SD (2026-11-19)

The MODEREAD-only configuration above did not notify the media driver on
hotplug. Captured av-cam `0x63A755C6`/`0x63A755D2` reads sub-micro slot A
byte 2 bit 4 into the front-media state. Updating that bit from the same
removable card as MMC0 and recomputing the frame CRC changed the stock
report state from `0` to `2` on insertion. The native kernel callback
received the original queue message and requested a probe of `0x101`.

The probe also checks Memory Stick detect: `mcmn_drv.ko` reads MODEREAD
bit 16 active low on this shared slot. Returning zero there made the
driver see Memory Stick as present. With bit 16 high (no Memory Stick),
MODEREAD is `0x01010000` empty and `0x00010000` inserted for a writable
SD card. The opt-in slot also reflects SD write-protect at bit 25.
No guest message, device registration, or mount result is injected.

The final hotplug run (`bringup-stock-sd-shared-slot-01`) registers
`mmca10` and `mmca10p1` and completes `MMCD_IOCTL_MOUNT`. The captured
cold-insert replay (`bringup-stock-play-captured-02`) records both
`FsysMount /dev/mmca` entry/exit and the still-media load completion. The
captured cold-insert, packaged cold-insert
(`bringup-stock-play-capture-packaged-02`), and final hotplug runs each
record 39 SD normal commands and no SD CMD24/CMD25. They use QEMU
`7f90555050c7123901523611c0155c213dd634eab9030d6a3678466d0f3814fb`,
the same powered fixture and icount policy, and separate copies of the
64 MiB FAT16 card (`12364e0b83b6d92ebeac4ea3b50cb7951dc03550799596e6b585ab8efd90e2b2`).
All three cards are byte-identical to their inputs after exit.

**Mount is not playback or capture.** The cold-insert replays do not
display the seeded JPEG or create a new file. The last logged SD
multi-block read starts at `0x124800`; the seeded JPEG begins at
`0x125800`. The command trace alone does not establish that the JPEG
bytes were read. A separate boot-empty/late-insert packaged run
(`bringup-sd-clean-shooting-01`) shows the stock `Accessing...` dialog
on Play, but never completes card initialization in that run and issues
no SD commands. Trace the still-media list and original decode/scanout
path after a confirmed mount; establish the still-release and file-write
path independently.

### After the captured step-26 fix

With the request-following lens rail, captured ORIL reaches step 42
with either the cold-insert JFIF card or a separate synthetic Exif 2.31
fixture (640 x 480, with an embedded thumbnail).
`bringup-sd-captured-{rail,dcf}-01` use per-block tracing: an SD CMD18
at `0x124800` includes the fixture sectors starting at `0x125800`.
The card read is real; it can include filesystem read-ahead and does
not prove decoder consumption. Both cards retain their input hashes
with zero SD CMD24/CMD25; neither stock display shows the photo.
The Exif image SHA-256 is
`3b6e3730e4178adae3f4a81bb21a567cd30c501c8fe2577093c3439702e1220d`.
The stock playback screen fails the fixture comparator (maximum mean
channel error 119.42, limit 12).

Another replay (`bringup-still-api-trace-01`) completes `FsysMount`
and `EV CMP LOAD STILL`; the mode trace then records `Mt F00_010_libObj.so`
entry/exit and `Mi F00_010_libObj.so` entry without exit in the sample.
The passive observer matches original `libObj` initialization sites,
but records no hits at the sampled `libmpr` still-entry or single-image
play sites. The missing transition is before demonstrated decode,
not evidence that a JPEG accelerator alone would fix playback.
The paired phase-only observation (`bringup-stock-play-phase-01`) also
reads the card and stays black on both stock Play and MENU.

Live hotplug remains timing-sensitive. In
`bringup-stock-hot-dcf-mmio-01`, the earlier insert reaches the native
SDHCI registers, completes the mount, and shows the stock card icon in
shooting. Play subsequently blanks the display without showing the
fixture. A later insert (`bringup-stock-hot-dcf-01`) instead stops at
the original MMC mount ioctl before any SD commands and shows
`Accessing...`. These are distinct from a successful photo display.

The original `Shutter/SteadyShot` menu confirms `Silent Shooting: On`
and `Release w/o Lens: Enable` in the captured profile
(`bringup-stock-lensless-setting-01`). In a separately mounted shooting
trial (`bringup-still-hot-shoot-01`), S1 then S2 yield two native
interrupts each (`liro-122`, `liro-142`). The stock display remains in
shooting view. The sampled still encode and write entries are not
reached, there are no SD writes, and the card hash stays
`581066b0588df7ffc68836143e0d090eb1282d03625648f375901c311498631a`.
No new guest file exists to reopen. Localize original release admission
before modeling an encoder; do not generate or write the photo on the
host.

In the cold-card wait (`bringup-stock-mode-context-01`), bounded original
thread snapshots resolve to polling or receive paths: `Appframework`
returns from `poll` at `libObj+0x843914`; `I-InputMain` waits at the
`osal_rcv_msg_tmo` call ending at `libObj+0x861F08`; `MprIf` is in the
condition wait leading back to the worker loop at `libmpr+0x50BB02`.
These samples do not locate the missing mode-init completion or justify
stubbed success. The next comparison needs the actual outstanding
request and its original completion producer.

The separate `bringup-body-dispatch-01` trace observes S1 `0x1F1`
and S2 `0xA7` at the original `I-InputMain` dispatch/send sites,
so absence of a full-press event at the input boundary is not the
explanation in that run. It did not complete SD mount, and cannot
replace the independently mounted shooting trial.

An unassisted Play-to-shoot attempt
(`bringup-stock-play-resume-capture-01`) left the no-card dialog
unchanged after S1. Insertion then showed `Accessing...`; the sampled
mount ioctl never completed and the trace contains no SD commands.
The stock readiness-clear site was not observed. This is not a
shooting-mode or recording test on an admitted card.

### Browser initialization: original topology completion is queued

The captured `libObj.so` (`e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1`)
maps `@M010` to `ModelBrowserToInstance` (`+0x429AE4`). The first passive
lifecycle trace (`bringup-browser-lifecycle-01`) identifies the wait as
browser re-initialization, not a blocking decoder call. On the same browser
object, the first `Mi` runs commands `0, 8, 0x24, 1, 0x25` and ends;
the later `Mt` runs commands `2, 9, 0x27` and ends, but the second `Mi`
runs `0, 8, 0x24` without `1, 0x25` or a matching end marker.
`Tc 05E` and `Tc 0E5` identify `modelRepair` and `modelDlnaSlideshow`
in the native model catalog; their order is not proof of cause.

`bringup-browser-events-01` reproduces the wait with passive 48-byte
instruction signatures (`observe_still.c` and `prepare_still_trace.py`
in `artifacts/qemu-a6400/first-liveview-validation`). It uses QEMU
`ab62eff98b068f155ca3bc812d8650301c6dee638523725b6104dd77399ec50c`,
observer `73657bea1c4ae13efdedf6d2c00aaa9e48373dde25e60c4911150cc1abf73fe0`,
the checked captured inputs, the powered sub-micro fixture with lens rail
reply, bars, stock LCD and a disposable copy of the Exif card inserted
at reset. With `-icount shift=0,sleep=on -net none -no-reboot`, wait for
step 42, press center, sample the LCD after 100 display ticks, press Play,
and sample after another 155 and 180 ticks. QMP quit returned exit 0.
The three rear-LCD samples are black; the Exif card hash is unchanged.

| Original Thumb site in `libObj.so` | First `Mi` | Second `Mi` |
|---|---|---|
| `+0x42D510`, `+0x42D73E` | `0x11004005` moves browser state `0 -> 1` | Same event moves state `4 -> 1` after teardown |
| `+0x429D8E`, `+0x398DE4` | Command `8` requests `0x12010102`; topology state/direction `0 / 0x10` | Same request; state/direction `2 / 0x11` |
| `+0x398CE0` or `+0x398E22` | Original immediate branch runs for the request | Original queue branch runs instead |
| `+0x42D510`, `+0x42D73E` | Browser receives normalized `0x102`; state `1 -> 2`, command `1` | No `0x102` or command `1` in the sample |

The second request is present; its original completion is not observed.
This narrows the next boundary to the topology manager's pending
operation and queue drain, not card detection or the JPEG decoder. The
observer again records zero hits at the sampled `libmpr` still-entry,
play, parse, encode and write entries. The four captured Sgc1 stop/latch
sites (`0x6376A766`, `0x6376A7DA`, `0x6376A80C`, `0x6376A84A`) also
have no hits. That refutes attributing this sample to a busy wait at
those sites; it does not exclude other camera stop paths. No guest code
or application result was changed.

The no-photo control (`bringup-browser-no-photo-01`) uses the same
FAT16 layout and DCIM directories but has no live JPEG entry. Its
card SHA-256 stays
`12058f8c42c4d8cd1ef11bdff71546ab0bd7e0a043dfe37a099fa20d16e7eb9d`.
The original kernel registers `mmca10p1`. The rear panel is again
black before and after Play, and the same browser request queues
in state/direction `2 / 0x11`. The topology manager is still tracking
original destroy request `0x1200F06C`, sent by `ModelStillRec` at
`libObj+0x703A44`. The original retry timer is not missing: the
one-second submission returns zero and subsequent `0x16060013` events
continue to run with that same destroy request pending. The next check
is the MWF object status returned to the destroy path, not a synthetic
timeout or completion. This sample did not validate the application
file-service mount separately.

`bringup-still-owner-01` repeats the seeded-card replay and confirms
the original `FsysMount /dev/mmca` and `EV CMP LOAD STILL` completions.
At `libObj+0x395C68`, the original destroy path reads local object ID
`1` with MWF status `0`; the comparison requires `1` to advance.
The original retry path repeatedly emits `0x08020109`. The bitfield
stores at `+0x397EDC`–`+0x397EF2` and the call at `+0x39B450` identify
this as an active count of two for pin `9` on object type `8`
(side parameter `1`, instance parameter `0`). The original recorder
constructor runs once; its destructor is not called in the sample.
The observer uses shortened exact signatures where the ELF relocation
table marks nearby literal words; older zero counts at those locations
are not evidence of unexecuted code. No JPEG API or buffer reply was
injected. QEMU exited normally and the card was unchanged.

`bringup-still-pin-owner-01` ties that active count to the actual
objects using captured `libMWF.so`
(`0903f8f9257e71b1c6bfd76bc74863c686d5a6d13d2b982cccae63b4cd43a7b2`).
`ObjCamera` pin `9` connects to `ObjStillRecorder` pin `9`. The camera
end retains two active references across all 23 later samples;
the recorder end has zero. The two native reference increments are
at `libObj+0xA6F898` and `+0xA6F800`; neither is balanced by a
decrement in this run. `libMWF+0x2BE7C` increments for status argument
`2` and decrements for `1` when the pin has a status configuration.
No observed `ObjIf::StopObject` or recorder destruction follows.

`bringup-camera-stop-request-01` confirms original category `0x3100`
requests `0x7540` (`SET_STILL_FORMAT_CMD`) and `0x7500` (`EE_START_CMD`)
at `libObj+0xA6F6E4`, and later `STILL_CAPTURE_STOP_CMD` (`0x750C`)
submissions. The original message names come from the table read by
`+0xA0BB6E` (ELF data load segment has a `0x8000` VA/file delta).
The sampled response handler (`+0xA7444E`) receives other responses
but not `0x7541` or `0x7501`, which have branches that release the same
camera pin. Neither clearing the pin count in the host nor injecting
a topology completion would prove the missing contract.

The missing second-stage still-format dispatch is not a missing
`0x4036` camera transport reply. `bringup-still-format-transport-01`
observes the original sequence send `0x4036` on queue `0x00030001`,
then consume the original response with status `0xF000`.
`bringup-format-relay-dispatch-01` follows that sequence's translated
`0x3130/0x7541` through the original MWF send, receive callback and
dequeue, but does not see it at `ObjCamera` dispatch (`+0xA7323A`).
`0x3130/0x7515` reaches that same dispatch from the same receiver in
the same run. The card still mounts and the LCD is black. An
empty-slot comparison also observes the still-format relay returning
zero, without a pin decrement. These results rule out a missing
transport acknowledgement for `SET_STILL_FORMAT_CMD`; they leave the
camera's later dispatch and live-view completion unqualified.

`bringup-ee-completion-01` measures the later dispatch gate. Original
`0x3130/0xA03B` closes it at `libObj+0xA75942`; the dequeued format
reply reaches `+0xA75AE6` with the gate zero and is deferred. Its earlier
`0x7515` comparison passes with the gate one. The native reopen site
`+0xA75A00` is not executed. The original EE sequence sends the
`0x3003` packet successfully on `0x00030001`, but its response handler
does not run in the sample.

`bringup-ee-oril-01` follows that exact request into captured
ORIL `0x63D4B422`. `bringup-ee-oril-state-01` records generation `1`,
the pending word `0x3003` written at `0x638F6FF8`, and the matching
frame worker applying its lookup and consuming that word at
`0x638F7082`/`0x638F70A4`. It then sets the stage byte at `0x644AC6FA`;
the later state-clear site has no hits. The local Ghidra 12.2
`ARM:LE:32:v7` bounded Thumb receipt under
`artifacts/analysis/ee-start-oril-01` uses the captured ORIL
`dd8f8d0c…`, VA base `0x635C6000`. The request, acceptance and
comparison sites were independently checked against the instructions.

The captured recipe begins at `0x63E97B7C` and its last entry at
index 42 is the wait word `0x897F`, which includes module 1.
In both the paused pre-Play and post-Play snapshots of
`bringup-ee-oril-state-01`, the thirteen request/report pairs at
`0x6434CDA8+0x18/+0x32` differ only at module 1:
request `0x0102`, report `0x0118`. Thus **reaching** step 42 does
not prove **completing** it. This is a later gate, not a recurrence
of captured step 26 or the older packaged step-32 wait. This control
led to the module-1 prerequisite below, not an injected `EE_START_CMP`.

The original `0x0102` case at `0x63908172` passes the sampled frame
number and phase checks. It calls `0x63902008(22)` and sees zero at
`0x63908180`, then the producer at `0x6393276A` publishes `0x0118`.
In `bringup-step42-job22-01`, all 309 such returns have both busy-mask
words at `0x640EB838` zero, bytes `+4/+5` zero and halfword `+2` zero
in job record `0x6435A848` (table `0x6435A378`, stride `0x38`). The
original predicate requires `+5 != 0` and `+4 == 0` after checking
the mask. The zero result is not a sampled busy-mask obstruction.

The captured diagnostic table names jobs 22 and 26 `IDT_OPD` and
`IDT_TOP`. `bringup-step42-job22-reserve-01` completes the file-service
mount and observes original requests for job 22 at `0x63902C7C`.
The case at `0x639036E4` makes a nested request for job 26; the
downstream OPD configuration entry `0x63B488A8` has no hits. The
follow-up `bringup-step42-idt-top-01` separates admission from
retirement:

| Original site | Passive observation |
| --- | --- |
| `0x63902D06` | Window/busy check returns zero (admitted to the next check) for all 226 job-22 requests and 452 job-26 requests. |
| `0x63B50B92` / `0x639036B4` | On all 452 job-26 attempts, the readiness word at `0x643EF448` reads zero instead of one and the original transition returns `4`. Neither its semaphore call nor its state-table transition is reached. |
| `0x63903714` | All 226 nested job-26 calls return zero to the job-22 case, which exits without configuring OPD. The sampled job-22 and job-26 active-byte set and clear stores do not execute. |

These are requests rejected **before** OPD configuration, not observed
OPD completions being lost. The first initializer lead is original
`0x638FCF08` in the camera init body, calling `0x63B50A5C`; its store
to `0x643EF448` at `0x63B50A88` follows a check of the first halfword
returned by the original `0x635DD8BC` provider. Do not write the flag,
report a job active, or add an OPD interrupt based on this evidence.

These three passive comparisons used QEMU `ab62eff98b06…`, captured
ORIL `dd8f8d0c…`, the powered WBI fixture `3385ec34…`, request-following
lens rail, bars, the stock LCD route, `EVSEL_LATE_US=500`, and
`-icount shift=0,sleep=on -net none -no-reboot`. The declared Exif card
was inserted at reset, copied per run, and remained byte-identical.
They took the step-42, center, 100 ticks, Play, 140 ticks sequence
through `bringup_observe.py`; all exited via QMP with code zero.
`bringup-step42-idt-top-01` did not log a completed file-service
mount; the mounted reports are the first two runs. Ghidra 12.2
`ARM:LE:32:v7` completed five bounded queries with unchanged input
hashes in `artifacts/analysis/idt-top-admission-01`. The job dispatch,
initializer, and recorded return values were checked against original
instructions rather than relying on decompilation alone.

#### The original IDT sidecar releases the captured step-42 predicate

`bringup-step42-idt-init-01` shows that Task 65 reaches `0x63B50A5C`.
The original reader at `0x63B50A3E` sees zeros at VA `0x80152000`;
`0x63B50A46` tests for header halfword `0x003D` and `0x63B50A88`
stores zero to the readiness word. This is not a dormant task in the
current profile. The observed hand-off query `0x00FA0074` returns
`0x20152000` to original kernel translator `0x6011F130`, whose ARM
path at `0x6011F1D8` adds `0x60000000`. Neither the captured nor the
packaged WBI spans that physical address.

The captured `nflasha3` FAT image, SHA-256
`cd4178d4518e13c64630d9de62fdbfbc05c0774f1ee8291f9fc56998910719a6`,
contains the full 176,528-byte `sabin/idt_cam.bin`, SHA-256
`d21e2c0b6b87f49f70ccf451781093037db8093b5a378587ec0029a16dcaef92`.
The file's low header halfword is `0x003D` and the declared length at
`+0x18` matches the file. The experimental preload supplies that entire
original image, never an isolated header or a readiness value. The
attempt in `bringup-step42-idt-sidecar-01` loaded at the **virtual**
address as a PA and still read zeros; its later Play observation
timed out. The original bootloader's file-read path was not exercised.

`bringup-step42-idt-physical-01` changes only the hand-off by placing
the image at **PA `0x20152000`**. Original code reads the header at
the same VA and writes one to `0x643EF448`, returns zero from the
job-26 transition, reaches OPD configuration, and sets the active
bytes for both jobs. The job-22 predicate returns one with state
`0/1`; the firmware publishes `0x0102` and enters the previously
unreached state clear at `0x638F722E` for `0x3003`. All 246 sampled
module-1 predicate returns are one. Thus the original final wait
clears without adding an OPD interrupt, providing autofocus results,
changing guest instructions, or forcing a firmware response.

The capture launcher now exposes the same preload through opt-in
`IDT_CAM` with pinned captured kernel, ORIL and sidecar hashes.
In the physical-address replay the SD file-service mount was not
observed and both pre-Play and post-Play LCD captures are black.
The card is byte-identical and QMP exit is zero. No camera-pin
decrement or application-level playback was proved in that comparison.

`bringup-step42-idt-launcher-01` verifies the checked launcher option with
the original mounted SD file service (`FsysMount /dev/mmca` ends at
4,801,589 guest us). The original sequencer stores completion `0x3003`
at `0x638F7218`; its worker maps the notification to `0x0302` and sends
it to internal queue `0x000302D9` with return zero. `bringup-idt-mounted-capture-01`
observes the same message pointer and first two words (`1, 0x0302`) at
send `0x638FBACE` and receive `0x638FBE20`. The dispatcher at
`0x638F9524` selects original Thumb entry `0x638FB225`. This is not a
lost internal notification. At this point the externally visible
EE callback, gate reopen and pin decrements are still unobserved.

The mounted capture replay reaches the original S2 action before
pressing Play. Teardown has already cleared the recorder's device flags,
and S2 still takes `Skip Capture Start Dev`. The Play capture is black,
SD CMD24/CMD25 are absent, QMP exit is zero, and an independent FAT read
of the closed card finds only the unchanged seeded `DSC00001.JPG`.
The card image hash is unchanged, so there is no new file to reopen.

#### DDR-B transition restores the native EE acknowledgement

The internal `0x0302` handler at `0x638FB41E` does more work before
building the application reply. `bringup-idt-post-ee-01` passes both
original semaphore-25 acquisitions. `bringup-idt-raw-callback-01` reaches
the release delegate and its original flag-19 setter; the flag wait
for `0x100` returns zero. This rules out a missing release callback.
`bringup-idt-raw-unit-01` then sees the original `AsyncAllocRule`
request to `0x0061003C` (`0x0014`, rule 1, mode 0, tag `0x0055`)
sent successfully, followed by a receive with no sampled return.

The identity-matched `dmm.ko` (`14197d3a…`) and `upm.ko` (`33a5b1d0…`)
were extracted read-only from captured `nflasha15` (`70d2613f…`).
`bringup-idt-dmm-rule-01` reaches `dmm.ko+.text+0x5908` at runtime
`0x5F0CF908`, then the synchronous codec-power request at `+0xC4E8`.
`bringup-idt-upm-ddr-01` records successful UPM callback registration
and receipt of `0x00920232` with words `{0x00920232, 0x61, 1, 0}`
on `0x00920524`. The exact original handler takes the codec branch and
performs its DFS handoff. At `upm.ko+.text+0x10298`, it sees zero from
PA `0xF01050A0` instead of bit 24. The site hits 6,527,070 times;
the next rank-idle check and DMM reply do not execute. The earlier
literal `0x920524` search of `/proc/osal/uipc` did not match its output;
it was not evidence that the callback had failed to register.

The original bootloader DGU5 (`nflashaB0` SHA-256 `3db81064…`)
independently polls that bit and `+0xA4` bit 0 together, at
`0xFE04B75E`/`0xFE04B768`. Its channel-B configuration records
at `0xFE043EB4` and `0xFE0440E4` write `+0xA0 = 0x300` and
`+0x138 = 0x10000`. Captured kernel `0x604DBB1C` writes rank
configuration back separately from the idle indicator, and
`0x604DBAEC`/`0x604DBAF0`/`0x604DBAF4` distinguish the command,
event clear, and status. An idle-only virtual controller let the original
firmware progress to `+0x138`, but with no command handler the next
`+0x148` poll at `upm.ko+.text+0x102F8` still read zero for
5,495,422 hits (`bringup-idt-upm-ddrb-control-01`).

The opt-in launcher now passes `DDRB_CONTROL=1` as `ddrb-control=on`.
The model starts with idle ranks and accepts the narrow native
`+0x138` transition to `0x00010300`, then `+0x90 = 0x26` after
the rank event is cleared. `bringup-ddrb-stock-media-01` observes:

| Original operation | Observed value |
| --- | --- |
| Read both rank idle indicators | `+0xA0 = 0x01000300`, `+0xA4 = 1` |
| Change rank configuration | `+0x138 = 0x00010300`, status `+0x148 = 0x20200000` |
| Issue the mode command after the native event clear | `+0x90 = 0x26`, status `+0x148 = 0x20000010`; mode reads `0x00240024` |
| Complete the provider chain | UPM returns zero at DMM `+0xC4EC`; DMM returns `0x8014` with status zero and tag `0x0055`. The original EE reply (`0x3003`, status `0xF000`) is assembled and received by `libObj+0xD5DE42`; the camera opens its deferred queue and dispatches the still-format acknowledgement. |

This is a synchronous virtual controller, not physical DDR timing,
training, self-refresh, or a full suspend/resume model. Unsupported
commands are logged and do not produce completion. No IPC reply,
readiness flag, or media decision is injected. The replay used QEMU
`202cb4dd4a41…`, observer `c0c4cad65c76…`, the checked captured
profile with `IDT_CAM`, and `WORK_DMA=0`.

The first hot-insert run has no SD commands and is not a mounted
capture trial. Its Play view is the original `Accessing...` dialog.
In `bringup-ddrb-seeded-play-01`, inserting the same source card at
reset completes `FsysMount /dev/mmca` at 1,617,494 guest us; the
second original `ModelBrowser` initialization completes, and the camera
opens its deferred queue twice. The stock `Preparing Image Database File`
dialog precedes 297 SD write commands in this run with **no** S2
input, and the disposable copy changes hash. Those writes do not
prove photo capture. After Play, neither captured head has the
seeded JPEG and no sampled still-play/parse entry is reached. The
case requires a retained no-shutter card and a Play action after
database preparation, not pixel or capture claims from command counts.
`bringup-ddrb-seeded-db-01` retains that control. It contains the
unchanged seeded JPEG (6,845 bytes, SHA-256 `3b6e3730e417…`) and
eight stock-written metadata files under `AVF_INFO` and `PRIVATE`.
The original browser reached the view selector with `No images in
this view.`; neither scanout showed the fixture.

The first active-only DDR model did not re-arm on a return to
shooting. `bringup-ddrb-reentry-control-01` records the stock idle
writes `+0x90 = 1`, `+0x138 = 0x00010000`, and `+0x68 = 0x00010000`
before the second active request. Without those writes taking effect,
the second rank-status poll sees only `0x20000000`. The current model
handles this narrow idle sequence and rejects duplicate or out-of-order
requests; qtest verifies a complete idle/active cycle. It preserves
RAM contents and does not model physical DDR power-down.

`bringup-ddrb-repeat-folder-01` uses the DDR-B-only build `e35ec2f1cd10…`
and the closed, guest-prepared card (`f40c512f788d…`) in a fresh
process. It completes `/dev/mmca` mount, waits for the measured
stock view selector, and changes the highlight to Folder View (Still).
Confirming the selection does not display the JPEG. The native
recorder destroy request `0x1200F06C` still owns the topology. In this
run the two pin-9 ends have zero references. The outstanding reference
is on a different connection: `ObjCameraAgent` (`0x08D7CF38`) pin
`0xB` (`0x08D7D030`) has one, and its peer (`0x08D77EE0`) has zero.
Unlike the first EE start, this replay also has an unreturned
allocation-rule **0** request: UPM enters `0x00920231` with argument 1,
but does not reach the DDR idle writes. The initial rule-1 path
and EE queue reopen remain proven; the bounded rule-0 wait is separate
from the previously fixed rank-status re-entry. QEMU exits zero,
with 450 SD commands, zero SD CMD24/CMD25, and an unchanged card.

Validation after adding `IDT_CAM`: the launcher refuses both the original
image with legacy inputs and a size-matched corrupt image with captured
inputs before creating a run directory. `bash -n qemu-a6400/run.sh` passes.
In WSL, `python -B -m unittest tests.test_qemu_boot_inputs tests.test_qemu_liveview
tests.test_qemu_emmc tests.test_qemu_media tests.test_research_status_sync -q`
runs 49 checks successfully with `A6400_QEMU_DIR` pointing at the
DDR-B build; `python -B -m unittest discover -s tests -q` runs 2,178 with 78 skips
and no failures. The captured backing, original ORIL, IDT image, and SD
fixture re-hash unchanged.

In the separate `bringup-stock-input-events-01` hot-insert run,
`mmca10p1` is listed during shooting. Original `I-InputMain` sends
body S2 `0xA7`; `InputService::pushFilterEvent` (`libObj+0x3D1600`)
sees press `0x15010032` and release `0x15010031`. The card SHA-256
remains `581066b0588df7ffc68836143e0d090eb1282d03625648f375901c311498631a`,
and no SD CMD24/CMD25 appears. This qualifies the application-input
route, not release admission or the earlier confirmed file-service
mount. No guest JPEG exists to reopen.

`bringup-still-release-path-01` closes the same-run mount/input gap.
The early hot insert completes `FsysMount /dev/mmca` at guest
`7,983,088 us`, before the held S1 then S2 sequence. The original
filter sees `0x15010032`/`0x15010031` and `ModelStillRec` reaches
`pvt_ActionExeCaptureStart` once (`libObj+0x6FCC3C`), but not the
start-command issuer (`+0x6FC4D8`). No sampled ORIL release-lock/S2REQ
site or still encode/write entry runs. SD CMD24/CMD25 count is zero
and the card stays byte-identical.

`bringup-still-device-guard-01` repeats the native press after a
confirmed mount. The destroy request `0x1200F06C` clears the
recorder's byte at `+0xFD1` (`libObj+0x703A4C`); shooting re-entry
request `0x1200F06B` queues behind that teardown. On the same recorder
object, the S2 action then reads four zero bytes at `+0xFD0`, takes
the original branch at `+0x6FCDC2`, and enters `Skip Capture Start
Dev` at `+0x6FD074`. The previous shooting OSD is not evidence that
the new recorder topology activated. The original forward calls for
`SET_STILL_FORMAT_CMD`, `EE_START_CMD` and `SET_SPINAL_REFLEX_CMD`
return zero without retry; this is submission, not completion.
The original still-mode gate must be resolved before analyzing a
real JPEG encode/write or testing restart survival.

### The DFS clock switch releases the separate UPM rule-0 wait

The original UPM handler does not block inside a timer call. In
`bringup-ddrb-rule0-folder-01` both synchronous DFS queries to
`0x00920277` return through `.text+0xFD54/+0xFDA4` 7,240,811 times,
without ever admitting the original delay calls. In
`bringup-ddrb-dfs-folder-clock-01`, the queried DFS state is `0xB`
instead of `1` or `2`. The DFS worker has returned from its original
peer handshake and is applying its clock switch. It writes `1` to
`0xF2916700`, then checks `(status & 0x101) == 1` at
`.text+0xD9F0` using status from `0xF2916610`. The sampled value is
zero and the predicate runs 24,430,407 times. The next store to the
status word does not execute. In the retained no-shutter bootstrap
control, native DFS state `2` allowed the same rule-0 request and its
three delays to return. That control's observation harness timed out
waiting on a transient step number, but the original replies and DDR
writes are in its log; it is not a successful screen replay.

`DFS_CONTROL=1` (`dfs-control=on`) is a separate opt-in. The original
switch programs `+0x620 = 0x101`, stages a divider at `+0x720`, then
writes `1` to `+0x700`. The model sets `+0x610` bit 0 for that sequence;
the original clear writes one back to that bit. The model does not
change the virtual clock rates or supply IPC replies. Qtest verifies
default behavior, refusal of invalid order and command, bit-specific
clearing, and a second switch. On QEMU `57370edd5714…` in
`bringup-dfs-clock-folder-01`, two original clock switches each pass
their first status check. The native DFS query returns `2`, UPM issues
the idle register writes, and DMM replies to rule 0 with opcode `0x8014`,
status zero and tag `0x0055`. This is not a teardown-completion claim:
pin `0xB` can still have outstanding references.

The native still-folder view now lists `100MSDCF`, and in
`bringup-dfs-stock-folder-child-01` opening the folder shows entry
`100-0001` with a `?` thumbnail. Selecting that entry reaches
`MprIf::EnterStillPb` and `MprIf::PlayStillSingle`. The repeat
`bringup-dfs-play-worker-01` reaches the admitted worker
(`libmpr+0x51F3EA`, mode `7`) and `DecStartStillDecorderFilter`
(`+0x3CA5BC`) twice. A later ZMA1/SA1 start uses program entry PA
`0x23FEA200`; the existing null device emits factor `0x01` with mask
`0x80` and produces no output data. This association is not yet a
qualified JPEG accelerator contract. The no-photo control has no
such decoder-filter entry or ZMA1 start. The full-screen comparator
rejects the seeded replay (worst-channel mean error `119.42`; no-photo
`115.07`; limit `12`). The seeded card keeps its input digest with no
SD writes; the separate no-photo replay issues 298 SD writes while
entering the browser, which are not evidence of capture.

### Recognizing the original ZMA1 startup program

The read-only `bringup-dfs-play-zma-02` trace binds the SA1 command to
ZMA1 at `0xF34B0000`. Original code programs `+0x150 = 0x23FE8000`,
`+0x008 = 0x80000000`, and `+0x030 = 0x23FEA200`; it clears the
interrupt factors, enables `+0x108 = 0x80`, and writes RUN and GO.
The SHA-256 of the first 64 bytes at the observed entry matches offset
`0x200` in the original `sabin/sa_zima_startup_zma1.bin`, not either
captured JPEG decoder program. The old null SA sets factor `0x01`,
which does not assert source A when only `0x80` is enabled. The
stock decoder-filter entry alone is not JPEG execution.

`ZMA1_STARTUP=1` opts in to a startup interrupt for this exact
program. The model requires ZMA1, the observed DRAM-program mode,
the startup mask, a bounded RAM address, and a SHA-256 match of the
full 2,692-byte program payload (`46a92d9f…`) extracted at offset
`0x200` from captured sidecar `fc668233…`. It does not execute DSP
code, produce pixel data, or reply to a guest application. The
ordinary null SA path is unchanged. Qtest refuses an unrecognized
program under the startup mask and checks that ordinary completion
still uses factor `0x01`.

On QEMU `ba71c8f8e5eb…`, `bringup-zma1-native-boot-01` verifies the
new factor `0x80`, a native IRQ 54 assertion, and original reads
and clears of factor and mask. The stock code goes on to write
`0x01001300` and a buffer pointer into mailbox A (`+0x110/+0x114`)
and writes `1` into `+0x00C`. That next command is not modelled.
The rear LCD now says **Unable to display**, rather than staying
black; the same full-screen comparison still fails (`98.47`,
limit `12`). All 450 SD commands are read/control, with no SD
CMD24/CMD25 and an unchanged card. The sampled ORIL DGI JPEG header
and submission sites remain unobserved, which does not rule out
other parser paths. No JPEG output, fabricated pixels or writes.

The repeat `bringup-zma1-command-01` verifies the original startup
wait at `0x63A8BB36` returns zero. The first later source-A mailbox
submission is at `0x63A81D5A`. Its descriptor at PA `0x15EF8180`
has nonzero words `0x780`, `0x438`, `0x780` at `+0x18`, `+0x1C`,
`+0x20` in the observed 64-byte window. These values do not yet
identify the descriptor ABI, an input JPEG, or a valid reply.
Captured ORIL arms source A with mask `0xFF` **after** the kick
(`0x63A81858`); no further ZMA1 factor read or clear is observed.
The [bounded Ghidra analysis](../../artifacts/analysis/zma1-source-a-01)
uses Ghidra 12.2, captured ORIL `dd8f8d0c…`, and original Thumb VAs;
the sender and mask sequence were checked against instructions and
the MMIO trace. The stock output and card digest match the preceding
negative run.

`bringup-zma1-command-origin-01` observes two zero returns from the
original `DecStartStillDecorderFilter` at its caller (`libmpr+0x3CD1B2`)
and two `0x4001` asynchronous decoder-handler entries (`+0x562ED8`).
The late screen is byte-identical to the previous **Unable to display**
frame. This is not an immediate error return from the filter start. The
new source-A enqueue sites were outside the observer's translation
whitelist in this replay; their zero hit counts are not evidence of
an alternate path.

With the whitelist corrected, `bringup-zma1-source-a-queue-01`
records the same 24-byte `0x3198` request at the original dispatcher
(`0x63D90228` from `0x635D3DEA`), route (`0x63D901B2`) and
enqueue (`0x63A8085C`). At `+4` it carries the Thumb callback
`0x63A0B7A1`, at `+0x12` operation `0x13`, and at `+0x14` the
same PA that reaches mailbox A. Those fields match the captured
ORIL writer at `0x63A0B91E..0x63A0B962`. That writer's report
path references `src/dgi_jpeg_fill.c` and
`dgis_jpeg_fill_check_zima_rpt`. The missing operation is the
**DGI JPEG fill** stage, not delivery of compressed JPEG data
to the ZMA1 startup program. The [bounded fill analysis](../../artifacts/analysis/zma1-jpeg-fill-01)
binds the native submit and callback sites. It does not establish
the writes or hardware completion required for this request.

`bringup-zma1-fill-result-01` observes that submit and then return
`0x24` from `dgi_jpeg_common_wait_safin` at `0x63A0B986`. The original
wait maps the `-50` timeout case to `0x24`; the fill routine preserves
that value in `r4` through cleanup and returns it at `0x63A0BB32`.
The callback at `0x63A0B7A0` and sampled DGI decode submission both
have zero hits. The 252-byte descriptor prefix additionally contains
`+0x44=0x3C000000`, `+0x5C=0x15EF8580`, and `+0x68=0x00800080`.
These words are observations, not a complete DMA contract. The LCD
again matches **Unable to display** and the closed card is unchanged.

`bringup-zma1-fill-planes-01` captures the complete 384-byte request.
`+0x110=1` declares one extent, `+0x118` and `+0x124` both name the
output base (`0x3C000000` here), and `+0x120=0x003FC001` describes a
valid padded extent. Native setters at `0x63D7AE56`, `0x63A03188`,
`0x63A032C4`, `0x63A03A58` and `0x63A03C40` bind the geometry,
color, output extent and separate report pointer. The original
consumer at `0x63A0BA78` accepts report word `+4` equal to 1 or 2.

`ZMA1_JPEG_FILL=1`, separately opted in with `ZMA1_STARTUP=1`, services
only that neutral single-extent layout after the qualified startup.
The model validates the descriptor and both RAM ranges, initializes
the active two-byte-per-pixel output from its neutral pattern, then
sets the report word and raises the normal timed source-A factor.
It does not process a compressed stream. The synthetic native test
checks invalid extent rejection, no early completion, active bytes,
unchanged padding, report write and factor. Its declared startup
sidecar is verified before use.

On `376f6dbb0ce5…`, `bringup-zma1-fill-model-01` observes two actual
source-A factor `1` read/clear cycles, original fill callbacks at
`0x63A0B7A0`, and successful waits at `0x63A0B986`. The original VPH
selects the same output base with 3,840 bytes per line and 1,080
lines. The rear LCD still has the byte-identical **Unable to display**
frame (`a33ac2146616…`). No sampled decode submission occurs and
the closed card is unchanged. The fill timeout is resolved; it was
not sufficient to make the firmware display the JPEG.

`bringup-zma1-player-result-01` verifies both final fill returns
after `mov r0,r4`. It also binds the still-filter virtual start to
`libmpr+0x5649E1` (command 3), with return zero twice. The original
asynchronous handler at `+0x562EEE` subsequently reads status
`0x1600F301` for event `0x4001` twice. The earlier zero hit count at
the virtual start was a probe mismatch: its signature included
three relocated literals at `+0x3CA808..0x3CA810`. The decoder's
asynchronous error is separate from the resolved fill timeout.

`bringup-zma1-player-worker-01` and `bringup-zma1-player-queue-01`
confirm that the command configures the original output (`+0x564752`
returns zero) and the original work submission (`+0x5648DE`) returns
zero as well. The original async status `0x1600F301` arrives **before**
both neutral fills, so the fills are a later fallback, not the cause
of playback failure. The request sender allocates and sends on
`0x0055001F` (the `InfraAvbbcIf` queue in E0309). Continue at the
original reply conversion; do not make fill decode compressed bytes
or assume the still filter never submitted work.

`bringup-zma1-player-work-dma-01` also enables the previously modelled
av-cam initialization copy. It reaches the original `0x4001` reply,
the same `0x1600F301` status and the same LCD hash. In
`bringup-zma1-player-native-result-01`, the original mapper at
`libmpr+0x355FFE` sees raw `0xFB030005` twice before translation.
The branch at `+0x356F84` reads the incoming `0x4001` report status
at `+8`. Neither the media producer of that status nor a matching
compressed decode is verified by the conversion alone.

`bringup-zma1-player-uipc-01` observes two original async sends to
`0x00C70047` carrying the raw error before `libmpr` maps it.
`bringup-zma1-error-owner-02` preserves 90 acknowledged executable
mappings from that same QEMU process. Both send return addresses
resolve to `dataflowInfraFramework.so+0x22596`. The captured library
(`5519b2a31a6d...`) copies the existing payload and calls
`osal_snd_msg` at `+0x22592`; it does not calculate the JPEG result.
The preceding owner-01 run closed before the guest returned its
maps, so its addresses were not used for attribution.

`bringup-playback-dataflow-origin-01` finds the raw error at entry to
that sender; both calls come from `libObj+0x50D5F4`. In
`bringup-playback-avbbc-origin-01`, both 36-byte packets already contain
`0xFB030005` at `+0xC` on entry to `InfraAvbbcMsgManager`
(`libObj+0x50D268`). Its same-process maps resolve both return addresses
to `libObj+0x32E83E`. The original call at `+0x32E7FE` requests the
decode/resize result from a virtual backend and `+0x32E802` copies its
output status into the packet. Neither sender originates the error.
`bringup-playback-result-backend-01` replays that selection with the
same board binary and options. Both virtual calls target Thumb
`libObj+0x25EACC` in the acknowledged maps. The call returns zero
but supplies `0xFB030005` through its output word both times. The
stock error frame and closed card digest are unchanged. What
produces the backend's output status remains open.

### Mounted single-shot capture remains unverified

`bringup-ddrb-mounted-shutter-01` reaches the recorder with both device
bytes enabled, unlike the earlier destroy-gated trial. This is a
RAW/self-timer trial, not a JPEG capture. A stock menu comparison
selects **Single Shooting** and **JPEG** on the prepared card before
S1/S2. With DFS off, `bringup-jpeg-stock-single-02` receives native S2
press and release and enters `pvt_ActionExeCaptureStart`. It takes the
original nonzero `PARAMID_STS_SPINAL` branch (`libObj+0x6FCCCA`,
exit at `+0x6FCDA8`, marker `0x1AE`), before the device guard. It
issues no capture command or SD write. The observation harness times
out after releasing the contacts; it is not a successful run.

With DFS enabled, `bringup-dfs-jpeg-stock-single-01` completes the
mounted menu/press/release sequence and exits normally, but the
sampled view filter does not get the `0x15010032` press and no capture
action executes. An independent held-contact replay confirms P1.5
changes from high to low while S1 remains low and returns high at
release. None of these trials creates a file to reopen. Trace the
original shutter route and capture predicate separately from the
playback accelerator; do not generate or insert a substitute JPEG.

In `bringup-dfs-shutter-irq-02`, the current-run LIRO table binds
IRQ 142 to the original Thumb handler `0x638B3728` on CPU 2. A
press raises and drops the GPIO IRQ without delivery in that sample;
the release is sampled by the handler. A separate bounded comparison
uses the original 50-us GPIO pulse versus an experimental 1-ms
pulse on P1.5 only (`bringup-dfs-s2-{short,long}-irq-01`). Both
run the same captured RAW/self-timer setting. In both, the original
handler reads low on press and high on release at `0x638B373E`,
the original view accepts S2, and the recorder issues `0x750A`
(`STILL_CAPTURE_START_CMD`). The short pulse has one native GIC
acknowledgement per edge; the long pulse has two per edge, so the
long pulse is not retained as a device fix. Neither run observes
`0x750B` capture-start completion, JPEG encode/write, or SD writes.
Their native command submission is a separate positive observation,
not evidence of an exposure or a saved photo.

The diagnostic `bringup-dfs-s2-long-jpeg-01` verifies the stock JPEG
setting, Single Shooting and a completed `/dev/mmca` mount in the
same run. Its S2 press reaches the original capture action, which
again exits at marker `0x1AE` on `PARAMID_STS_SPINAL = 1`, before
issuing `0x750A`. QEMU exits normally. The closed FAT walk finds
only the original 6,845-byte `DSC00001.JPG`, still decodable at
640 x 480 with the exact seed digest; there were no SD writes.

`bringup-dfs-jpeg-selftimer-01` keeps the captured five-second
self-timer and changes only the file format through the stock JPEG
setting. It verifies the native mount and holds S2 for 420 display
ticks, then observes another 1,900 ticks. The GIC sees IRQ 142
assert and deassert on press without acknowledgement. The original
handler runs only on release and samples P1.5 high. No capture
action executes, so the longer hold does not test recorder
completion. QEMU exits zero with no SD writes; the closed FAT walk
finds only the unchanged seed.

The diagnostic `bringup-dfs-jpeg-edge-selftimer-01` changes only the
two body shutter trigger modes in the emulator's boot prelude. It
matches the stock mount, JPEG selection, pre-press LCD hash and
420-tick hold of that control. With the normal 50-us GPIO pulses,
the native GIC takes one interrupt per press/release on IRQs 122
and 142. Both S2 levels are sampled by the original handler.
The stock recorder reads `PARAMID_STS_SPINAL = 0`, passes both
device flags, and issues `0x750A` before release. It does not report
`0x750B`, call the sampled JPEG encode/write functions or write SD
blocks; the closed card is unchanged. This distinguishes delivery
from recorder completion, and does not overturn the Single Shooting
comparison where Spinal was one.

The opt-in board form of that diagnostic is `SHUTTER_EDGE_IRQ=1`
(`shutter-edge-irq=on`). Before the ordinary CPU0 stub, it sets only
the edge bits of `GICD_ICFGR7/8` for IRQs 122/142 using read-modify-write.
It preserves the other SPI settings and the default boot stub. The
boot prelude was cross-checked against the 36-byte assembled diagnostic
(`1cf5898c0628…`). A synthetic native ARM test keeps IRQ delivery
masked while both input pulses expire: the two pending bits survive
only with the option enabled. This is a virtual board policy; the
camera's physical GPIO interrupt latch is still unmeasured.

`bringup-jpeg-edge-model-01` repeats the native admission on the
board implementation (`1858a1bb7de7…`), without the diagnostic
loader. The stock SD mount and JPEG menu hashes pass, each shutter
edge is acknowledged once, and `0x750A` is issued before release.
No sampled P7.15 request-high write or `0x12` S2REQ message follows;
other writes on port 7 and a `0x13` message are observed. That is
not evidence that a correctly issued exposure interrupt was lost.
The next boundary is after native capture submission, before a
proven exposure or encoder input. No `0x750B`, encode/write call
or SD write occurs; an independent walk of the closed FAT image
finds only the 6,845-byte unchanged seed.

`bringup-jpeg-single-edge-01` repeats the mounted JPEG/Single Shooting
control on `376f6dbb0ce5...` with `SHUTTER_EDGE_IRQ=1`. Each S2 edge
has one acknowledgement, and the original GPIO handler reads both
levels. The capture action still exits on `PARAMID_STS_SPINAL = 1`
before `0x750A`; the replay exits zero with an unchanged SD image.
This rules out missing or duplicate S2 delivery in this comparison.

`bringup-jpeg-capture-queue-01` binds the original forwarding target
to `libMWF+0x1D53E` from the process map and observes the same `0x750A`
at the original `libObj+0xA7323A` dispatcher. This is native delivery
through MWF, not yet submission to the camera sequence. It again
creates no photo.

`bringup-jpeg-capture-agent-01` observes that request at
`libObj+0xA736A2`. The original `GetSpinalUtil` result is non-null
(`0x0021C400` in this run); the next mode predicate at `+0xA736B4`
returns zero and selects the ordinary handler at `+0xA73730`.
The null-object branch and the alternate sequence call at
`+0xA7371C` do not execute. This narrows the next boundary to the
ordinary capture dispatch, not body IRQ delivery or a proven
missing exposure interrupt. The closed writable card is unchanged.

`bringup-capture-sequence-transport-01` also sees drive parameter
`0x031D` at both original dispatch layers. The original inhibit
query at `libObj+0xAFF53A` returns zero. The first sampled subsequent
native send is a `SeqStillCap` packet `0x4044` with payload word 2
to `0x00040193`; it returns zero and ORIL emits its `0x13` S2REQ
message. Later `0x4031` sends are from `SeqSyncIrisDrive`. None of
these sends is a capture result. A separate bounded comparison of
the EE module comparator has no hits after native S2, so the
displayed sequencer index 12 alone does not establish a capture
predicate or a missing module reply.

The held-exposure control `bringup-capture-held-exposure-01` uses the
same stock JPEG/five-second self-timer settings. The held LCD shows
the requested 30-second exposure; that display does not prove that
exposure began. The recorded contact remains asserted through more
than 2,240 display ticks. Both held frames are byte-identical.
Native `0x750A` is observed, but there is no sampled `0x12` S2REQ,
`0x750B`, encode/write call or SD write. The replay's 600-second
host deadline expires during the post-release observation, not
during the hold. QEMU exits zero; the independent closed FAT walk
finds only the unchanged seed. This bounds the held-input test, not
the unobserved post-release window.

`bringup-capture-caif-request-01` repeats the mounted JPEG/self-timer
sequence on the same board binary and records the original CAIF
request. `CAP_REQ` routes selector zero and sends message `0x13`.
The original semaphore-29 wait at `0x638E984E` returns zero. The
queue consumer at `0x6375DEBC` checks matching words `7` and `0xF`
and mode byte `4`, then takes `0x6375DF8E` because the substate
byte is `1`; `0x6375DF74` is not reached for this message. The
meaning of that branch and the missing exposure transition are
not yet established. QEMU exits zero with the same closed SD digest
and no new photo.

On `57370edd5714…`, the five focused boot-input, QEMU, media, and
status test modules pass 50 checks. After the ZMA1 change, all six
native live-view/DDR/DFS/SA tests and 51 focused checks pass.
Full WSL discovery on
`ba71c8f8e5eb…` reports 2,187 tests, 78 skipped, no failures.
After the shutter edge policy, the new board build `1858a1bb7de7…`
passes 52 focused checks and 2,188 full WSL tests (78 skipped).
With the fill model, `376f6dbb0ce5…` passes 53 focused checks and
2,189 full WSL tests (78 skipped); those totals include the optional
sidecar-qualified fill regression.
After adding optional exact path-and-digest comparison for the reopened
DCIM export, 54 focused checks and 2,190 full WSL tests (78 skipped)
pass. The restart verifier is tested with constructed files only.
The original eMMC, ORIL and IDT inputs
re-hash unchanged. These replays used the same captured firmware,
WBI-derived sub-micro fixture, bars, sequencer policies and disposable
cards as above, with the noted DFS and menu changes.
