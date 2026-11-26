/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 AudioIp I/O Controller (0xF3420000 .. 0xF3428FFF, 36 KiB).
 *
 * Hardware contract derived from av-cam.bin ADF/AIP audio subsystem
 * (0x639e8d00..0x639ea500, E0528, and a5000 RE_AUDIO.md):
 *   - 0xF3420000..0xF3420FFF: AudioIp master clock, PLL & reset control
 *   - 0xF3421000..0xF3421FFF: I2S / serial audio interface routing
 *   - 0xF3422000..0xF3422FFF: Internal stereo ADC / DAC / PGA gain & mute
 *   - 0xF3423000..0xF3423FFF: Capture & playback ring-buffer DMA engine:
 *       +0x3000: Capture DMA control (bit 0 = enable/run)
 *       +0x3004: Capture ring base PA
 *       +0x3008: Capture ring byte size (or end offset)
 *       +0x300C: Capture ring current write offset (polled on VSYNC/tick)
 *       +0x3100: Playback DMA control (bit 0 = enable/run)
 *       +0x3104: Playback ring base PA
 *       +0x3108: Playback ring byte size
 *       +0x310C: Playback ring current read offset
 *       +0x3C00..+0x3C28: Peak/VU level meters & channel DMA status
 *   - 0xF3428000..0xF3428FFF: CXD90045 extended codec / jack-detect block
 *     (previously uncovered by the 0x8000 regfile; 0x639e93f6 accesses +0x8000..+0x8038)
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/core/sysbus.h"
#include "hw/misc/cxd90045_peripherals.h"
#include "system/address-spaces.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "qom/object.h"

#define TYPE_CXD90045_AUDIO "cxd90045-audio"
OBJECT_DECLARE_SIMPLE_TYPE(Cxd90045AudioState, CXD90045_AUDIO)

#define AUDIOIP_BASE        0xF3420000ULL
#define AUDIOIP_SIZE        0x00009000ULL /* Covers 0xF3420000..0xF3428FFF */
#define AUDIOIP_WORDS       (AUDIOIP_SIZE >> 2)

/* 48 kHz stereo 16-bit PCM = 192 bytes per ms -> 1920 bytes per 10 ms tick */
#define AUDIO_TICK_NS       (10 * 1000 * 1000LL)
#define AUDIO_SAMPLES_TICK  480u
#define AUDIO_BYTES_TICK    (AUDIO_SAMPLES_TICK * 4u)
#define AUDIO_MAX_RING      (16u * 1024u * 1024u)

enum {
    AUDIO_IN_NONE = 0,
    AUDIO_IN_SILENCE,
    AUDIO_IN_SINE440,
};

struct Cxd90045AudioState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    QEMUTimer *dma_timer;

    uint32_t regs[AUDIOIP_WORDS];
    int input_mode;
    bool mic_plugged;
    uint32_t sine_phase;

    /* Telemetry */
    uint64_t capture_frames;
    uint64_t playback_frames;
    int32_t last_peak_l;
    int32_t last_peak_r;
};

/* 32-entry quarter/full sine table (amplitude ~8192 = -12 dBFS in int16) */
static const int16_t sine32[32] = {
       0,  1606,  3134,  4551,  5793,  6811,  7568,  8035,
    8192,  8035,  7568,  6811,  5793,  4551,  3134,  1606,
       0, -1606, -3134, -4551, -5793, -6811, -7568, -8035,
   -8192, -8035, -7568, -6811, -5793, -4551, -3134, -1606,
};

static bool audio_pa_in_dram(uint32_t pa, uint32_t len)
{
    if (len == 0 || len > AUDIO_MAX_RING) {
        return false;
    }
    /* Avoid reserved first MiB; allow RAM0 (1 MiB..1 GiB) and RAM1 (2 GiB+) */
    if (pa >= 0x00100000u && (uint64_t)pa + len <= 0x40000000ULL) {
        return true;
    }
    if (pa >= 0x80000000u && (uint64_t)pa + len <= 0xBF800000ULL) {
        return true;
    }
    return false;
}

static void audio_parse_mode(Cxd90045AudioState *s, const char *mode)
{
    if (!mode || !*mode || strcmp(mode, "sine440") == 0) {
        s->input_mode = AUDIO_IN_SINE440;
    } else if (strcmp(mode, "silence") == 0) {
        s->input_mode = AUDIO_IN_SILENCE;
    } else {
        s->input_mode = AUDIO_IN_NONE;
    }
}

