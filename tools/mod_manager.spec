# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for building standalone Windows Sony BIONZ X Mod Manager."""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(os.environ.get("BIONZX_REPO_ROOT", os.getcwd())).resolve()
PMCA_DIR = Path(os.environ["BIONZX_PMCA_DIR"]).resolve() if os.environ.get("BIONZX_PMCA_DIR") else None
MLV2_DIR = Path(os.environ["BIONZX_MLV2_DIR"]).resolve() if os.environ.get("BIONZX_MLV2_DIR") else None
ARRIVEDERCI_PATH = (
    Path(os.environ["BIONZX_ARRIVEDERCI_PATH"]).resolve()
    if os.environ.get("BIONZX_ARRIVEDERCI_PATH")
    else ROOT / "mods" / "arri_isp" / "arrivederci" / "Arrivederci.mod"
)
INCLUDE_DBG = os.environ.get("BIONZX_INCLUDE_DEBUGGER_BIN", "0") == "1"
ONEFILE = os.environ.get("BIONZX_ONEFILE", "0") == "1"

datas = [
    (str(ROOT / "tools" / "mod_manager_ui"), "tools/mod_manager_ui"),
    (str(ROOT / "mods" / "menu_touch_full" / "MenuTouchFull.mod"), "bundled_mods"),
    (str(ARRIVEDERCI_PATH), "bundled_mods"),
    (str(ROOT / "mods" / "open_gate" / "OpenGateSensor.mod"), "bundled_mods"),
    (str(ROOT / "mods" / "open_gate" / "OpenGate32.mod"), "bundled_mods"),
]
binaries = []
hiddenimports = [
    "src.a6400.mod_manager",
    "src.a6400.targets.usb_debugger_compat",
    "src.a6400.winusb_transport",
    "src.a6400.usb_debug_ring",
    "tools.camera_debugger",
    "tools.deploy_native_usb_debugger",
    "tools.setup_usb_debugger",
    "tools.research.reload_mod",
    "tools.research.elf_metadata_identity",
    "tkinter",
    "tkinter.filedialog",
]

for pkg in ("webview", "libusb_package", "certifi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

for pkg in ("usb", "Cryptodome", "elftools", "comtypes", "yaml", "asn1crypto", "axmlparserpy", "tlslite"):
    hiddenimports += collect_submodules(pkg)

hiddenimports += ["config", "win32file", "win32api", "win32con", "pywintypes"]

pathex = [str(ROOT)]
if PMCA_DIR and (PMCA_DIR / "pmca").is_dir() and (PMCA_DIR / "config.py").is_file():
    pathex.append(str(PMCA_DIR))
    datas.append((str(PMCA_DIR / "config.py"), "."))
    datas.append((str(PMCA_DIR / "pmca"), "pmca"))
    if (PMCA_DIR / "certs").is_dir():
        datas.append((str(PMCA_DIR / "certs"), "certs"))
    hiddenimports += collect_submodules("pmca")

if MLV2_DIR and MLV2_DIR.is_dir():
    for fname in ("core.bin", "mlv2.req", "mlv2_guard_arm", "build.json", "shim.bin"):
        candidate = MLV2_DIR / fname
        if candidate.is_file():
            datas.append((str(candidate), "bundled_tools/mlv2"))

if INCLUDE_DBG:
    for rel in (
        "devcrashday_a6400/a6400_usb_debugd.elf",
        "devcrashday_a6400/hook/usb_debug_launcher.so",
    ):
        candidate = ROOT / rel
        if candidate.is_file():
            datas.append((str(candidate), str(Path(rel).parent).replace("\\", "/")))

a = Analysis(
    [str(ROOT / "tools" / "mod_manager_gui.py")],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unicorn", "capstone", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="BionzX-Mod-Manager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="BionzX-Mod-Manager",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="BionzX-Mod-Manager",
    )
