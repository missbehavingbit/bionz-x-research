"""Read-only camera storage dump GUI for the native USB debugger.

Uses tools/camera_debugger.py (OP_READ / OP_INFO / OP_USB_STATE / OP_DMESG only).
Nothing is ever written to the camera: no exec, no push, no mode switches.

Two modes:
  * Single pull - one camera path (e.g. /dev/nflasha1 or /proc/mtd) to one local file
  * Full dump   - every non-zero partition from /proc/partitions plus system metadata

Partitions backing /setting (Backup.bin) and /cert are tagged PRIVATE and written to a
separate private/ subfolder: they hold your serial number, device keys and Wi-Fi credentials.

Launch from the repository root with python -m tools.camera_dump_gui.
"""
import contextlib
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'tools'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    import camera_debugger as cd
except Exception as exc:  # reported in the GUI so double-click users see it
    cd = None
    CD_IMPORT_ERROR = f'{type(exc).__name__}: {exc}'
else:
    CD_IMPORT_ERROR = None

SETTINGS_FILE = Path(__file__).with_name('camera_dump_gui.settings.json')


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_settings(**values):
    data = load_settings()
    data.update(values)
    with contextlib.suppress(Exception):
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


def pmca_root(folder):
    """Return the folder that contains the pmca package, or None.

    Accepts the Sony-PMCA-RE checkout itself, the pmca package folder inside it,
    or a parent folder that has Sony-PMCA-RE in it.
    """
    if not folder:
        return None
    folder = Path(folder)
    for candidate in (folder, folder.parent, folder / 'Sony-PMCA-RE'):
        if (candidate / 'pmca' / 'usb' / 'sony.py').is_file():
            return candidate.resolve()
    return None


def use_pmca(folder):
    """Put a pmca checkout on sys.path. Returns (root, error_text)."""
    root = pmca_root(folder)
    if root is None:
        return None, (f'No pmca package found in {folder}. Pick the Sony-PMCA-RE folder '
                      '(the one containing pmca\\usb\\sony.py).')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for name in [m for m in sys.modules if m == 'pmca' or m.startswith('pmca.')]:
        del sys.modules[name]
    import importlib
    importlib.invalidate_caches()
    try:
        importlib.import_module('pmca.usb.sony')
        importlib.import_module('pmca.commands.usb')
    except Exception as exc:
        return root, f'Found pmca in {root} but it failed to import: {type(exc).__name__}: {exc}'
    return root, None


def pmca_available():
    import importlib.util
    return importlib.util.find_spec('pmca') is not None


def autodetect_pmca():
    saved = load_settings().get('pmca_folder')
    for folder in (saved, ROOT / 'Sony-PMCA-RE', ROOT.parent / 'Sony-PMCA-RE', ROOT / 'tools' / 'Sony-PMCA-RE'):
        if folder and pmca_root(folder):
            root, err = use_pmca(folder)
            return str(root), err
    return (saved or ''), None

CHUNK = cd.MAX_DATA if cd else 4060
# OP_READ offsets are 31-bit on the wire (see Debugger.read); anything past this
# cannot be addressed and is reported as truncated rather than silently padded.
OFFSET_LIMIT = 0x7fffffff
PRIVATE_MOUNTS = ('/setting', '/cert')
SKIP_PREFIXES = ('ram', 'loop', 'zram', 'nbd', 'mtdblock')
METADATA_FILES = ('/proc/partitions', '/proc/mounts', '/proc/mtd', '/proc/cmdline',
                  '/proc/version', '/proc/cpuinfo', '/proc/meminfo', '/proc/devices',
                  '/proc/filesystems', '/proc/iomem', '/proc/diskstats')
RETRIES = 4
# Limit direct device reads to known storage node names. The debugger opens
# arbitrary absolute paths, including devices that can reset the camera.
STORAGE_PATH = re.compile(
    r'/dev/(?:nflasha[0-9]+|mmca[0-9]+(?:p[0-9]+)?|mmcblk[0-9]+(?:p[0-9]+)?)'
)
EXCLUDED_STORAGE = {'/dev/nflasha23'}


def dump_source_allowed(path):
    return path in METADATA_FILES or (
        path not in EXCLUDED_STORAGE and STORAGE_PATH.fullmatch(path) is not None
    )

