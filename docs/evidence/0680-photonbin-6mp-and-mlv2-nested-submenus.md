# E0680 - `PhotonBin 6MP` Low-Light `.ARW` Mod and `Mod Loader v2` Nested Submenus (`SMNU`)

```text
ID: E0680
Date: 2026-11-22
Claim: True charge-domain photosite binning is physically absent on the Sony a6400 IMX284 CMOS APS (isolated floating-diffusion nodes, RGGB Bayer CFA, ECDrvParam[+0x20]=0x1000=0.00 dB unity capacitive averaging in Mode 10). Pre-demosaic 4x4->2x2 Bayer / 2x2 Mono photon convergence into 6.0 MP .ARW files (Option 1) combined with IMX284 Mconv High Conversion Gain (+9.805 dB / 3.092x) and 14-bit 1H2H readout overrides (Option 3) is implemented as a customizable SMOD v2 mod (PhotonBin6MP.mod) driven by a backward-compatible nested submenu extension (SMOD_V2_FLAG_SUBMENU / SMNU) in Mod Loader v2, verified via Unicorn suites and live ILCE-6400A 1.01 QEMU execution.
Status: verified (offline Unicorn + live captured ILCE-6400A 1.01 QEMU guest; physical camera still-capture execution not run)
Firmware hash / camera configuration:
  - Target body: Sony ILCE-6400A firmware 1.01 (CXD90045, Sony CE Linux 8.2.7.6)
  - Captured QEMU image: ~/a6400-inputs/a6400a-v101-captured/emmc.img (2,147,483,648 B, SHA-256 ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f, verified identical before and after QEMU run)
  - Verified firmware inputs (firmware/extracted_a6400a_v101/ & ~/a6400-inputs/a6400a-v101-captured/):
      av-cam.bin       16,822,380 B  SHA-256 3029a174b393fee9b2624d27737c20bc60e71cbbb33736b2a9363b57184ee03d (ORIL_BASE 0x635C6000, LIRO PA = VA - 0x60000000)
      viewUnified2.so  11,534,720 B  SHA-256 a51e120f7ff6334902e2aa49e78bfadbcbcf000a6e31f947b1bdb8380edcf168 (ref base 0x12F46000)
      libObj.so        20,732,776 B  ref base 0x05FC2000 (MakeRawInfoArea @ +0x00D7861C = 0x06D3A61C)
      libmpr.so        12,666,664 B  ref base 0x075CE000 (MakeRawMainIfds @ +0x00432978 = 0x07A00978)
Source:
  - tools/native_camera/mlv2/{mlv2.h,core.c,test_mod.c,build_wsl.sh,test_mlv2_unicorn.py}
  - tools/research/pack_mod_v2.py, src/a6400/mod_manager/{smod.py,catalog.py}, tests/test_mod_manager.py
  - mods/photon_bin_6mp/{photon_bin_6mp.c,photon_bin_6mp_trampoline.S,build_wsl.sh,test_mod_v2_unicorn.py,README.md}
  - mods/ilce_6400a/PhotonBin6MP.mod (6,188 B, SHA-256 ef5af925becb321b29da55440d1c8e6ace650b099690cda1b3193747a219017f)
  - Receipts:
      artifacts/tests/mlv2-ilce6400a-build-3/ (core.bin 9,564 B, FNV-1a 0x9db02975, SHA-256 c8cc6874ffbbaed6a81637bcb58b17f0ea9b668113d70aa9e7d89b93c87600b3)
      artifacts/mods/ilce_6400a/photon_bin_6mp/
      artifacts/qemu-a6400a/photon-bin-6mp-e2e-20261122/ (01..09 LCD PNGs, moddev_status.json, moddev_report.json, panel.log)
Method:
  1. Disassembled Camera::Imager::IMX284::C_ImagerCtrl_IMX284 (vtable 0x6451DE80) and ImagerDrvParam_IMX284 (vtable 0x6451DFA8) in av-cam.bin, extracted all 19 drive modes (MODE_REG_TABLE_MAIN @ 0x64550F4C), and executed calcDrvParam (0x637A0E20) in Unicorn to verify Mconv (+9.805 dB = 0x317A Q12), ADnum (1H1H vs 1H2H), and binning gain (0x1000 = 0.00 dB).
  2. Disassembled ImageBuilder::MakeRawInfoArea (libObj.so + 0x00D7861C) and MSL::MSLD_ArwDI::MakeRawMainIfds (libmpr.so + 0x00432978) to map rawMainSrcData (+0xD0), rawMain (+0x110..+0x11C), and rawAdditionalInfo (+0x120) fields feeding .ARW SubIFD serialization.
  3. Extended Mod Loader v2 (CTX_VERSION=3) and pack_mod_v2.py with SMOD_V2_FLAG_SUBMENU (0x20, FNV-verified SMNU trailer) keeping SmodV2Header 68 B unchanged, calling PAS_MenuListTab::refresh (MLV2_MENU_REFRESH) on submenu entry/exit, syncing option bytes into (__smod_bss + bss_cfg_off), and invoking cfg_call_off(2) on live option edits while ON.
  4. Implemented PhotonBin6MP.mod with 6 customizable nested submenu settings across 2 pages, verified Option 3 and Option 1 in Unicorn (including LibRaw ARW 2.3 cRAW round-trip), and executed a full interactive QEMU ILCE-6400A 1.01 rear_panel.py run.
Observation:
  - Mod Loader v2 Unicorn test suite (test_mlv2_unicorn.py): 16/16 scenarios PASS on both ilce_6400a and ilce_6400 builds, including Scenario 16 (2-page nested submenu rendering, pre-ON customization, live cfg_call_off(2), < Back focus restoration, and rescan persistence).
  - PhotonBin 6MP Unicorn suite (mods/photon_bin_6mp/test_mod_v2_unicorn.py): PASS across SMOD/RLC1/SMNU validation, Option 3 LIRO halfword swaps & Unicorn execution of IMX284 Mconv @ ISO 100 (reg 0x099C 0x20 -> 0x22) and Force LCG @ ISO 6400 (0x22 -> 0x20), foreign-byte refusal (sw='x'), and Option 1 cRAW/16-bit 4x4->2x2 [1,2,1]/[1,3,3,1] phase-aligned Bayer, 2x2 Box, 2x2 Mono, 16-bit highlight preservation (46,464 ADU > 16,383 with exact 512 black floor), and 14-bit Sony ARW 2.3 cRAW round-trip (R=2512, B=4512).
  - Live QEMU ILCE-6400A 1.01 execution (artifacts/qemu-a6400a/photon-bin-6mp-e2e-20261122/):
      * Guard installed core_init 00000003 (core_load 9db02975) into live im.elf (core_view_bias=0xfff39000).
      * Mods tab rendered 'PhotonBin 6MP' | 'OFF >'; entering opened 2-page submenu 'PhotonBin 6MP  1/2' and '2/2' with orange/grey page dots.
      * Turning Mod Status ON logged 'pb_opt3 en=1 rsn=1 cfg=000000 mc=w,w h2h=w,w' (all 4 live av-cam.bin LIRO sites matched stock and wrote HCG + 14-bit 1H2H).
      * Live option edits on Pages 1/2 and 2/2 triggered mod_cfg callbacks and live LIRO re-patches ('cfg=011210 mc=w,w h2h=a,a' -> Stock Auto, 'cfg=011220 mc=w,w h2h=a,a' -> Force LCG, 'cfg=011221 mc=a,a h2h=w,w' -> Stock Gate).
      * < Back returned to 'Mods 1/1' showing 'PhotonBin 6MP' | 'ON >'; switching Mod Status OFF logged 'pb_opt3 en=0 rsn=0 cfg=011221 mc=w,w h2h=a,a' and 'mod_off 0e104000', restoring all sites cleanly.
      * Captured emmc.img SHA-256 remained ed231fc020c4e43a1bc7ef7d62738049a988622e7cdf7271bb3543b3e4e7e22f.
Alternative explanations:
  - None for the verified instruction, menu state-machine, LIRO memory swap, and ARW2 codec contracts.
Confidence and limitations:
  - High confidence in Mod Loader v2 nested submenu navigation, LIRO Option 3 instruction swaps, and Option 1 MakeRawInfoArea .ARW buffer/IFD transformation.
  - Because QEMU sony-a6400 does not yet model live IMX284 CSI-2 sensor frames into DFECore still capture, end-to-end MakeRawInfoArea pixel convergence was verified in ARM Thumb Unicorn against real firmware structs and LibRaw's ARW 2.3 specification rather than a QEMU shutter release.
Dependent implementation/scenarios:
  - tools/native_camera/mlv2/, mods/photon_bin_6mp/, mods/ilce_6400a/PhotonBin6MP.mod, src/a6400/mod_manager/, tests/test_mod_manager.py
Next experiment:
  - Optional supervised RAM-only test on physical camera hardware (capturing a dark-frame + low-light chart .ARW across 16-bit Uncomp, 14-bit cRAW, and Force HCG vs Stock Auto) or porting the 5 target coordinates to ILCE-6400 2.00 once captured 2.00 av-cam.bin/libObj.so inputs are staged.
```

