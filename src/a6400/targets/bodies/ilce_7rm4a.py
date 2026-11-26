"""Sony ILCE-7RM4A (a7R IVA) offline-sourced target profile with a read-only USB identity."""
from __future__ import annotations

from src.a6400.targets.bodies._common import E0480, E0570, E0646, E0678, ref
from src.a6400.targets.profile import (
    EvidenceRef,
    PID_TIER_OBSERVED,
    PID_TIER_STOCK,
    STATUS_REFERENCE,
    TargetProfile,
    UsbIdentity,
    UsbPid,
)

ILCE_7RM4A = TargetProfile(
    target_id="ilce_7rm4a",
    model_name="Sony ILCE-7RM4A (a7R IVA)",
    status=STATUS_REFERENCE,
    soc_verified=True,
    product_code="0032853030",
    firmware_version="1.13",
    kernel_version="3.0.27_nl-rt106+ SMP preempt mod_unload ARMv7",
    kallsyms={
        "usb_gadgetcore_ioctl": 0x602BC860,
        "usb_gadgetcore_register_driver": 0x602BC62C,
        "usb_gadgetcore_unregister_driver": 0x602BC580,
        "usb_gcw_start": 0x602C1814,
        "usb_gcw_stop": 0x602C1850,
        "setup_req": 0x602BD4C0,
        "make_config_desc": 0x602BFC70,
    },
    uipc_queues={},
    usb=UsbIdentity(
        vid=0x054C,
        pids={
            "stock_0448": UsbPid(0x0448, PID_TIER_STOCK),
            "stock_047d": UsbPid(0x047D, PID_TIER_STOCK),
            "stock_0bee": UsbPid(0x0BEE, PID_TIER_STOCK),
            "stock_0bef": UsbPid(0x0BEF, PID_TIER_STOCK),
            "stock_0bfe": UsbPid(0x0BFE, PID_TIER_STOCK),
            "stock_0bff": UsbPid(0x0BFF, PID_TIER_STOCK),
            "stock_0c13": UsbPid(0x0C13, PID_TIER_STOCK),
            "stock_0c14": UsbPid(0x0C14, PID_TIER_STOCK),
            "stock_0ca7": UsbPid(0x0CA7, PID_TIER_STOCK),
            "stock_0ca8": UsbPid(0x0CA8, PID_TIER_STOCK),
            "stock_0cc9": UsbPid(0x0CC9, PID_TIER_STOCK),
            "stock_0cca": UsbPid(0x0CCA, PID_TIER_STOCK),
            "stock_0d0d": UsbPid(0x0D0D, PID_TIER_STOCK),
            "stock_0d0e": UsbPid(0x0D0E, PID_TIER_STOCK),
            "stock_0d11": UsbPid(0x0D11, PID_TIER_STOCK),
            "stock_0d12": UsbPid(0x0D12, PID_TIER_STOCK),
            "stock_0d9c": UsbPid(0x0D9C, PID_TIER_STOCK),
            "stock_0d9d": UsbPid(0x0D9D, PID_TIER_STOCK),
            "mass_storage": UsbPid(0x0D9D, PID_TIER_OBSERVED),
            "pc_remote": UsbPid(0x0D9F, PID_TIER_OBSERVED),
        },
        bcd_device=0x0200,
    ),
    modules={},
    sources={
        "product_code": ref(E0646, "physical GetModelInfo on 1.13: opaque five-byte code 0032853030; not updater fdat.model"),
        "firmware_version": ref(E0570, "packaged updater 1.13; config.yaml fdat.version '1.13', model 0x91030003"),
        "kernel_version": ref(E0570, "vermagic in this body's own system/vmlinux.bin 8d02896458cd8927... (build Sony CE Linux 8.2.7.6, jp10239@dic-build-scm-u20-019, 2026-01-31)"),
        "kallsyms.usb_gadgetcore_ioctl": ref(E0570, "decoded from this body's system/vmlinux.bin 8d02896458cd8927...; matches E0480"),
        "kallsyms.usb_gadgetcore_register_driver": ref(E0570, "decoded from this body's system/vmlinux.bin 8d02896458cd8927...; matches E0480"),
        "kallsyms.usb_gadgetcore_unregister_driver": ref(E0570, "decoded from this body's system/vmlinux.bin 8d02896458cd8927... (not listed in E0480)"),
        "kallsyms.usb_gcw_start": ref(E0480, "decoded from this body's system/vmlinux.bin, confirmed in E0570"),
        "kallsyms.usb_gcw_stop": ref(E0480, "decoded from this body's system/vmlinux.bin, confirmed in E0570"),
        "kallsyms.setup_req": ref(E0480, "decoded from this body's system/vmlinux.bin, confirmed in E0570"),
        "kallsyms.make_config_desc": ref(E0480, "decoded from this body's system/vmlinux.bin, confirmed in E0570"),
        "usb": (
            EvidenceRef(E0570, "normalUsbDescriptors in this body's config.yaml b511141a6266ceb0...; 18 pairs, vid 0x54C"),
            EvidenceRef(E0646, "read-only Windows PnP on 1.13: Mass Storage 054C:0D9D, REV_0200, USBSTOR"),
            EvidenceRef(E0646, "read-only Windows PnP on 1.12 and 1.13: PC Remote 054C:0D9F, host driver libusbK"),
            EvidenceRef(E0678, "libInfraUsbCmn.so 96638679... ptbl_did DID 0x00 -> BK4 unit 0x00E7002E = 0x0D9D, "
                               "DID 0x3B -> unit 0x00E70035 = 0x0D9F in the packaged 1.13 presets"),
        ),
    },
    boundaries=(
        "The kernel, symbol, devno and descriptor claims are offline packaged firmware only (E0570) and are "
        "exact for the packaged 1.13 image, not for an arbitrary boot. E0570's note that the owner's body "
        "ran the current release was wrong: it ran 1.12 and was updated to 1.13 by its owner before E0646.",
        "E0646 adds only host-observed USB facts and one GetModelInfo reply from the owner's body on 1.13. "
        "No live kernel, module, memory, /proc or partition read was performed; the displayed 1.13 version "
        "and USB identity do not establish the live kernel or module bytes.",
        "PC Remote 0x0D9F is host-observed and absent from the 18 packaged normalUsbDescriptors pairs; "
        "E0678 finds it as BK4 preset unit 0x00E70035, named by DID 0x3B of the stock USB service's ptbl_did, "
        "which the original DescriptorMaintainer::execOverwrite stores into that DID's descriptor buffer. Only "
        "the DID-to-mode mapping is a value match with host observations. Stock descriptor membership does not "
        "identify a mode; MTP and UVC were not observed.",
        "The observed wire product_code 0032853030 and updater fdat.model 0x91030003 are distinct namespaces.",
        "A resolved kallsyms symbol is presence evidence, not loadability or callability proof.",
        "uipc_queues is empty: the a6400's queue handles come from a live /proc/osal/uipc read and are "
        "per-boot, so no offline value is claimed here.",
        "soc_verified rests on the package's own crypterName: CXD90045 statement, which is stronger than "
        "a model name and weaker than reading /proc/cpuinfo on the body.",
        "This body's kernel is distinct from the a6400's: E0477 counts it among the nine kernels and E0480 "
        "shows its gadget-core symbols sit at their own addresses (exactly 0x4 from the a7 III and 0x1000 "
        "from the ILCE-9 on all six shared symbols). Do not copy an address between these bodies.",
        "devno.ko (74096566ca4132ef..., 88 entries) publishes 17 /dev/usb nodes and no usb/video or "
        "usb/audio row, so a kernel-side UVC node does not exist on this body (E0570; module-package "
        "absence recorded platform-wide in E0479). The same 88-entry table is byte-identical to the "
        "ILCE-6100A devno.ko (E0482) within the 8.2.7.6 build generation; that is a measured equality on "
        "this body's own file, not a copied value.",
        "No modules are claimed: this packaged image carries no UVC module package (E0479), and the module "
        "set was not inventoried against a live load.",
    ),
    notes=(
        "Offline-sourced from its own packaged updater (ILCE-7RM4A firmware 1.13). The a7R IVA is one of "
        "the thirteen BIONZ X bodies mapped in E0477; this record turns that map into a registered profile. "
        "av-cam command table: 333 records at 0x1096180 in this image (E0570), matching E0477. "
        "E0646 records the first physical read-only identity from the owner's body on 1.13: Mass Storage "
        "054C:0D9D, PC Remote 054C:0D9F, and a native GetModelInfo reply ILCE-7RM4A after one Sony BUSY."
    ),
)