OPTION_HELP = {
    'whole': ('Include whole-disk devices',
              'Also pull each supported whole disk as one image, in addition to '
              'its partitions. It contains every partition plus the partition table and any '
              'unpartitioned gaps, so it is the most complete copy, but it duplicates the data '
              'and takes as long as all partitions together. Off by default.'),
    'cards': ('Include memory cards (SD)',
              'Also pull the inserted memory card (mmca10 / mmca10p1). That is your photos and '
              'videos, not camera system storage. A 128 GB card would take days over this USB '
              'debugger, and only its first 2 GiB can be read. A USB card reader is far faster. '
              'Cards count as PRIVATE: they go into the private/ folder, and "Skip PRIVATE" '
              'leaves them out. Off by default.'),
    'private': ('Skip PRIVATE partitions (/setting, /cert, cards)',
                'Leave out the partitions mounted at /setting (Backup.bin) and /cert, which hold '
                'your serial number, device keys and Wi-Fi credentials, and any memory card '
                '(photos and videos with GPS, faces and the camera serial in their metadata). '
                'When they are pulled they go into a separate private/ folder - never share that '
                'folder. Tick this if you plan to share the whole dump.'),
    'meta': ('Also save metadata (/proc, dmesg, usb-state)',
             'Save small text files describing the camera: partition table, mounts, kernel '
             'version, boot command line, CPU and memory info, plus the kernel log and USB '
             'debugger state. Useful for analysing the dump later; takes a few seconds. '
             'dmesg and usb-state need debugger protocol v2 - on a V1 daemon they are logged as '
             'unavailable, which is harmless.'),
    'transport': ('Transport',
                  'How the PC talks to the camera. auto tries WinUSB first and falls back to the '
                  'Sony ext-command (needs the PMCA folder). Leave on auto unless one of them fails.'),
    'pmca': ('PMCA folder',
             'Your Sony-PMCA-RE checkout (the folder containing pmca\\usb\\sony.py). The ext-command '
             'USB transport uses its USB drivers. Remembered between runs.'),
}


# ---------------------------------------------------------------- parsing
def parse_partitions(text):
    """/proc/partitions -> [{'name','blocks','bytes','major','minor'}]."""
    parts = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 4 or not fields[0].isdigit():
            continue
        major, minor, blocks, name = int(fields[0]), int(fields[1]), int(fields[2]), fields[3]
        parts.append({'name': name, 'major': major, 'minor': minor,
                      'blocks': blocks, 'bytes': blocks * 1024})
    return parts


def parse_mounts(text):
    """/proc/mounts -> {device_basename: [(mountpoint, fstype, opts)]}."""
    mounts = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].startswith('/dev/'):
            continue
        dev = fields[0].rsplit('/', 1)[-1]
        mounts.setdefault(dev, []).append((fields[1], fields[2], fields[3]))
    return mounts


def _parents(name, names):
    return [o for o in names if o != name and re.fullmatch(re.escape(o) + r'p?\d+', name)]


def is_whole_disk(name, names):
    """True for a disk whose partitions are also listed (mmcblk0 vs mmcblk0p1, nflasha vs nflasha1).

    A name that is itself a partition of another listed disk never counts, so
    nflasha1 stays a partition even though nflasha12 starts with it.
    """
    if _parents(name, names):
        return False
    return any(other != name and re.fullmatch(re.escape(name) + r'p?\d+', other) for other in names)


def parent_disk(name, names):
    """mmca10p1 -> mmca10, nflasha3 -> nflasha; a whole disk is its own parent."""
    parents = _parents(name, names)
    return min(parents, key=len) if parents else name   # nflasha12 -> nflasha, not nflasha1


CARD_TYPES = ('SD', 'SDIO', 'MS', 'MSPRO')   # /sys/block/<disk>/device/type values for cards
CARD_FSTYPES = ('exfat', 'texfat')            # fallback when sysfs says nothing
CARD_MIN_GUESS = 32 * (1 << 30)                # last resort: >32 GiB and not known eMMC


def is_card(name, names, mounts, disk_info, size=0):
    """True when the partition sits on a removable memory card (SD / Memory Stick)."""
    info = disk_info.get(parent_disk(name, names), {})
    if info.get('type', '').upper() in CARD_TYPES or info.get('removable') == '1':
        return True
    if info.get('type', '').upper() == 'MMC':   # soldered eMMC (e.g. ILCE-7C system storage)
        return False
    if any(m[1] in CARD_FSTYPES for m in mounts):
        return True
    # No conclusive sysfs answer and not mounted (in Mass Storage mode the PC owns the
    # card; Sony's mmca driver reports removable=0 even for the SD slot). Camera system
    # storage is far smaller than this, memory cards are not.
    return size > CARD_MIN_GUESS


