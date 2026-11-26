# E0552 - ILCE-6400A nflasha5 is a WBI1 image that names Jiritsu

Date: 2026-11-17. Offline read of the local partition image
`artifacts/dumps/a6400a/nflasha5.bin`. No USB session, no Senser entry, no
camera write, and no file carved out of the image into the repository.

## Result

The 62,914,560-byte image is a compressed **WBI1** warm-boot snapshot, the
same container already described for packaged `nflasha5` in E0043. It is the
configured `/wbi` partition from E0507, not a mountable copy of `/usr` or
`/rootfs`. SHA-256
`d131b73f9c4610bdc3f549a65e94d70c11cc92d3071b878d65ef46a8fe13b3b6`.
The bounded decoder reports **624** sections and **146,259,968** expanded
bytes.

Strings inside the expanded sections name this body and the service the
Senser file installer cannot use:

| Needle | Role in this image |
| --- | --- |
| `ILCE-6400A` | Model text is present in the snapshot |
| `Linux version 3.0.27_nl-rt106+` | Banner text matches the E0506 packaged main kernel family |
| `libJiritsu.so`, `libsencore.so`, `libusbcmd.so`, `usb_extcmd` | The service library, Senser library, USB API and external-command driver are named |
| `/tmp/jiritsu/libJit.so`, `/tmp/jiritsu/ver.txt` | Temporary Jiritsu workspace paths, in RAM prefixes |
| `JIRITSU_GET_FILE`, `JIRITSU_SET_FILE`, `JIRITSU_DEBUG`, `JIRITSU_START` | Operation names in the resident library's strings |

Those names explain the user's two observations. Feature `0x23` still answers
`05/20/00` until `a6400_usb_debugd` registers it. Generic Senser `FileControl`
is not the path that produced the seven firmware files: this snapshot's
reader is Jiritsu `GET_FILE`, which the strings show as a whole-file read
(`adj_cmd_get_file`) rather than a block `pread`.

## What this image does not prove

No ELF file for `libJiritsu.so`, `usb_extcmd.ko` or `libc-2.11.2.so` was
recovered as a contiguous file image. Numeric adjust-command IDs, including
the public ZV-E10 values `0x1001` / `0x4008` / `15` / `16`, were **not**
established as the ILCE-6400A dispatch values. The host packet builders refuse
that unverified map. `audit-wbi` is the only mode that reads this image.
`check`, `start-ram-debugger` and `jiritsu-dump` return before opening USB.

`/dev/nflasha23` stays excluded from the chunk planner. The planner is tested
offline and was not run against a camera.

## Reproduction

```powershell
.\venv\Scripts\python.exe -m tools.research.a6400a_jiritsu_bootstrap --mode audit-wbi
.\venv\Scripts\python.exe -m unittest tests.test_a6400a_jiritsu -q
```

The audit receipt stays under `artifacts/analysis/a6400a-wbi-jiritsu/`. The
partition image stays under `artifacts/dumps/a6400a/`. Neither is a source file.
