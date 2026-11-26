import sys
import os
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
pmca_path = os.path.join(repo_root, "Sony-PMCA-RE")
if pmca_path not in sys.path:
    sys.path.insert(0, pmca_path)

from pmca.commands.usb import senserShellCommand

def run_cmd(cam, cmd):
    while True:
        d = cam.dev.readTerminal()
        if not d:
            break
    cam.dev.writeTerminal((cmd + "\n").encode('latin1'))
    time.sleep(0.5)
    out = b""
    for _ in range(60):
        d = cam.dev.readTerminal()
        if d:
            out += d
            if b"/usr/share/app # " in out or b"# " in out:
                time.sleep(0.1)
                d2 = cam.dev.readTerminal()
                if d2:
                    out += d2
                break
        time.sleep(0.05)
    return out.decode('latin1', errors='replace')

def execute_install(cam):
    print("[+] Connected to Sony ZV-E10 in Senser mode!")
    guard_path = os.path.join(repo_root, "artifacts/hardware/zve10/native_menu_touch_full_guard")
    install_req = os.path.join(repo_root, "artifacts/hardware/zve10/menu-touch-full-install.req")

    print("[*] Uploading native_menu_touch_full_guard to /tmp (RAM)...")
    with open(guard_path, "rb") as f:
        cam.writeFile("/tmp/native_menu_touch_full_guard", f.read())

    print("[*] Uploading menu-touch-full-install.req to /tmp/menu_touch_full.req...")
    with open(install_req, "rb") as f:
        cam.writeFile("/tmp/menu_touch_full.req", f.read())

    print("[*] Activating terminal...")
    cam.setTerminalEnable(False)
    time.sleep(0.1)
    cam.setTerminalEnable(True)
    time.sleep(0.4)
    for _ in range(10):
        cam.dev.readTerminal()
        time.sleep(0.05)

    print("[*] Setting executable permissions...")
    run_cmd(cam, "chmod 755 /tmp/native_menu_touch_full_guard")

    print("\n" + "=" * 60)
    print("=== EXECUTING OP=1 LIVE TOUCH MOD INSTALLATION ===")
    print("=" * 60)
    result = run_cmd(cam, "/tmp/native_menu_touch_full_guard /tmp/menu_touch_full.req")
    print(result)

    print("[*] Cleaning up /tmp...")
    run_cmd(cam, "rm -f /tmp/native_menu_touch_full_guard /tmp/menu_*.req")

    try:
        cam.setTerminalEnable(False)
    except Exception:
        pass
    print("[+] Senser session finished.")

if __name__ == '__main__':
    senserShellCommand(driverName='libusb', complete=execute_install)