def plan_dump(parts, mounts, include_whole_disks=False, skip_private=False,
              disk_info=None, include_cards=False):
    names = {p['name'] for p in parts}
    disk_info = disk_info or {}
    plan = []
    for p in parts:
        entry = dict(p)
        entry['mounts'] = mounts.get(p['name'], [])
        entry['private'] = any(m[0] == pm or m[0].startswith(pm + '/')
                               for m in entry['mounts'] for pm in PRIVATE_MOUNTS)
        entry['whole_disk'] = is_whole_disk(p['name'], names)
        entry['card'] = is_card(p['name'], names, entry['mounts'], disk_info, p['bytes'])
        # Photos/videos carry GPS, faces and the camera serial in their metadata.
        entry['private'] = entry['private'] or entry['card']
        reason = None
        if not dump_source_allowed('/dev/' + p['name']):
            reason = 'storage path is not allowed'
        elif p['bytes'] == 0:
            reason = 'zero size'
        elif p['name'].startswith(SKIP_PREFIXES):
            reason = 'virtual device'
        elif entry['card'] and not include_cards:
            reason = 'memory card (photos, not system storage)'
        elif entry['whole_disk'] and not include_whole_disks:
            reason = 'whole disk (partitions pulled individually)'
        elif entry['private'] and skip_private:
            reason = 'private (skipped by option)'
        entry['skip'] = reason
        plan.append(entry)
    return plan


def human(n):
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if abs(n) < 1024 or unit == 'GiB':
            return f'{n:.1f} {unit}' if unit != 'B' else f'{n} B'
        n /= 1024


def safe_name(camera_path):
    return camera_path.strip('/').replace('/', '_') or 'root'


# ---------------------------------------------------------------- engine
class Cancelled(Exception):
    pass


class PullError(Exception):
    pass


class Session:
    """Holds one debugger connection and reopens it after USB hiccups."""

    def __init__(self, transport, log):
        self.transport = transport
        self.log = log
        self.stack = None
        self.debugger = None

    def open(self):
        self.close()
        self.stack = contextlib.ExitStack()
        self.debugger = self.stack.enter_context(cd.connect(self.transport))
        return self.debugger

    def close(self):
        if self.stack:
            with contextlib.suppress(Exception):
                self.stack.close()
        self.stack = None
        self.debugger = None

    def call(self, fn, *args, cancel=None):
        last = None
        for attempt in range(RETRIES):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            try:
                if self.debugger is None:
                    self.open()
                return fn(self.debugger, *args)
            except cd.DebuggerError:
                raise  # camera said no (e.g. ENOENT) - retrying will not help
            except (ValueError, ImportError):
                raise  # missing pmca etc. - reconnecting will not fix it
            except Exception as exc:
                last = exc
                self.log(f'  USB error ({type(exc).__name__}: {exc}); reconnecting '
                         f'[{attempt + 1}/{RETRIES}]')
                self.close()
                time.sleep(1.0 + attempt)
        raise last


def pull(session, camera_path, local_path, expected=None, cancel=None, progress=None):
    """Read camera_path into local_path via OP_READ. Resumable through a .part file.

    Returns a manifest record. Never pads or fabricates bytes.
    """
    if not dump_source_allowed(camera_path):
        raise PullError(f'{camera_path} is outside the supported storage and metadata paths.')
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    part = local_path.with_name(local_path.name + '.part')
    sha = hashlib.sha256()
    offset = 0
    if part.exists():
        with part.open('rb') as fh:
            for block in iter(lambda: fh.read(1 << 20), b''):
                sha.update(block)
                offset += len(block)
        if expected and offset > expected:
            part.unlink()
            sha, offset = hashlib.sha256(), 0
    resumed_from = offset
    limit = expected if expected else None
    truncated = False
    started = time.monotonic()
    with part.open('ab') as out:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            size = CHUNK if limit is None else min(CHUNK, limit - offset)
            if size <= 0:
                break
            if offset > OFFSET_LIMIT - size:
                size = OFFSET_LIMIT - offset
                if size <= 0:
                    truncated = True
                    break
            try:
                block = session.call(
                    lambda d, o=offset, s=size: d.read(camera_path, o, s), cancel=cancel
                )
            except Cancelled:
                raise
            except Exception as exc:
                raise PullError(f'{camera_path}: {exc}') from exc
            if not block:
                break
            out.write(block)
            sha.update(block)
            offset += len(block)
            if progress:
                elapsed = max(time.monotonic() - started, 1e-3)
                progress(offset, expected, (offset - resumed_from) / elapsed)
    record = {'camera_path': camera_path, 'local': str(local_path), 'bytes': offset,
              'expected_bytes': expected, 'sha256': sha.hexdigest(),
              'resumed_from': resumed_from or None,
              'truncated_at_2GiB': truncated,
              'complete': (not truncated) and (expected is None or offset == expected)}
    if not record['complete']:
        record['local'] = str(part)
        return record
    os.replace(part, local_path)
    return record


