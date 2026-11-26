"""Bounded virtual-USB-host observation of the QEMU sony-a6400 DWC3 gadget model.

Boots the captured inputs with the opt-in `cxd90045-dwc3` model, the virtual VBUS cable and the
CID-high option, attaches a host socket to the model's chardev, and records what the original
guest kernel does when the cable is attached and a USB host speaks to it:

1. guest DWC3/UDC state before attach (serial shell commands);
2. QMP `usb-vbus=true`, then the model's `CONN` line (USB reset + connect done events);
3. optionally one standard SETUP packet (default GET_DESCRIPTOR(device, 18 bytes));
4. every line the model sends back (`IN <ep> <hex>`), guest state after attach, `DISC`, VBUS off.

Nothing is answered on the guest's behalf: an absent IN reply is reported as absent.
Run inside WSL from the repository root:

    python3 -m tools.research.qemu_usb_host_probe --qemu-dir ~/sony-a6400-qemu-11.1.3 \
        --inputs ~/a6400-inputs/inputs-moddev-1 --report-dir artifacts/qemu-a6400/NEW_DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from tools.research.qemu_boot_probe import Qmp
from tools.research.qemu_ep0_host import Ep0Host
from tools.research.qemu_ptp_device_info import get_device_info

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_MARKER = b"Created HeapAllocator:"
GET_DEVICE_DESCRIPTOR = "8006000100001200"
POWERED_SUBMICRO_SHA256 = "3385ec340e931aa7b7dbb954da99e5b6239174ba90441eb2c905f01bcf5bbda9"
CAPTURED_KERNEL_SHA256 = "d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618"
VPH_ENABLE, VPH_OSD_BASE = 0xF3440144, 0xF3441320
VPH_OSD_ENABLE = 1 << 1
STATE_COMMAND = ("echo __USB_STATE_{tag}__; grep -i -E 'dwc|usb|fusb' /proc/interrupts; "
                 "ls /sys/class/udc 2>&1; for f in /sys/class/udc/*/state /sys/class/udc/*/current_speed; "
                 "do echo $f=$(cat $f 2>&1); done; dmesg | grep -v AL_EMMC | grep -i -E 'dwc|udc|gadget|ep0|otg'; "
                 "echo __USB_STATE_{tag}_END__")
STOCK_OWNER_COMMAND = (
    "echo __USB_STATE_{tag}__; for n in im.elf gui.elf; do "
    "for p in $(pidof $n); do echo maps-$n-$p; "
    "grep -E 'lib(UsbControl|Obj|Infra(MscServer|MtpServer|Remote|UsbCmn))|viewUnified2' "
    "/proc/$p/maps; done; done; echo __USB_STATE_{tag}_END__"
)
FIRST_DISCONNECTED_JACK = re.compile(
    rb"^\[USB startup\] [^\n]*\boffset=00012980\b[^\n]*"
    rb"\br1=00000001 r2=00000000\b", re.M)


# DWC3 model registers read through the guest physical address space (the model's own read handler).
DWC3_REGISTERS = {"GCTL": 0xF020C110, "GEVNTADRLO0": 0xF020C400, "GEVNTSIZ0": 0xF020C408,
                  "GEVNTCOUNT0": 0xF020C40C, "DCFG": 0xF020C700, "DCTL": 0xF020C704,
                  "DEVTEN": 0xF020C708, "DSTS": 0xF020C70C, "DALEPENA": 0xF020C720}


def read_register(qmp, address):
    reply = qmp.request("human-monitor-command", {"command-line": f"xp /1wx {address:#x}"})
    text = str(reply)
    match = re.search(r":\s*(0x[0-9a-fA-F]+)", text)
    return match.group(1) if match else text.strip()


def read_registers(qmp) -> dict:
    return {name: read_register(qmp, address) for name, address in DWC3_REGISTERS.items()}


def stock_osd_armed(qmp):
    enabled = int(read_register(qmp, VPH_ENABLE), 16)
    base = int(read_register(qmp, VPH_OSD_BASE), 16)
    return bool(enabled & VPH_OSD_ENABLE and base)


def read_native_dwc3_context(qmp):
    """Read only the fields used before the first original DWC3 delay."""
    def kernel_word(va):
        if not 0x60000000 <= va < 0xA0000000 or va & 3:
            raise ValueError(f"DWC3 context address outside captured linear mapping: {va:#x}")
        return int(read_register(qmp, va - 0x60000000), 16)

    qmp.request("stop")
    try:
        if any(kernel_word(va) != expected for va, expected in (
                (0x602B1B9C, 0xE1A0C00D), (0x602B1BA0, 0xE92DD9F8), (0x602B1BAC, 0xEBFFF150))):
            raise ValueError("QEMU guest ARM kernel mapping does not match the pinned image")
        fusb = kernel_word(0x6051B024)
        binding = kernel_word(fusb + 0xBC)
        if binding != 0x604E4E30 or kernel_word(binding + 8) != 0x602B1B9C:
            raise ValueError("Original FUSB gadget binding is not live")
        dwc3 = kernel_word(binding + 4)
        return {
            "kernel_sha256": CAPTURED_KERNEL_SHA256,
            "kernel_bias": 0x60000000,
            "fusb": fusb,
            "fusb_state": kernel_word(fusb + 0x80),
            "cid": kernel_word(fusb + 0x84),
            "vbus": kernel_word(fusb + 0x88),
            "binding": binding,
            "callback": 0x602B1B9C,
            "dwc3": dwc3,
            "registers": kernel_word(dwc3 + 0x2B4),
            "outer_sync": kernel_word(0x604C92EC),
            "l2x0_registers": kernel_word(0x604F7824),
            "gctl": int(read_register(qmp, DWC3_REGISTERS["GCTL"]), 16),
        }
    finally:
        qmp.request("cont")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_setup(text: str) -> str:
    value = text.replace(" ", "").lower()
    if not re.fullmatch(r"[0-9a-f]{16}", value):
        raise argparse.ArgumentTypeError("SETUP must be exactly 8 bytes of hex")
    return value


def section(console: bytes, tag: str) -> str:
    begin, end = f"\n__USB_STATE_{tag}__".encode(), f"\n__USB_STATE_{tag}_END__".encode()
    start = console.rfind(begin)
    stop = console.find(end, start + 1) if start >= 0 else -1
    if start < 0 or stop < 0:
        return ""
    return console[start + len(begin):stop].decode("utf-8", "replace").replace("\r", "")


def run(args) -> dict:
    report_dir = args.report_dir.resolve()
    if not report_dir.is_relative_to((PROJECT_ROOT / "artifacts").resolve()):
        raise SystemExit("reports must stay under artifacts/")
    report_dir.mkdir(parents=True, exist_ok=False)
    binary = args.qemu_dir / "build/qemu-system-arm"
    report = dict(status="running", tool_sha256=digest(Path(__file__)), qemu_binary=str(binary),
                  qemu_binary_sha256=digest(binary), inputs=str(args.inputs),
                  inputs_manifest_sha256=digest(args.inputs / "boot-manifest.json"),
                  setup=args.setup,
                  plugin=str(args.plugin) if args.plugin else None,
                  plugin_sha256=digest(args.plugin) if args.plugin else None, machine_options=["dwc3-gadget=on", "usb-vbus-test=on", "usb-cid-high=on"],
                  host_lines=[], events=[],
                  captured_hwparams=args.captured_hwparams,
                  limitations=["QEMU model observation; the DWC3 model is a contract model, not hardware.",
                               "The host side sends only the listed lines; nothing is answered for the guest."])
    stock = args.stock_attach
    if stock:
        if args.powered_submicro.stat().st_size != 384:
            raise ValueError("Expected a 384-byte powered sub-micro replay")
        report.update(stock_attach=stock, sony_data_transfer_verified=False,
                      powered_submicro_sha256=digest(args.powered_submicro))
        if report["powered_submicro_sha256"] != POWERED_SUBMICRO_SHA256:
            raise ValueError("Powered sub-micro replay identity mismatch")
        if args.capture_dwc3_context and digest(args.inputs / "nflasha3-Image") != CAPTURED_KERNEL_SHA256:
            raise ValueError("Kernel identity mismatch for DWC3 context capture")
    with tempfile.TemporaryDirectory(prefix="a6400-usbhost-") as temp:
        scratch = Path(temp)
        monitor, host_path, console = scratch / "qmp", scratch / "usbhost", scratch / "console"
        plugin = None
        if args.plugin:   # run.sh splits QEMU_EXTRA on spaces: use a copy at a space-free path
            plugin = scratch / args.plugin.name
            plugin.write_bytes(args.plugin.read_bytes())
        helper = None
        if args.otg_mode_probe:
            helper = scratch / "otg-mode-probe"
            helper.write_bytes(args.otg_mode_probe.read_bytes())
            report["otg_mode_probe_sha256"] = digest(helper)
        env = dict(os.environ, ULOGIO="", TIMEOUT="0", DWC3_GADGET="1", USB_VBUS_TEST="1", USB_CID_HIGH="1",
                   EVSEL_LATE_US="500", A6400_QEMU_DIR=str(args.qemu_dir), A6400_QEMU_DATA_DIR=str(args.inputs),
                   RUN_DIR=str(scratch / "run"),
                   QEMU_EXTRA=(f"-icount shift=0,sleep=on -net none -no-reboot "
                               f"-qmp unix:{monitor},server=on,wait=off "
                               f"-chardev socket,id=usbhost,path={host_path},server=on,wait=off "
                               f"-global cxd90045-dwc3.chardev=usbhost"
                               + (" -global cxd90045-dwc3.captured-hwparams=on"
                                  if args.captured_hwparams else "")
                               + (f" -plugin {plugin}" if plugin else "")))
        if helper:
            env["SETTING_FILES"] = f"{helper}:OTGMODE.ELF"
        if stock:
            micro = scratch / "micro.bin"
            micro.write_bytes(args.powered_submicro.read_bytes())
            if digest(micro) != POWERED_SUBMICRO_SHA256:
                raise ValueError("Staged powered sub-micro replay identity mismatch")
            # Strip launcher overrides inherited from other emulator sessions.
            env = {name: env[name] for name in (
                "HOME", "PATH", "ULOGIO", "TIMEOUT", "DWC3_GADGET", "USB_VBUS_TEST",
                "USB_CID_HIGH", "EVSEL_LATE_US", "A6400_QEMU_DIR",
                "A6400_QEMU_DATA_DIR", "RUN_DIR", "QEMU_EXTRA")}
            env.update(SUBMICRO_FRAMES="", SUBMICRO_PAGE="", REAR_CONTROLS="1",
                       REAR_LCD_DIAGNOSTIC="0", SETTING_FILES="", AVCAM="",
                       SD_IMAGE="", SD_SHA256="", LIVEVIEW_SOURCE="",
                       SGC1_EVENT_A="1", CAMPIG_ENABLE_READBACK="1", SERIAL="stdio",
                       EXTRA="", LC_ALL="C", PYTHONDONTWRITEBYTECODE="1")
            if args.stock_liveview_bars:
                env["LIVEVIEW_SOURCE"] = "bars"
            env["QEMU_EXTRA"] += (f" -M submicro-frames={micro}"
                                  " -global cxd90045-dwc3.strict-ep0=on"
                                  " -global cxd90045-dwc3.captured-start-gate=on")
            if args.stock_ptp_info:
                env["QEMU_EXTRA"] += " -global cxd90045-dwc3.strict-bulk=on"
            report["stock_options"] = {name: env[name] for name in (
                "REAR_CONTROLS", "REAR_LCD_DIAGNOSTIC", "SGC1_EVENT_A",
                "CAMPIG_ENABLE_READBACK", "LIVEVIEW_SOURCE", "SETTING_FILES",
                "QEMU_EXTRA")}
        started = time.monotonic()
        deadline = started + args.seconds

        def note(kind, **data):
            report["events"].append(dict(t=round(time.monotonic() - started, 3), kind=kind, **data))

        with console.open("wb") as log:
            proc = subprocess.Popen(["bash", str(PROJECT_ROOT / "qemu-a6400/run.sh")], cwd=PROJECT_ROOT,
                                    env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            qmp, host = None, None
            rx = b""

            def pump(seconds):
                nonlocal rx
                end = min(deadline, time.monotonic() + seconds)
                while time.monotonic() < end:
                    if proc.poll() is not None:
                        raise RuntimeError(f"QEMU exited: {proc.returncode}")
                    if host is not None:
                        try:
                            chunk = host.recv(65536)
                            if chunk:
                                rx += chunk
                                while b"\n" in rx:
                                    line, rx = rx.split(b"\n", 1)
                                    text = line.decode("ascii", "replace")
                                    report["host_lines"].append(text)
                                    note("host_rx", line=text[:200])
                        except socket.timeout:
                            pass
                    else:
                        time.sleep(0.2)

            def wait_until(condition, timeout, label):
                end = min(deadline, time.monotonic() + timeout)
                while not condition():
                    if time.monotonic() >= end:
                        raise TimeoutError(f"{label} not observed")
                    pump(0.2)

            def guest(command, tag, timeout=40):
                for byte in (command.format(tag=tag) + "\n").encode():
                    proc.stdin.write(bytes([byte]))
                    proc.stdin.flush()
                    time.sleep(0.01)
                wait_until(lambda: f"\n__USB_STATE_{tag}_END__".encode() in console.read_bytes(), timeout, tag)
                report[f"guest_{tag.lower()}"] = section(console.read_bytes(), tag)

            def host_send(line):
                host.sendall((line + "\n").encode("ascii"))
                note("host_tx", line=line)

            def cable(present):
                qmp.request("qom-set", {"path": "/machine", "property": "usb-vbus", "value": present})
                if stock:
                    readback = qmp.request("qom-get", {"path": "/machine", "property": "usb-vbus"})
                    if readback is not present:
                        raise RuntimeError(f"VBUS readback disagrees with requested state {present}")
                note("vbus", present=present)

            def lcd(tag):
                image = scratch / f"{tag}.ppm"
                qmp.request("screendump", {"filename": str(image), "device": "vph", "head": 0})
                pixels = image.read_bytes()
                (report_dir / image.name).write_bytes(pixels)
                note("lcd", tag=tag, sha256=hashlib.sha256(pixels).hexdigest())

            try:
                wait_until(monitor.exists, 30, "QMP socket")
                qmp = Qmp(monitor, deadline + 10)
                if stock == "before-firmware":
                    cable(True)
                elif stock == "before-first-view":
                    wait_until(lambda: FIRST_DISCONNECTED_JACK.search(console.read_bytes()),
                               170, "original boot-time SetJackStatus(1,0)")
                    note("original_jack_initialized")
                    cable(True)
                wait_until(lambda: FIRMWARE_MARKER in console.read_bytes(), 150 if stock else 120,
                           "firmware startup marker")
                note("firmware_marker")
                wait_until(lambda: b"/ # " in console.read_bytes(), 30, "guest shell")
                if stock:
                    wait_until(lambda: stock_osd_armed(qmp), 120, "original OSD plane")
                    lcd("before_center")
                    qmp.request("send-key", {
                        "keys": [{"type": "qcode", "data": "ret"}], "hold-time": 180,
                    })
                    note("rear_center")
                    pump(8)
                    if args.stock_playback:
                        qmp.request("send-key", {
                            "keys": [{"type": "qcode", "data": "p"}], "hold-time": 250,
                        })
                        note("rear_play")
                        pump(8)
                        if args.stock_resume_shooting:
                            qmp.request("send-key", {
                                "keys": [{"type": "qcode", "data": "s"}], "hold-time": 250,
                            })
                            note("rear_shutter_half_press")
                            pump(8)
                    lcd("before_attach")
                    guest(STOCK_OWNER_COMMAND, "OWNERS_BEFORE")
                guest(STATE_COMMAND, "BEFORE")
                report["registers_before"] = read_registers(qmp)
                if helper:
                    guest("echo __USB_STATE_MODE__; /setting/OTGMODE.ELF --offline-qemu-only; "
                          "echo helper_exit=$?; "
                          "echo __USB_STATE_MODE_END__", "MODE")
                    report["registers_mode"] = read_registers(qmp)
                host = socket.socket(socket.AF_UNIX)
                host.connect(str(host_path))
                host.settimeout(0.2)
                note("host_connected")
                cable(True)
                pump(args.settle)
                report["registers_vbus"] = read_registers(qmp)
                if args.capture_dwc3_context:
                    report["native_dwc3_context"] = read_native_dwc3_context(qmp)
                host_send("CONN")
                pump(args.settle)
                report["registers_conn"] = read_registers(qmp)
                run_observed = bool(stock and int(report["registers_conn"]["DCTL"], 16) & (1 << 31))
                if args.stock_ptp_info and run_observed:
                    client = Ep0Host(host, deadline=deadline)
                    try:
                        report["enumeration"] = client.enumerate_device()
                        guest(STATE_COMMAND, "CONFIGURED")
                        if "/sys/class/udc/dwc3.0/state=configured" not in report["guest_configured"]:
                            raise ValueError("Original UDC did not enter configured state")
                        guest(STOCK_OWNER_COMMAND, "PTP_OWNER")
                        if not re.search(r"/libInfraRemote\.so\s*$", report["guest_ptp_owner"], re.M):
                            raise ValueError("Original PC Remote service is not mapped")
                        report["ptp_device_info"] = get_device_info(client, report["enumeration"])
                        report["sony_data_transfer_verified"] = True
                    finally:
                        report["token_events"] = client.events
                elif args.setup:
                    host_send("SETUP " + args.setup)
                    pump(args.reply_wait)
                guest(STATE_COMMAND, "ATTACHED")
                if stock:
                    lcd("attached")
                    guest(STOCK_OWNER_COMMAND, "OWNERS_ATTACHED")
                    if not report["sony_data_transfer_verified"]:
                        report["stock_attach_boundary"] = (
                            "no_udc_run" if not run_observed
                            else "stock_enumeration_and_data_not_verified")
                host_send("DISC")
                cable(False)
                pump(5)
                guest(STATE_COMMAND, "AFTER")
                report["dwc3_trace"] = [line for line in console.read_bytes().decode("utf-8", "replace").splitlines()
                                        if line.startswith("[DWC3 trace]")]
                report["in_replies"] = [line for line in report["host_lines"] if line.startswith("IN ")]
                qmp.request("quit")
                report["qemu_exit"] = proc.wait(10)
                if report["qemu_exit"] != 0:
                    raise RuntimeError(f"QEMU exit status {report['qemu_exit']}")
                report["status"] = ("boundary" if stock and not report["sony_data_transfer_verified"]
                                    else "observed")
            except Exception as error:  # report every stop with its reason
                report["status"] = "stopped"
                report["stop_reason"] = f"{type(error).__name__}: {error}"
            finally:
                if host:
                    host.close()
                if qmp:
                    qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(10)
                report["owned_exit"] = proc.poll()
                report["elapsed_seconds"] = round(time.monotonic() - started, 1)
                (report_dir / "console.log").write_bytes(console.read_bytes())
                (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--qemu-dir", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument("--settle", type=float, default=8.0, help="seconds after VBUS and after CONN")
    parser.add_argument("--reply-wait", type=float, default=10.0, help="seconds to collect IN lines after SETUP")
    parser.add_argument("--setup", type=parse_setup, default=GET_DEVICE_DESCRIPTOR,
                        help="8-byte SETUP packet in hex, or empty to send none")
    parser.add_argument("--plugin", type=Path, help="optional passive QEMU TCG plugin (for example dwc3_trace.so)")
    parser.add_argument("--otg-mode-probe", type=Path,
                        help="offline-only ARM helper: issue original OTG SET_MODE(2) before attach")
    parser.add_argument("--captured-hwparams", action="store_true",
                        help="use E0595 physical ILCE-6400 DWC3 hardware-parameter registers")
    parser.add_argument("--stock-attach", choices=("before-firmware", "before-first-view", "after-notice"),
                        help="insert cable before firmware startup, at native jack initialization, "
                             "or after the OSD and a rear-center key")
    parser.add_argument("--powered-submicro", type=Path,
                        help="hash-pinned powered A/C/B replay extracted for E0591")
    parser.add_argument("--stock-ptp-info", action="store_true",
                        help="require an original PC Remote owner, PTP data and response")
    parser.add_argument("--capture-dwc3-context", action="store_true",
                        help="read the original emulated kernel's bound DWC3 prefix fields")
    parser.add_argument("--stock-liveview-bars", action="store_true",
                        help="pair stock USB observation with the existing sensor-luma fixture")
    parser.add_argument("--stock-playback", action="store_true",
                        help="press the original Play button before late cable insertion")
    parser.add_argument("--stock-resume-shooting", action="store_true",
                        help="leave playback using the original half-press input")
    args = parser.parse_args(argv)
    if bool(args.stock_attach) != bool(args.powered_submicro):
        parser.error("--stock-attach and --powered-submicro must be used together")
    if args.stock_attach and args.otg_mode_probe:
        parser.error("stock attach cannot use the OTG mode helper")
    if args.stock_attach == "before-first-view" and not args.plugin:
        parser.error("the first-view schedule needs a matching passive startup trace plugin")
    if args.stock_ptp_info and not args.stock_attach:
        parser.error("--stock-ptp-info requires --stock-attach")
    if args.capture_dwc3_context and not args.stock_attach:
        parser.error("--capture-dwc3-context requires --stock-attach")
    if args.stock_liveview_bars and not args.stock_attach:
        parser.error("--stock-liveview-bars requires --stock-attach")
    if args.stock_playback and args.stock_attach != "after-notice":
        parser.error("--stock-playback requires the late --stock-attach after-notice schedule")
    if args.stock_resume_shooting and not args.stock_playback:
        parser.error("--stock-resume-shooting requires --stock-playback")
    report = run(args)
    print(json.dumps({k: report.get(k) for k in (
        "status", "stop_reason", "stock_attach_boundary", "in_replies", "elapsed_seconds")}, indent=2))
    return 0 if report["status"] == "observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
