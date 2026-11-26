"""Unit tests for CXD90045 SDHCI / eMMC controller model."""
import struct
import unittest

from src.a6400.devices.cxd90045_sdhci import (
    CxdSdhciEmmc,
    EMMC0_CAPAREG,
    EMMC0_IRQ,
    INT_CMD_COMPLETE,
    INT_XFER_COMPLETE,
    SDHCI_ADMA_ADDRESS,
    SDHCI_ARGUMENT,
    SDHCI_BLOCK_COUNT,
    SDHCI_BLOCK_SIZE,
    SDHCI_CAPABILITIES,
    SDHCI_CLOCK_CONTROL,
    SDHCI_COMMAND,
    SDHCI_HOST_CONTROL,
    SDHCI_INT_ENABLE,
    SDHCI_INT_STATUS,
    SDHCI_RESPONSE_0,
    SDHCI_SIGNAL_ENABLE,
    SDHCI_TRANSFER_MODE,
)


class CxdSdhciEmmcTests(unittest.TestCase):
    def test_cmd1_sector_mode_and_adma2_read(self):
        # 8 MiB boot area + 2 sectors user area
        backing = bytearray(8 * 1024 * 1024 + 1024)
        backing[8 * 1024 * 1024:8 * 1024 * 1024 + 4] = b'SDM0'
        ram = bytearray(0x4000)
        irqs = {}

        dev = CxdSdhciEmmc(
            set_irq=lambda irq, lvl: irqs.__setitem__(irq, lvl),
            mem_read=lambda addr, n: bytes(ram[addr:addr + n]),
            mem_write=lambda addr, data: ram.__setitem__(slice(addr, addr + len(data)), data),
            image_bytes=bytes(backing),
        )
        self.assertEqual(dev.read(SDHCI_CAPABILITIES, 4), EMMC0_CAPAREG)
        dev.write(SDHCI_CLOCK_CONTROL, 0x0001, 2)
        self.assertEqual(dev.read(SDHCI_CLOCK_CONTROL, 2) & 0x02, 0x02)

        dev.write(SDHCI_INT_ENABLE, 0xFFFFFFFF, 4)
        dev.write(SDHCI_SIGNAL_ENABLE, 0xFFFFFFFF, 4)

        # CMD1 with sector-mode bit 30 (0x40000080)
        dev.write(SDHCI_ARGUMENT, 0x40000080, 4)
        dev.write(SDHCI_COMMAND, 1 << 8, 2)
        self.assertEqual(dev.read(SDHCI_RESPONSE_0, 4) & 0xC0000000, 0xC0000000)
        self.assertTrue(irqs.get(EMMC0_IRQ))
        dev.write(SDHCI_INT_STATUS, INT_CMD_COMPLETE, 4)
        self.assertFalse(irqs.get(EMMC0_IRQ))

        # ADMA2 descriptor at 0x100: 512 bytes -> 0x2000, valid|end|transfer (0x23)
        struct.pack_into('<II', ram, 0x100, (512 << 16) | 0x23, 0x2000)
        dev.write(SDHCI_HOST_CONTROL, 0x10, 1)  # ADMA2
        dev.write(SDHCI_ADMA_ADDRESS, 0x100, 4)
        dev.write(SDHCI_BLOCK_SIZE, 512, 2)
        dev.write(SDHCI_BLOCK_COUNT, 1, 2)
        dev.write(SDHCI_TRANSFER_MODE, 0x0011, 2)  # DMA + read
        dev.write(SDHCI_ARGUMENT, 0, 4)  # LBA 0 -> user_offset
        dev.write(SDHCI_COMMAND, 17 << 8, 2)

        self.assertEqual(bytes(ram[0x2000:0x2004]), b'SDM0')
        self.assertEqual(dev.read(SDHCI_INT_STATUS, 4) & (INT_CMD_COMPLETE | INT_XFER_COMPLETE),
                         INT_CMD_COMPLETE | INT_XFER_COMPLETE)
        self.assertTrue(irqs.get(EMMC0_IRQ))

    def test_err_int_status_halfword_rw1c_and_cow_write_overlay(self):
        backing = bytearray(8 * 1024 * 1024 + 1024)
        backing[0:4] = b'EXBL'
        backing[8 * 1024 * 1024:8 * 1024 * 1024 + 4] = b'USER'
        frozen_backing = bytes(backing)
        ram = bytearray(0x4000)

        dev = CxdSdhciEmmc(
            set_irq=lambda irq, lvl: None,
            mem_read=lambda addr, n: bytes(ram[addr:addr + n]),
            mem_write=lambda addr, data: ram.__setitem__(slice(addr, addr + len(data)), data),
            image_bytes=frozen_backing,
        )
        # ALFReD SDHCI_CLEAR_STAT halfword writes to 0x30 and 0x32 must not set ERR_INT_STATUS,
        # and PRESENT_STATE (0x24) bit 20 (DAT[0] idle) must be high so device is not busy.
        self.assertEqual(dev.read(0x24, 4) & 0x00100000, 0x00100000)
        self.assertEqual(dev._build_ext_csd()[179], 0x08)
        dev.write(0x30, 0x00FF, 2)
        dev.write(0x32, 0xF7FF, 2)
        self.assertEqual(dev.read(SDHCI_INT_STATUS, 4), 0)

        # ADMA2 CMD24 write updates in-memory COW overlay without mutating backing bytes.
        ram[0x1000:0x1004] = b'VFAT'
        struct.pack_into('<II', ram, 0x100, (512 << 16) | 0x23, 0x1000)
        dev.write(SDHCI_HOST_CONTROL, 0x10, 1)
        dev.write(SDHCI_ADMA_ADDRESS, 0x100, 4)
        dev.write(SDHCI_BLOCK_SIZE, 512, 2)
        dev.write(SDHCI_BLOCK_COUNT, 1, 2)
        dev.write(SDHCI_TRANSFER_MODE, 0x0001, 2)  # DMA write
        dev.write(SDHCI_ARGUMENT, 0, 4)
        dev.write(SDHCI_COMMAND, (24 << 8) | 0x22, 2)
        self.assertEqual(dev.sectors_written, 1)
        self.assertEqual(frozen_backing[8 * 1024 * 1024:8 * 1024 * 1024 + 4], b'USER')

        struct.pack_into('<II', ram, 0x100, (512 << 16) | 0x23, 0x2000)
        dev.write(SDHCI_TRANSFER_MODE, 0x0011, 2)
        dev.write(SDHCI_COMMAND, (17 << 8) | 0x22, 2)
        self.assertEqual(bytes(ram[0x2000:0x2004]), b'VFAT')

        # CMD6 R1b switch to boot0 (EXT_CSD[179] = 1) sets XFER_COMPLETE and reads boot0.
        dev.write(SDHCI_INT_STATUS, 0xFFFFFFFF, 4)
        dev.write(SDHCI_ARGUMENT, (3 << 24) | (179 << 16) | (1 << 8), 4)
        dev.write(SDHCI_COMMAND, (6 << 8) | 0x03, 2)
        self.assertEqual(dev.read(SDHCI_INT_STATUS, 4) & (INT_CMD_COMPLETE | INT_XFER_COMPLETE),
                         INT_CMD_COMPLETE | INT_XFER_COMPLETE)
        struct.pack_into('<II', ram, 0x100, (512 << 16) | 0x23, 0x2400)
        dev.write(SDHCI_ARGUMENT, 0, 4)
        dev.write(SDHCI_COMMAND, (17 << 8) | 0x22, 2)
        self.assertEqual(bytes(ram[0x2400:0x2404]), b'EXBL')


if __name__ == '__main__':
    unittest.main()
