# E0554 - ILCE-6400A 1.01 libJiritsu dispatches GET_FILE on halfword 8

Date: 2026-11-17. Offline unpack of the official updater
`firmware/Update_ILCE6400AV101.exe` and disassembly of the packaged
`libJiritsu.so`. No USB session, no Senser entry, and no camera write.
The updater executable was not run.

## Package identity

The updater is 324,175,032 bytes, SHA-256
`fd6ae50755a9202a8b506b6b77aa27ab41ae717e96f05a0d4e2ad699b40d9ad1`,
the same file recorded in E0506. `tools.research.sony_updater_payload`
restored the inverted cabinet at file offset `0x71A2C` (323,698,894 bytes).
`expand.exe` extracted `FirmwareData_ILCE6400AV101.dat`, and `fwtool.py unpack`
wrote `firmware/extracted_a6400a_v101`. Those trees stay out of Git.

The main-image copies used below are from
`firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked`:

| File | Size | SHA-256 |
| --- | ---: | --- |
| `lib/libJiritsu.so` | 162,568 | `c3621c7ec30771604b1aaae25e51699340333486e1aa281a1cc5f4a543a99dff` |
| `lib/libusbcmd.so` | 15,996 | `23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e` |
| `kmod/usb_extcmd.ko` | 14,796 | `d47d05ec891d18c8582baf19e413967c5d3fd5c36438d76f6d2e8a7f112428e3` |

`libusbcmd.so` and `usb_extcmd.ko` match the E0506 package hashes. E0552's
WBI snapshot named this library; this record uses the contiguous ELF from the
updater instead.

## Dispatch

`jiritsu_init` passes thumb address `0x7015` to
`osal_reg_sync_msg_queue_cb`. That function reads the unsigned halfword at
message offset `0x1A`, subtracts one, and branches through a Thumb TBB table
for values 1 through 18. Each arm logs one `JIRITSU_*` string and then calls
the handler named below. The log-string addresses were resolved from the
PC-relative loads in front of those calls.

| Halfword | Log string | Handler reached when the message's first byte is 4 |
| ---: | --- | --- |
| 1 | `JIRITSU_MODE` | `cmd_mode` |
| 2 | `JIRITSU_MOUNT` | `cmd_mount` |
| 3 | `JIRITSU_FORMAT` | `cmd_format` |
| 4 | `JIRITSU_START` | `cmd_start` |
| 5 | `JIRITSU_DEBUG` | `cmd_debug` |
| 6 | `JIRITSU_FIRMUP` | `adj_cmd_set_firm` |
| 7 | `JIRITSU_SET_FILE` | `adj_cmd_set_file` |
| 8 | `JIRITSU_GET_FILE` | `adj_cmd_get_file` |
| 9 | `JIRISTU_FILE_DELETE` | `cmd_file_delete` |
| 10 | `JIRISTU_GET_FILELIST` | `adj_cmd_get_file_list` |
| 11 | `JIRISTU_GET_FILE_HASH` | `adj_cmd_get_file_hash` |
| 12 | `JIRITSU_COMPARE_MEDIA` | `adj_cmd_compare_media` |
| 13 | `JIRISTU_GET_VERSION` | `adj_cmd_get_version` |
| 14 | `JIRITSU_FACTORY` | `cmd_factory` |
| 15 | `JIRITSU_SET_FILE_PATH` | `adj_cmd_set_file_path` |
| 16 | `JIRITSU_SET_FILE2` | `adj_cmd_set_file2` |
| 17 | `JIRITSU_GET_FILE2` | `adj_cmd_get_file2` |
| 18 | `JIRISTU_FILE_DELETE2` | `cmd_file_delete2` |

`adj_cmd_get_file` reads the bytes immediately after that halfword: mode at
`+0x1C`, area at `+0x1D`, flag at `+0x1E`. Mode `1` is the path read. The
path bytes begin at `+0x1F`. `adj_cmd_set_file_path` requires the byte at
`+0x1C` to be `0` and then copies the following path. Those are the same
payload shapes the public ZV-E10 notes use for its commands 8-equivalent and
15, but this library compares the halfword with 8, not with `0x4008`.

A search of this `libJiritsu.so` text found no `movw`/`cmp` immediate
`0x4008`. Commands 15 and 16 in the public ZV-E10 tool match this table's
`SET_FILE_PATH` and `SET_FILE2`. Its `GET_FILE` constant `0x4008` does not.

`libsencore.so` from the same `nflasha15` image carries eighteen 72-byte
records. The record for `@JIRITSU_GET_FILE` ends its 8-byte header in
`10 08`, and the other seventeen records use the same trailing command byte
as the table above. That is a second copy of the names and byte IDs. It is
not, by itself, the Senser packet encoder.

## What remains closed

The host tool still refuses `check`, `start-ram-debugger` and
`jiritsu-dump` before opening USB. `A6400A_V101_LIBRARY_COMMANDS` records
halfwords 8, 15 and 16 with status `library`, so `get_file_payload` raises
instead of returning bytes. The category value `0x1001` is stored by this
library into one outbound request at `0x8A46`; that store was not followed
into `libsencore.so`'s Senser parser, and no packet was sent.

The first byte of the message selects an open, body, or close path inside
`cmd_high_capacity_get_file` (`1`, `4`, `0x10`). The body path is the one
that calls `adj_cmd_get_file`. Who writes that byte, and whether one host
exchange becomes one message, was not established.