---

## 1. Verified `ILCE-6400A` 1.01 Target Coordinates

| Layer | Function / Target | Address | Stock Bytes | Patched Behavior |
| :--- | :--- | :--- | :--- | :--- |
| Option 3 (`av-cam.bin` LIRO `.text`) | `C_ImagerCtrl_IMX284` Main `Mconv` | `VA 0x6379FEC6` (`PA 0x0379FEC6`) | `b8 f1 00 0f 03 d0` | `b8 f1 00 0f 00 bf` (`Force HCG`) / `4f f0 00 08 03 e0` (`Force LCG`) |
| Option 3 (`av-cam.bin` LIRO `.text`) | `C_ImagerCtrl_IMX284` ZAF `Mconv` | `VA 0x6379FF7C` (`PA 0x0379FF7C`) | `b8 f1 00 0f 03 d0` | `b8 f1 00 0f 00 bf` (`Force HCG`) / `4f f0 00 08 03 e0` (`Force LCG`) |
| Option 3 (`av-cam.bin` LIRO `.text`) | `CaptureUtilityImager` Silent `1H2H` | `VA 0x638ECC48` (`PA 0x038ECC48`) | `f2 b9` (`cbnz`) | `00 bf` (`nop` — keeps 14-bit `ADnum=2`) |
| Option 3 (`av-cam.bin` LIRO `.text`) | `CaptureUtilityImager` Bulb `1H2H` | `VA 0x638ECC56` (`PA 0x038ECC56`) | `19 e0` (`b`) | `00 bf` (`nop` — keeps 14-bit `ADnum=2`) |
| Option 1 (`libObj.so` `.text`) | `ImageBuilder::MakeRawInfoArea` | `0x05FC2000 + 0x00D7861C = 0x06D3A61C` | `2d e9 f0 4f 85 b0 02 af 04 46 0e 46` | Calls original `MakeRawInfoArea`, then converges `6024x4024` RAW -> `3008x2008` `.ARW` |
| UI (`viewUnified2.so` `.text`) | `PAS_MenuListTab::refresh` | `0x12F46000 + 0x005B0360 = 0x134F6360` | Unmodified callee | Invoked by `Mod Loader v2` on submenu enter / `< Back` transitions |