static void audio_dma_tick(void *opaque)
{
    Cxd90045AudioState *s = opaque;
    bool active = false;

    /* Capture DMA channel (+0x3000..+0x300C) */
    uint32_t cap_ctl  = s->regs[0x3000 >> 2];
    uint32_t cap_base = s->regs[0x3004 >> 2];
    uint32_t cap_size = s->regs[0x3008 >> 2];
    uint32_t cap_pos  = s->regs[0x300C >> 2];

    if ((cap_ctl & 1u) && cap_size >= 64 && (cap_size & 3u) == 0) {
        active = true;
        uint32_t step = MIN(AUDIO_BYTES_TICK, cap_size);
        if (s->input_mode != AUDIO_IN_NONE &&
            audio_pa_in_dram(cap_base, cap_size)) {
            uint8_t pcm[AUDIO_BYTES_TICK];
            int32_t peak = 0;

            for (unsigned i = 0; i < step / 4; i++) {
                int16_t l = 0, r = 0;
                if (s->input_mode == AUDIO_IN_SINE440) {
                    /* 440 Hz @ 48 kHz: phase step = 440 * 32 / 48000 */
                    uint32_t idx = (s->sine_phase >> 10) & 31u;
                    l = sine32[idx];
                    r = sine32[(idx + 8) & 31u]; /* 90-deg stereo phase */
                    s->sine_phase += (440u * 32u * 1024u) / 48000u;
                }
                stw_le_p(&pcm[4 * i], (uint16_t)l);
                stw_le_p(&pcm[4 * i + 2], (uint16_t)r);
                if (abs(l) > peak) {
                    peak = abs(l);
                }
            }

            uint32_t cur = cap_pos % cap_size;
            uint32_t first = MIN(step, cap_size - cur);
            address_space_write(&address_space_memory, cap_base + cur,
                                MEMTXATTRS_UNSPECIFIED, pcm, first);
            if (first < step) {
                address_space_write(&address_space_memory, cap_base,
                                    MEMTXATTRS_UNSPECIFIED, pcm + first,
                                    step - first);
            }
            s->last_peak_l = peak;
            s->last_peak_r = peak;
            /* Expose VU peak level in +0x3C00 / +0x3C04 */
            s->regs[0x3C00 >> 2] = (uint32_t)peak;
            s->regs[0x3C04 >> 2] = (uint32_t)peak;
        }
        s->regs[0x300C >> 2] = (cap_pos + step) % cap_size;
        s->capture_frames += step / 4;
    }

    /* Playback DMA channel (+0x3100..+0x310C) */
    uint32_t pb_ctl  = s->regs[0x3100 >> 2];
    uint32_t pb_base = s->regs[0x3104 >> 2];
    uint32_t pb_size = s->regs[0x3108 >> 2];
    uint32_t pb_pos  = s->regs[0x310C >> 2];

    if ((pb_ctl & 1u) && pb_size >= 64 && (pb_size & 3u) == 0) {
        active = true;
        uint32_t step = MIN(AUDIO_BYTES_TICK, pb_size);
        if (audio_pa_in_dram(pb_base, pb_size)) {
            uint8_t out_pcm[AUDIO_BYTES_TICK];
            uint32_t cur = pb_pos % pb_size;
            uint32_t n = MIN(step, pb_size - cur);
            address_space_read(&address_space_memory, pb_base + cur,
                               MEMTXATTRS_UNSPECIFIED, out_pcm, n);
            int32_t p_l = 0, p_r = 0;
            for (unsigned i = 0; i + 4 <= n; i += 4) {
                int16_t sl = (int16_t)lduw_le_p(&out_pcm[i]);
                int16_t sr = (int16_t)lduw_le_p(&out_pcm[i + 2]);
                if (abs(sl) > p_l) {
                    p_l = abs(sl);
                }
                if (abs(sr) > p_r) {
                    p_r = abs(sr);
                }
            }
            if (p_l || p_r) {
                s->last_peak_l = p_l;
                s->last_peak_r = p_r;
            }
        }
        s->regs[0x310C >> 2] = (pb_pos + step) % pb_size;
        s->playback_frames += step / 4;
    }

    if (active) {
        timer_mod(s->dma_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUDIO_TICK_NS);
    }
}

