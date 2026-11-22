import sys
import os
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
pmca_path = os.path.join(repo_root, "Sony-PMCA-RE")
if pmca_path not in sys.path:
    sys.path.insert(0, pmca_path)

from pmca.commands.usb import senserShellCommand

def run_cmd(cam, cmd, timeout=10.0):
    for _ in range(5):
        cam.dev.readTerminal()
        time.sleep(0.05)
    full = f"{cmd}; echo ___CMD_DONE___\n"
    cam.dev.writeTerminal(full.encode('latin1'))
    out = b""
    start = time.time()
    while time.time() - start < timeout:
        d = cam.dev.readTerminal()
        if d:
            out += d
            if b"___CMD_DONE___" in out:
                time.sleep(0.1)
                d2 = cam.dev.readTerminal()
                if d2:
                    out += d2
                break
        time.sleep(0.05)
    return out.decode('latin1', errors='replace')

def execute_deploy(cam):
    print("[+] Connected to Sony ZV-E10 in Senser mode!")

    guard_path = os.path.join(repo_root, "artifacts/hardware/zve10/native_menu_touch_full_guard")
    check_req = os.path.join(repo_root, "artifacts/hardware/zve10/menu-touch-full-check.req")
    install_req = os.path.join(repo_root, "artifacts/hardware/zve10/menu-touch-full-install.req")

    print("[*] Uploading native_menu_touch_full_guard (static musl ARM) to /tmp/mt_guard...")
    with open(guard_path, "rb") as f:
        cam.writeFile("/tmp/mt_guard", f.read())

    print("[*] Uploading check.req to /tmp/mt_check.req...")
    with open(check_req, "rb") as f:
        cam.writeFile("/tmp/mt_check.req", f.read())

    print("[*] Uploading install.req to /tmp/mt_install.req...")
    with open(install_req, "rb") as f:
        cam.writeFile("/tmp/mt_install.req", f.read())

    print("[*] Activating terminal...")
    cam.setTerminalEnable(False)
    time.sleep(0.2)
    cam.setTerminalEnable(True)
    time.sleep(1.0)
    for _ in range(15):
        cam.dev.readTerminal()
        time.sleep(0.05)

    print("[*] Setting executable permissions...")
    run_cmd(cam, "chmod 755 /tmp/mt_guard")

    print("\n" + "=" * 60)
    print("=== STEP 1: PREFLIGHT BOUNDS CHECK (OP=0) ===")
    print("=" * 60)
    res_check = run_cmd(cam, "/tmp/mt_guard /tmp/mt_check.req", timeout=8.0)
    print(res_check)
    if "result=OK" not in res_check and "result=0" not in res_check:
        print("[-] Preflight check FAILED! Aborting installation.")
        run_cmd(cam, "rm -f /tmp/mt_* /tmp/native_menu_touch_full_guard /tmp/menu_*.req")
        return

    print("\n" + "=" * 60)
    print("=== STEP 2: LIVE TOUCH MOD INSTALLATION (OP=1) ===")
    print("=" * 60)
    res_install = run_cmd(cam, "/tmp/mt_guard /tmp/mt_install.req", timeout=8.0)
    print(res_install)
    if "result=SUCCESS" not in res_install and "result=0" not in res_install:
        print("[-] Installation FAILED!")
        run_cmd(cam, "rm -f /tmp/mt_* /tmp/native_menu_touch_full_guard /tmp/menu_*.req")
        return

    print("\n" + "=" * 60)
    print("=== STEP 3: CLEANUP TEMPORARY FILES FROM /tmp ===")
    print("=" * 60)
    res_clean = run_cmd(cam, "rm -f /tmp/mt_* /tmp/native_menu_touch_full_guard /tmp/menu_*.req && ls -la /tmp")
    print(res_clean)

    print("\n" + "=" * 60)
    print("=== STEP 4: VERIFY im.elf STATUS ===")
    print("=" * 60)
    res_ps = run_cmd(cam, "ps | grep im.elf")
    print(res_ps)

    try:
        cam.setTerminalEnable(False)
    except Exception:
        pass
    print("\n[+] Senser deployment complete! Mod is active in im.elf DRAM.")

if __name__ == '__main__':
    senserShellCommand(driverName='libusb', complete=execute_deploy)
