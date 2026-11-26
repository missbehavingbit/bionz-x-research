import sys
import os
import struct
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
pmca_path = os.path.join(repo_root, "Sony-PMCA-RE")
if pmca_path not in sys.path:
    sys.path.insert(0, pmca_path)

from pmca.commands.usb import senserShellCommand

ERR_MAP = {
    0: "NO_ERROR / NOT_RUN",
    1: "TAB_SELECT_FAILED",
    2: "PAGE_SELECT_FAILED",
    3: "ROW_SELECT_FAILED",
    4: "ENTER_LEAF_FAILED",
    98: "MOUSE_ID_GE_4",
    99: "COORDS_INVALID_32767",
    101: "LOOKUP_RETURNED_ERR",
    102: "LOOKUP_MENU_IS_NULL",
    103: "MENU_VTABLE_MISMATCH",
    104: "SELF_PTR_MISMATCH",
    105: "MENU_MODE_BYTE_21_NOT_2",
    106: "INHERITED_MODE_GT_2",
    107: "MAIN_STATE_1E0_NOT_2",
    108: "ROOT_NODE_186AC_IS_NULL",
    109: "ROOT_NODE_VTABLE_MISMATCH",
    112: "TABS_COUNT_OUT_OF_RANGE",
    113: "TAB_SELECTED_INDEX_FAILED",
    114: "TAB_BAR_FOCUS_ACTIVE",
    115: "TAB_NODE_INVALID",
    116: "PAGE_NODE_INVALID",
    117: "ROW_SELECTION_INVALID",
    118: "MENU_ROW_INDEX_MISMATCH",
    151: "FN_VIEW_NOT_RESOLVED",
    152: "FN_VTABLE_MISMATCH",
    153: "VM_PTR_INVALID",
    154: "VM_COUNT_OUT_OF_RANGE",
    155: "VM_ENTRIES_INVALID",
    156: "FN_VIEW_NOT_FOUND_IN_ENTRIES",
    199: "NO_WIDGET_HIT_AT_COORDINATES",
    201: "POPUP_NODE_NULL",
    202: "POPUP_NODE_VTABLE_MISMATCH",
    203: "POPUP_COUNTS_OUT_OF_RANGE",
    204: "POPUP_NO_WIDGET_HIT",
}

ACT_MAP = {
    0: "NONE",
    1: "TAB_SWITCH",
    2: "PAGE_SWITCH",
    3: "ROW_SELECT",
    4: "ROW_ENTER",
    5: "SWIPE_PAGE",
    6: "POPUP_SELECT",
    7: "POPUP_SCROLL",
    8: "POPUP_CANCEL",
    10: "FN_SELECT",
    11: "FN_ENTER",
    12: "FN_CANCEL",
    100: "ENTERED_HANDLE_MENU_TOUCH",
    110: "IN_POPUP",
    120: "REACHED_HIT_TESTS",
    150: "ENTERED_HANDLE_FN_TOUCH",
}

def run_cmd(cam, cmd, timeout=5.0):
    while True:
        d = cam.dev.readTerminal()
        if not d:
            break
    cam.dev.writeTerminal((cmd + "\n").encode('latin1'))
    time.sleep(0.3)
    out = b""
    start = time.time()
    while time.time() - start < timeout:
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

def execute_dump(cam):
    print("[+] Reading state block from camera DRAM...")
    time.sleep(1.0)

    cam.setTerminalEnable(False)
    time.sleep(0.2)
    cam.setTerminalEnable(True)
    time.sleep(1.0)
    for _ in range(15):
        cam.dev.readTerminal()
        time.sleep(0.05)

    # 1. State address is at 0x06592000 + 0x013f8000 = 0x0798a000
    state_addr = 0x0798a000
    print(f"[*] State block address: 0x{state_addr:08x}")

    # Inspect GOT entry for g_pEvtMgr at lib_base + 0x13ded14 = 0x07970d14
    got_evt = 0x06592000 + 0x13ded14
    dump_got = run_cmd(cam, f"dd if=/proc/157/mem bs=1 skip={got_evt} count=16 2>/dev/null | busybox xxd")
    print(f"\n=== GOT ENTRY FOR g_pEvtMgr AT 0x{got_evt:08x} ===")
    print(dump_got)

    dump_cmd = f"dd if=/proc/157/mem bs=1 skip={state_addr} count=656 2>/dev/null | busybox xxd -p"
    out_dump = run_cmd(cam, dump_cmd)
    
    # Extract hex string
    hex_str = ""
    for line in out_dump.splitlines():
        line = line.strip()
        if line and all(c in "0123456789abcdefABCDEF" for c in line):
            hex_str += line

    if len(hex_str) < 160:
        print(f"[-] Truncated hex received: {hex_str}")
        print(out_dump)
        return

    raw = bytes.fromhex(hex_str[:1312])
    fields = struct.unpack("<IIIIIIIIiiIiiIIIIIII", raw[:80])
    (magic, version, enabled, count, inflight, capacity,
     last_action, last_target, last_x, last_y, last_err,
     press_x, press_y, press_valid, lib_base, view_base, caution_base, cached_menu,
     view4_base, cached_fn_view) = fields

    print("\n" + "=" * 55)
    print("=== SONY ZV-E10 TOUCH MOD DRAM TELEMETRY ===")
    print("=" * 55)
    print(f"Magic:         0x{magic:08x} ({'MTF2' if magic == 0x4d544632 else 'INVALID'})")
    print(f"Version:       {version}")
    print(f"Enabled:       {enabled}")
    print(f"Event Count:   {count}")
    print(f"Inflight:      {inflight}")
    print(f"Capacity:      {capacity}")
    print(f"Last Action:   {last_action} ({ACT_MAP.get(last_action, 'UNKNOWN')})")
    print(f"Last Target:   0x{last_target:08x} ({last_target})")
    print(f"Last Coord:    ({last_x}, {last_y})")
    print(f"Last Error:    {last_err} ({ERR_MAP.get(last_err, 'UNKNOWN')})")
    print(f"Press Coord:   ({press_x}, {press_y})")
    print(f"Press Valid:   {press_valid}")
    print(f"libObj Base:   0x{lib_base:08x}")
    print(f"viewUnified:   0x{view_base:08x}")
    print(f"CautionConfig: 0x{caution_base:08x}")
    print(f"Cached Menu:   0x{cached_menu:08x}")
    print(f"viewUnified4:  0x{view4_base:08x}")
    print(f"Cached ViewFn: 0x{cached_fn_view:08x}")
    print("=" * 55)

    print("\n=== RECENT EVENTS (LAST 16) ===")
    ev_raw = raw[80:80 + 16 * 36]
    for i in range(16):
        chunk = ev_raw[i*36:(i+1)*36]
        if len(chunk) < 36:
            break
        seq, tid, act, tgt, ex, ey, tab, page, row = struct.unpack("<IIIIiiIII", chunk)
        if seq:
            act_str = ACT_MAP.get(act, f"ACT_{act}")
            print(f"[{seq:03d}] {act_str:<14} tgt=0x{tgt:08x} pos=({ex:3d},{ey:3d}) tab={tab} page={page} row={row}")
    print("=" * 55)

    try:
        cam.setTerminalEnable(False)
    except Exception:
        pass

if __name__ == '__main__':
    senserShellCommand(driverName='libusb', complete=execute_dump)