static uint64_t audioip_read(void *opaque, hwaddr off, unsigned size)
{
    Cxd90045AudioState *s = opaque;
    uint32_t idx = (uint32_t)(off >> 2);
    uint32_t v;

    if (idx >= AUDIOIP_WORDS) {
        return 0;
    }
    v = s->regs[idx];

    switch (off & ~3ULL) {
    case 0x0004: /* AudioIp PLL / clock lock status */
        v |= 0x00000001u;
        break;
    case 0x8004: /* Extended codec ready + 3.5mm external mic jack sense */
        v = (v & ~0x03u) | 0x01u | (s->mic_plugged ? 0x02u : 0x00u);
        break;
    default:
        break;
    }

    if (size < 4) {
        unsigned sh = (off & 3) * 8;
        v = (v >> sh) & ((1u << (size * 8)) - 1u);
    }
    return v;
}

static void audioip_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    Cxd90045AudioState *s = opaque;
    uint32_t idx = (uint32_t)(off >> 2);

    if (idx >= AUDIOIP_WORDS) {
        return;
    }

    if (size == 4) {
        s->regs[idx] = (uint32_t)val;
    } else {
        unsigned sh = (off & 3) * 8;
        uint32_t mask = ((1u << (size * 8)) - 1u) << sh;
        s->regs[idx] = (s->regs[idx] & ~mask) | (((uint32_t)val << sh) & mask);
    }

    if ((off & ~3ULL) == 0x3000 || (off & ~3ULL) == 0x3100) {
        if ((s->regs[0x3000 >> 2] & 1u) || (s->regs[0x3100 >> 2] & 1u)) {
            timer_mod(s->dma_timer,
                      qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + AUDIO_TICK_NS);
        }
    }
}

static const MemoryRegionOps audioip_ops = {
    .read = audioip_read,
    .write = audioip_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void audioip_reset(DeviceState *dev)
{
    Cxd90045AudioState *s = CXD90045_AUDIO(dev);

    timer_del(s->dma_timer);
    memset(s->regs, 0, sizeof(s->regs));
    s->sine_phase = 0;
}

static void audioip_realize(DeviceState *dev, Error **errp)
{
    Cxd90045AudioState *s = CXD90045_AUDIO(dev);

    s->dma_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, audio_dma_tick, s);
    memory_region_init_io(&s->iomem, OBJECT(dev), &audioip_ops, s,
                          "cxd90045.audio-io", AUDIOIP_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->iomem);
}

static bool audio_get_mic_plugged(Object *obj, Error **errp)
{
    return CXD90045_AUDIO(obj)->mic_plugged;
}

static void audio_set_mic_plugged(Object *obj, bool val, Error **errp)
{
    CXD90045_AUDIO(obj)->mic_plugged = val;
}

static void audio_get_u64(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    Cxd90045AudioState *s = CXD90045_AUDIO(obj);
    uint64_t val = 0;

    if (strcmp(name, "capture-frames") == 0) {
        val = s->capture_frames;
    } else if (strcmp(name, "playback-frames") == 0) {
        val = s->playback_frames;
    } else if (strcmp(name, "peak-left") == 0) {
        val = (uint64_t)s->last_peak_l;
    } else if (strcmp(name, "peak-right") == 0) {
        val = (uint64_t)s->last_peak_r;
    }
    visit_type_uint64(v, name, &val, errp);
}

static void audioip_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = audioip_realize;
    device_class_set_legacy_reset(dc, audioip_reset);
    object_class_property_add_bool(klass, "mic-plugged",
                                   audio_get_mic_plugged,
                                   audio_set_mic_plugged);
    object_class_property_add(klass, "capture-frames", "uint64",
                              audio_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "playback-frames", "uint64",
                              audio_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "peak-left", "uint64",
                              audio_get_u64, NULL, NULL, NULL);
    object_class_property_add(klass, "peak-right", "uint64",
                              audio_get_u64, NULL, NULL, NULL);
}

static const TypeInfo audioip_info = {
    .name          = TYPE_CXD90045_AUDIO,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Cxd90045AudioState),
    .class_init    = audioip_class_init,
};

static void audioip_register_types(void)
{
    type_register_static(&audioip_info);
}
type_init(audioip_register_types)

DeviceState *cxd90045_audio_create(MachineState *machine, MemoryRegion *sysmem,
                                   const char *input_mode, bool mic_plugged)
{
    DeviceState *dev = qdev_new(TYPE_CXD90045_AUDIO);
    Cxd90045AudioState *s = CXD90045_AUDIO(dev);

    audio_parse_mode(s, input_mode);
    s->mic_plugged = mic_plugged;
    object_property_add_child(OBJECT(machine), "cxd90045-audio", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    /* Map at priority -400, above the baseline -500 regfile if present */
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 0, AUDIOIP_BASE, -400);
    audioip_reset(dev);
    return dev;
}