def read_text(session, path, cancel=None, limit=2 * 1024 * 1024):
    data, _ = session.call(lambda d: d.read_all(path, limit), cancel=cancel)
    return data.decode('utf-8', 'replace')


# ---------------------------------------------------------------- GUI
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title('Sony camera storage dump (read-only)')
    root.geometry('980x700')
    root.minsize(820, 560)

    class Tooltip:
        """Small hover popup; appears after a short delay, follows nothing, wraps text."""
        def __init__(self, widget, text):
            self.widget, self.text, self.tip, self.job = widget, text, None, None
            widget.bind('<Enter>', self.schedule, add='+')
            widget.bind('<Leave>', self.hide, add='+')
            widget.bind('<ButtonPress>', self.hide, add='+')

        def schedule(self, _e=None):
            self.hide()
            self.job = self.widget.after(400, self.show)

        def show(self):
            x = self.widget.winfo_rootx() + 10
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry(f'+{x}+{y}')
            tk.Label(self.tip, text=self.text, justify='left', wraplength=380,
                     background='#ffffe0', relief='solid', borderwidth=1,
                     padx=6, pady=4).pack()

        def hide(self, _e=None):
            if self.job:
                self.widget.after_cancel(self.job)
                self.job = None
            if self.tip:
                self.tip.destroy()
                self.tip = None

    def tip(widget, key):
        Tooltip(widget, OPTION_HELP[key][1])
        return widget

    def show_help():
        text = '\n\n'.join(f'{title}\n{body}' for key, (title, body) in OPTION_HELP.items()
                            if key in ('whole', 'cards', 'private', 'meta'))
        messagebox.showinfo('Full dump options', text)

    events = queue.Queue()
    cancel = threading.Event()
    state = {'plan': [], 'busy': False}

    out_var = tk.StringVar(value=str(ROOT / 'artifacts' / 'dumps'))
    transport_var = tk.StringVar(value='auto')
    single_src = tk.StringVar(value='/dev/nflasha1')
    single_dst = tk.StringVar(value='nflasha1.raw')
    whole_var = tk.BooleanVar(value=False)
    skip_priv_var = tk.BooleanVar(value=False)
    cards_var = tk.BooleanVar(value=False)
    meta_var = tk.BooleanVar(value=True)
    status_var = tk.StringVar(value='Not connected')
    pmca_folder, pmca_err = autodetect_pmca()
    pmca_var = tk.StringVar(value=pmca_folder)

    def log(msg):
        events.put(('log', msg))

    def apply_pmca(folder, quiet=False):
        root_dir, err = use_pmca(folder)
        if err:
            log(f'PMCA: {err}')
            if not quiet:
                messagebox.showerror('PMCA folder', err)
            return False
        pmca_var.set(str(root_dir))
        save_settings(pmca_folder=str(root_dir))
        log(f'PMCA: using {root_dir}')
        return True

    def browse_pmca():
        folder = filedialog.askdirectory(
            title='Select the Sony-PMCA-RE folder (contains pmca\\usb\\sony.py)',
            initialdir=pmca_var.get() or str(ROOT))
        if folder:
            apply_pmca(folder)

    # --- top bar
    top = ttk.Frame(root, padding=8)
    top.pack(fill='x')
    ttk.Label(top, text='Output folder').grid(row=0, column=0, sticky='w')
    ttk.Entry(top, textvariable=out_var).grid(row=0, column=1, sticky='ew', padx=4)
    ttk.Button(top, text='Browse...', command=lambda: out_var.set(
        filedialog.askdirectory(initialdir=out_var.get()) or out_var.get())).grid(row=0, column=2)
    ttk.Label(top, text='Transport').grid(row=0, column=3, padx=(12, 2))
    tip(ttk.Combobox(top, textvariable=transport_var, values=('auto', 'extcmd', 'winusb'),
                     width=8, state='readonly'), 'transport').grid(row=0, column=4)
    connect_btn = ttk.Button(top, text='Connect & scan')
    connect_btn.grid(row=0, column=5, padx=(12, 0))
    ttk.Label(top, text='PMCA folder').grid(row=1, column=0, sticky='w', pady=(4, 0))
    pmca_entry = tip(ttk.Entry(top, textvariable=pmca_var), 'pmca')
    pmca_entry.grid(row=1, column=1, sticky='ew', padx=4, pady=(4, 0))
    pmca_entry.bind('<Return>', lambda _e: apply_pmca(pmca_var.get()))
    ttk.Button(top, text='Browse...', command=browse_pmca).grid(row=1, column=2, pady=(4, 0))
    top.columnconfigure(1, weight=1)
    ttk.Label(top, textvariable=status_var, foreground='#555').grid(
        row=2, column=0, columnspan=6, sticky='w', pady=(4, 0))

    nb = ttk.Notebook(root)
    nb.pack(fill='both', expand=True, padx=8)

    # --- single pull tab
    single = ttk.Frame(nb, padding=10)
    nb.add(single, text='Single pull')
    ttk.Label(single, text='Camera path').grid(row=0, column=0, sticky='w')
    ttk.Entry(single, textvariable=single_src, width=50).grid(row=0, column=1, sticky='ew', padx=4)
    ttk.Label(single, text='Save as (in output folder)').grid(row=1, column=0, sticky='w', pady=4)
    ttk.Entry(single, textvariable=single_dst, width=50).grid(row=1, column=1, sticky='ew', padx=4)
    pull_btn = ttk.Button(single, text='Pull')
    pull_btn.grid(row=2, column=1, sticky='w', padx=4, pady=6)
    ttk.Label(single, foreground='#555', wraplength=700, justify='left', text=(
        'Start with a small partition (/dev/nflasha1) to confirm direct device reads work. '
        'Supported metadata files include /proc/mtd and /proc/partitions. Partition sizes come from '
        '/proc/partitions after "Connect & scan"; otherwise reads run until the camera returns EOF. '
        'Tip: double-click a row on the Full dump tab to fill this in.')).grid(
        row=3, column=0, columnspan=2, sticky='w')
    single.columnconfigure(1, weight=1)

    # --- full dump tab
    full = ttk.Frame(nb, padding=10)
    nb.add(full, text='Full dump')
    opts = ttk.Frame(full)
    opts.pack(fill='x')
    for i, (key, var, replans) in enumerate((('whole', whole_var, True), ('cards', cards_var, True),
                                              ('private', skip_priv_var, True), ('meta', meta_var, False))):
        cb = ttk.Checkbutton(opts, text=OPTION_HELP[key][0], variable=var,
                             command=(lambda: replan()) if replans else None)
        cb.grid(row=i // 2, column=i % 2, sticky='w', padx=(0, 24), pady=1)
        tip(cb, key)
    ttk.Button(opts, text='?  What do these do?', command=show_help).grid(
        row=0, column=2, sticky='e', padx=(12, 0))
    dump_btn = ttk.Button(opts, text='Start full dump')
    dump_btn.grid(row=1, column=2, sticky='e', padx=(12, 0))
    opts.columnconfigure(2, weight=1)
    ttk.Label(full, foreground='#555', text='Hover over an option for details.').pack(anchor='w')

    cols = ('size', 'mount', 'flag', 'status')
    tree = ttk.Treeview(full, columns=cols, height=10)
    tree.heading('#0', text='Partition')
    for c, w in zip(cols, (100, 300, 90, 260)):
        tree.heading(c, text=c.capitalize())
        tree.column(c, width=w, anchor='w')
    tree.column('#0', width=140)
    tree.tag_configure('skip', foreground='#999')
    tree.tag_configure('private', foreground='#b00')
    tree.pack(fill='both', expand=True, pady=6)
    summary_var = tk.StringVar(value='Connect & scan to list partitions.')
    ttk.Label(full, textvariable=summary_var).pack(anchor='w')

    def on_double(_e):
        sel = tree.focus()
        if sel:
            single_src.set('/dev/' + sel)
            single_dst.set(sel + '.raw')
            nb.select(single)
    tree.bind('<Double-1>', on_double)

    # --- progress + log
    bottom = ttk.Frame(root, padding=8)
    bottom.pack(fill='both')
    prog = ttk.Progressbar(bottom, mode='determinate', maximum=1000)
    prog.pack(fill='x')
    prog_var = tk.StringVar(value='')
    row = ttk.Frame(bottom)
    row.pack(fill='x')
    ttk.Label(row, textvariable=prog_var).pack(side='left')
    cancel_btn = ttk.Button(row, text='Cancel', state='disabled', command=lambda: cancel.set())
    cancel_btn.pack(side='right')
    logbox = tk.Text(bottom, height=11, wrap='word', font=('Consolas', 9))
    logbox.pack(fill='both', expand=True, pady=(4, 0))

    def replan():
        if not state.get('parts'):
            return
        state['plan'] = plan_dump(state['parts'], state['mounts'], whole_var.get(), skip_priv_var.get(),
                                  state.get('disk_info'), cards_var.get())
        tree.delete(*tree.get_children())
        total = 0
        for e in state['plan']:
            mount = ', '.join(f'{m[0]} ({m[1]})' for m in e['mounts'])
            tags = ('skip',) if e['skip'] else (('private',) if e['private'] else ())
            if not e['skip']:
                total += e['bytes']
            tree.insert('', 'end', iid=e['name'], text=e['name'], tags=tags,
                        values=(human(e['bytes']), mount,
                                'PRIVATE (card)' if e['card'] else ('PRIVATE' if e['private'] else ''),
                                f'skip: {e["skip"]}' if e['skip'] else 'queued'))
        n = sum(1 for e in state['plan'] if not e['skip'])
        big = [e['name'] for e in state['plan'] if not e['skip'] and e['bytes'] > OFFSET_LIMIT]
        txt = f'{n} partitions queued, {human(total)} total.'
        if big:
            txt += f'  Over 2 GiB (only first 2 GiB readable via OP_READ): {", ".join(big)}'
        summary_var.set(txt)

    def set_busy(busy):
        state['busy'] = busy
        for b in (connect_btn, pull_btn, dump_btn):
            b.configure(state='disabled' if busy else 'normal')
        cancel_btn.configure(state='normal' if busy else 'disabled')

    def start(worker):
        if state['busy']:
            return
        if cd is None:
            messagebox.showerror('camera_debugger not found',
                                 f'Could not import tools/camera_debugger.py:\n{CD_IMPORT_ERROR}')
            return
        # extcmd (and auto's fallback) needs Sony-PMCA-RE's pmca package; winusb does not.
        if transport_var.get() != 'winusb' and not pmca_available():
            if pmca_var.get() and apply_pmca(pmca_var.get(), quiet=True):
                pass
            else:
                messagebox.showinfo('PMCA folder needed',
                                    'The USB transport needs the Sony-PMCA-RE "pmca" package.\n'
                                    'Select your Sony-PMCA-RE folder (the one containing pmca\\usb\\sony.py).')
                browse_pmca()
                if not pmca_available():
                    return
        cancel.clear()
        set_busy(True)

        def wrapped():
            session = Session(transport_var.get(), log)
            try:
                worker(session)
            except Cancelled:
                log('Cancelled. Partial files are kept as .part and resume on the next run.')
            except Exception as exc:
                log(f'ERROR: {type(exc).__name__}: {exc}')
                events.put(('warning', ('Camera operation failed', str(exc))))
            finally:
                session.close()
                events.put(('done', None))
        threading.Thread(target=wrapped, daemon=True).start()

    def progress_cb(label):
        def cb(done, expected, rate):
            events.put(('progress', (label, done, expected, rate)))
        return cb

    # --- workers
    def scan(session):
        log('Connecting...')
        session.open()
        info = session.call(lambda d: d.request(cd.OP_INFO)['payload'].decode('utf-8', 'replace'))
        events.put(('status', 'Connected: ' + ' '.join(info.split())[:150]))
        log(info.strip())
        parts_txt = read_text(session, '/proc/partitions', cancel)
        mounts_txt = read_text(session, '/proc/mounts', cancel)
        parts = parse_partitions(parts_txt)
        names = {p['name'] for p in parts}
        disk_info = {}
        for disk in sorted({parent_disk(p['name'], names) for p in parts}):
            if disk.startswith(SKIP_PREFIXES):
                continue
            info = {}
            for key, path in (('type', f'/sys/block/{disk}/device/type'),
                              ('removable', f'/sys/block/{disk}/removable')):
                try:
                    info[key] = read_text(session, path, cancel, limit=64).strip()
                except Exception:
                    pass
            disk_info[disk] = info
            if info:
                log(f'  {disk}: ' + ', '.join(f'{k}={v}' for k, v in info.items()))
        events.put(('scan', (parts, parse_mounts(mounts_txt), disk_info)))
        log(parts_txt.strip())
        log(f'Found {len(parse_partitions(parts_txt))} block devices.')

    def do_single(session):
        src = single_src.get().strip()
        dst = Path(out_var.get()) / (single_dst.get().strip() or safe_name(src))
        expected = None
        name = src.rsplit('/', 1)[-1]
        for p in state.get('parts') or []:
            if src == '/dev/' + p['name']:
                expected = p['bytes']
        log(f'Pulling {src} -> {dst}' + (f' ({human(expected)})' if expected else ''))
        try:
            rec = pull(session, src, dst, expected, cancel, progress_cb(name))
        except PullError as exc:
            log(f'Skipped: {exc}')
            events.put(('warning', ('Pull skipped', str(exc))))
            return
        log(f'Done: {human(rec["bytes"])}  sha256={rec["sha256"]}' +
            ('' if rec['complete'] else '  [INCOMPLETE]'))
        if not rec['complete']:
            events.put(('warning', ('Incomplete pull', f'{src}: the partial file was kept.')))
            return
        Path(str(dst) + '.sha256').write_text(f'{rec["sha256"]}  {dst.name}\n', encoding='utf-8')

    def do_full(session):
        if not state.get('plan'):
            scan(session)
            time.sleep(0.3)  # let the UI thread build the plan
            for _ in range(50):
                if state.get('plan'):
                    break
                time.sleep(0.1)
        plan = [e for e in state['plan'] if not e['skip']]
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        base = Path(out_var.get()) / f'dump-{stamp}'
        resume = sorted(Path(out_var.get()).glob('dump-*/manifest.partial.json'))
        if resume:
            base = resume[-1].parent
            log(f'Resuming unfinished dump in {base}')
        base.mkdir(parents=True, exist_ok=True)
        manifest = {'created': stamp, 'tool': 'camera_dump_gui (read-only OP_READ)',
                    'transport': session.transport, 'partitions': [], 'metadata': []}
        mpath = base / 'manifest.partial.json'

        def save():
            mpath.write_text(json.dumps(manifest, indent=2), encoding='utf-8')

        if meta_var.get():
            meta = base / 'metadata'
            meta.mkdir(exist_ok=True)
            info = session.call(lambda d: d.request(cd.OP_INFO)['payload'])
            (meta / 'debugger-info.txt').write_bytes(info)
            for fn, label in ((lambda d: d.usb_state().encode(), 'usb-state.txt'),
                              (lambda d: d.dmesg()[0], 'dmesg.txt')):
                try:
                    (meta / label).write_bytes(session.call(fn, cancel=cancel))
                except Exception as exc:
                    log(f'  {label}: {exc}')
            for path in METADATA_FILES:
                try:
                    data, trunc = session.call(lambda d, p=path: d.read_all(p), cancel=cancel)
                    (meta / safe_name(path)).write_bytes(data)
                    manifest['metadata'].append({'camera_path': path, 'bytes': len(data),
                                                 'truncated': trunc})
                except cd.DebuggerError as exc:
                    log(f'  {path}: not available ({exc})')
            log(f'Metadata saved to {meta}')
            save()

        total = sum(e['bytes'] for e in plan)
        done_before = 0
        for i, e in enumerate(plan, 1):
            sub = base / ('private' if e['private'] else 'partitions')
            dst = sub / f'{e["name"]}.raw'
            events.put(('row', (e['name'], 'pulling...')))
            if dst.exists() and dst.stat().st_size == e['bytes']:
                log(f'[{i}/{len(plan)}] {e["name"]} already complete, skipping')
                events.put(('row', (e['name'], 'done (previous run)')))
                sha = hashlib.sha256()
                with dst.open('rb') as fh:
                    for block in iter(lambda: fh.read(1 << 20), b''):
                        sha.update(block)
                manifest['partitions'].append({
                    'camera_path': f'/dev/{e["name"]}', 'local': str(dst), 'bytes': e['bytes'],
                    'expected_bytes': e['bytes'], 'sha256': sha.hexdigest(), 'complete': True,
                    'name': e['name'], 'private': e['private'], 'mounts': e['mounts'],
                    'from_previous_run': True})
                save()
                done_before += e['bytes']
                continue
            log(f'[{i}/{len(plan)}] /dev/{e["name"]} ({human(e["bytes"])})'
                + ('  PRIVATE' if e['private'] else ''))

            def cb(done, expected, rate, name=e['name'], offset=done_before):
                events.put(('progress', (f'{name}  |  overall {human(offset + done)} / {human(total)}',
                                         offset + done, total, rate)))
            try:
                rec = pull(session, f'/dev/{e["name"]}', dst, e['bytes'], cancel, cb)
            except PullError as exc:
                log(f'  failed: {exc}')
                events.put(('row', (e['name'], f'error: {exc}'[:60])))
                manifest['partitions'].append({'name': e['name'], 'error': str(exc)})
                save()
                done_before += e['bytes']
                continue
            rec.update(name=e['name'], private=e['private'], mounts=e['mounts'])
            manifest['partitions'].append(rec)
            save()
            done_before += e['bytes']
            flag = 'done' if rec['complete'] else ('2 GiB limit' if rec['truncated_at_2GiB'] else 'short read')
            events.put(('row', (e['name'], f'{flag}  {rec["sha256"][:12]}')))
            log(f'  {flag}: {human(rec["bytes"])} sha256={rec["sha256"]}')

        with (base / 'SHA256SUMS').open('w', encoding='utf-8') as fh:
            for rec in manifest['partitions']:
                if rec.get('complete'):
                    rel = Path(rec['local']).relative_to(base).as_posix()
                    fh.write(f'{rec["sha256"]}  {rel}\n')
        os.replace(mpath, base / 'manifest.json')
        bad = [r['name'] for r in manifest['partitions'] if not r.get('complete')]
        log(f'Full dump finished: {base}')
        if bad:
            log(f'Incomplete or failed: {", ".join(bad)}')
            details = [
                f'{rec["name"]}: {rec.get("error", "incomplete read")}'
                for rec in manifest['partitions'] if not rec.get('complete')
            ]
            events.put(('warning', ('Incomplete dump', '\n'.join(details))))
        if any(e['private'] for e in plan):
            log('Reminder: keep the private/ folder to yourself (serial number, device keys, Wi-Fi credentials).')

    connect_btn.configure(command=lambda: start(scan))
    pull_btn.configure(command=lambda: start(do_single))
    dump_btn.configure(command=lambda: start(do_full))

    # --- event pump
    def pump():
        try:
            while True:
                kind, val = events.get_nowait()
                if kind == 'log':
                    logbox.insert('end', val + '\n')
                    logbox.see('end')
                elif kind == 'status':
                    status_var.set(val)
                elif kind == 'scan':
                    state['parts'], state['mounts'], state['disk_info'] = val
                    replan()
                elif kind == 'row':
                    name, text = val
                    if tree.exists(name):
                        tree.set(name, 'status', text)
                elif kind == 'progress':
                    label, done, expected, rate = val
                    if expected:
                        prog.configure(value=1000 * done / expected)
                        eta = (expected - done) / rate if rate > 0 else 0
                        prog_var.set(f'{label}  {100 * done / expected:.1f}%  {human(rate)}/s  '
                                     f'ETA {int(eta // 3600)}h{int(eta % 3600 // 60):02d}m')
                    else:
                        prog.configure(mode='indeterminate')
                        prog_var.set(f'{label}  {human(done)}  {human(rate)}/s')
                elif kind == 'done':
                    set_busy(False)
                    prog.configure(mode='determinate')
                elif kind == 'warning':
                    title, text = val
                    messagebox.showwarning(title, text, parent=root)
        except queue.Empty:
            pass
        root.after(100, pump)

    if pmca_err:
        log(f'PMCA: {pmca_err}')
    elif pmca_var.get() and pmca_available():
        log(f'PMCA: using {pmca_var.get()}')
    else:
        log('PMCA: not found. Use "Browse..." next to PMCA folder and pick your Sony-PMCA-RE folder.')
    if CD_IMPORT_ERROR:
        log(f'WARNING: could not import tools/camera_debugger.py: {CD_IMPORT_ERROR}')
    log('Read-only: this tool only uses OP_READ/INFO/USB_STATE/DMESG. Camera must be in '
        'Mass Storage mode with the USB debugger installed (not PC Remote).')
    pump()
    root.mainloop()


if __name__ == '__main__':
    run_gui()
