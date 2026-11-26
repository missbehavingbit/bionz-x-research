/* Action mod ("USB Streaming Test") for Mod Loader v2 in sony-a6400 QEMU.
 * Triggers UsbControl configure/start and signals the guest UVC daemon without blocking the UI thread.
 */
#include "mlv2_sys.h"
#include "usb_symbols.h"

#ifndef USB_BASE_REF
#define USB_BASE_REF  0x04857000u
#define USB_FILE_FNV  0xa2ae986bu
#define USB_FILE_LEN  94176u
#define MWF_BASE_REF  0x040f7000u
#define MWF_FILE_FNV  0x2b7c22d1u
#define MWF_FILE_LEN  238108u
#define LIBC_BASE_REF 0x042c1000u
#define LIBC_FILE_FNV 0x7f95ef74u
#define LIBC_FILE_LEN 984292u
#endif

#ifndef UVC_MOD_ALLOW_REAL_CAMERA
static int contains(const char *s, const char *needle) {
    for (; *s; s++) {
        const char *a = s, *b = needle;
        while (*b && *a == *b) { a++; b++; }
        if (!*b) return 1;
    }
    return 0;
}
#endif

static void log_value(const char *phase, uint32_t value) {
    ml_log(phase, value);
    ml_log_to("/tmp_bt/usb-streaming-test.log", ML_O_WR_CREAT_APP, phase, value);
}

static int check_file(const char *path, uint32_t expected, uint32_t length) {
    int fd = ml_open(path, ML_O_RDONLY), n;
    char buf[512];
    uint32_t hash = 2166136261u, size = 0;
    if (fd < 0) { log_value("file_open_error", (uint32_t)fd); return 0; }
    while ((n = ml_read(fd, buf, sizeof(buf))) > 0) {
        size += (uint32_t)n;
        if (size > length) { ml_close(fd); log_value("file_oversize", size); return 0; }
        for (int i = 0; i < n; i++) hash = (hash ^ (uint8_t)buf[i]) * 16777619u;
    }
    ml_close(fd);
    if (n != 0 || size != length || hash != expected) {
        log_value("file_read_result", (uint32_t)n);
        log_value("file_size", size);
        log_value("file_fnv", hash);
        return 0;
    }
    return 1;
}

static int same_code(uint32_t address, const uint8_t *bytes) {
    for (unsigned i = 0; i < 16; i++) {
        if (((volatile uint8_t *)(address & ~1u))[i] != bytes[i]) return 0;
    }
    return 1;
}

static uint32_t word(uint32_t a) { return *(volatile uint32_t *)a; }

static uint32_t parse_hex_u32(const char *s) {
    uint32_t v = 0;
    while (*s) {
        char c = *s++;
        if (c >= '0' && c <= '9') v = (v << 4) | (uint32_t)(c - '0');
        else if (c >= 'a' && c <= 'f') v = (v << 4) | (uint32_t)(c - 'a' + 10);
        else if (c >= 'A' && c <= 'F') v = (v << 4) | (uint32_t)(c - 'A' + 10);
        else break;
    }
    return v;
}

static int ends_with(const char *s, const char *suffix) {
    int sl = 0, pl = 0;
    while (s[sl]) sl++;
    while (suffix[pl]) pl++;
    if (sl < pl) return 0;
    for (int i = 0; i < pl; i++) {
        if (s[sl - pl + i] != suffix[i]) return 0;
    }
    return 1;
}

static uint32_t find_lib_base(const char *suffix) {
    int fd = ml_open("/proc/self/maps", ML_O_RDONLY);
    if (fd < 0) return 0;
    char buf[256];
    int pos = 0;
    char ch;
    uint32_t found = 0;
    while (ml_read(fd, &ch, 1) == 1) {
        if (ch == '\n' || pos >= (int)sizeof(buf) - 1) {
            buf[pos] = 0;
            if (ends_with(buf, suffix)) {
                found = parse_hex_u32(buf);
                break;
            }
            pos = 0;
        } else {
            buf[pos++] = ch;
        }
    }
    ml_close(fd);
    return found;
}

static int send_command(uint32_t control, uint32_t id, uint32_t fn_send_event,
                        uint32_t fn_malloc, uint32_t fn_free,
                        uint32_t fn_objmsg_ctor, uint32_t fn_objmsg_dtor,
                        uint32_t fn_param_ctor, uint32_t fn_param_dtor,
                        uint32_t fn_add_param) {
    uint32_t msg = ((uint32_t (*)(uint32_t))fn_malloc)(8);
    uint32_t param = ((uint32_t (*)(uint32_t))fn_malloc)(12);
    if (!msg || !param) {
        if (msg) ((void (*)(uint32_t))fn_free)(msg);
        if (param) ((void (*)(uint32_t))fn_free)(param);
        return -1;
    }
    ((void (*)(uint32_t, uint32_t, uint32_t))fn_objmsg_ctor)(msg, 0x6103, id);
    ((void (*)(uint32_t, int))fn_param_ctor)(param, 4);
    ((void (*)(uint32_t, uint32_t, uint32_t))fn_add_param)(msg, 0x1005, param);
    ((void (*)(uint32_t))fn_param_dtor)(param);
    ((void (*)(uint32_t))fn_free)(param);
    int result = ((int (*)(uint32_t, uint32_t))fn_send_event)(control + 0x28, msg);
    ((void (*)(uint32_t))fn_objmsg_dtor)(msg);
    ((void (*)(uint32_t))fn_free)(msg);
    return result;
}

static void write_text_file(const char *path, const char *text, uint32_t len) {
    int fd = ml_open(path, ML_O_WR_CREAT_TRUNC);
    if (fd >= 0) {
        ml_write(fd, text, len);
        ml_close(fd);
    }
}

__attribute__((used)) void uvc_mod_on(int on) {
    if (!on) {
        write_text_file("/tmp_bt/stop_feeder", "1\n", 2);
        write_text_file("/tmp_bt/uvc_mod.cmd", "stop\n", 5);
        log_value("uvc_mod_off", 0);
        return;
    }

    ml_unlink("/tmp_bt/stop_feeder");
    log_value("uvc_mod_on", 1);

#ifndef UVC_MOD_ALLOW_REAL_CAMERA
    char cmdline[2048];
    int fd = ml_open("/proc/cmdline", ML_O_RDONLY);
    if (fd < 0) { log_value("cmdline_open_err", (uint32_t)fd); return; }
    int n = ml_read(fd, cmdline, sizeof(cmdline) - 1);
    ml_close(fd);
    if (n < 0) return;
    cmdline[n] = 0;
    if (!contains(cmdline, "console=ttyAM0,")) {
        log_value("cmdline_not_qemu", 0);
        return;
    }
#endif

    if (!check_file("/usr/lib/libUsbControl.so", USB_FILE_FNV, USB_FILE_LEN) ||
        !check_file("/usr/lib/libMWF.so", MWF_FILE_FNV, MWF_FILE_LEN) ||
        !check_file("/lib/libc-2.11.2.so", LIBC_FILE_FNV, LIBC_FILE_LEN)) {
        log_value("identity_file_refused", 0);
        return;
    }

    uint32_t base_usb = find_lib_base("/usr/lib/libUsbControl.so");
    uint32_t base_mwf = find_lib_base("/usr/lib/libMWF.so");
    uint32_t base_libc = find_lib_base("/lib/libc-2.11.2.so");
    log_value("base_usb", base_usb);
    log_value("base_mwf", base_mwf);
    log_value("base_libc", base_libc);
    if (!base_usb || !base_mwf || !base_libc) {
        log_value("base_missing", 0);
        return;
    }

    uint32_t fn_ctrl_inst   = base_usb  + (F_CONTROL_INSTANCE - USB_BASE_REF);
    uint32_t fn_send_event  = base_usb  + (F_SEND_EVENT       - USB_BASE_REF);
    uint32_t fn_objmsg_ctor = base_mwf  + (F_OBJMSG_CTOR      - MWF_BASE_REF);
    uint32_t fn_objmsg_dtor = base_mwf  + (F_OBJMSG_DTOR      - MWF_BASE_REF);
    uint32_t fn_param_ctor  = base_mwf  + (F_PARAM_CTOR       - MWF_BASE_REF);
    uint32_t fn_param_dtor  = base_mwf  + (F_PARAM_DTOR       - MWF_BASE_REF);
    uint32_t fn_add_param   = base_mwf  + (F_ADD_PARAM        - MWF_BASE_REF);
    uint32_t fn_malloc      = base_libc + (F_MALLOC           - LIBC_BASE_REF);
    uint32_t fn_free        = base_libc + (F_FREE             - LIBC_BASE_REF);

    if (!same_code(fn_ctrl_inst,   usb_expected_CONTROL_INSTANCE) ||
        !same_code(fn_send_event,  usb_expected_SEND_EVENT) ||
        !same_code(fn_objmsg_ctor, usb_expected_OBJMSG_CTOR) ||
        !same_code(fn_objmsg_dtor, usb_expected_OBJMSG_DTOR) ||
        !same_code(fn_param_ctor,  usb_expected_PARAM_CTOR) ||
        !same_code(fn_param_dtor,  usb_expected_PARAM_DTOR) ||
        !same_code(fn_add_param,   usb_expected_ADD_PARAM) ||
        !same_code(fn_malloc,      usb_expected_MALLOC) ||
        !same_code(fn_free,        usb_expected_FREE)) {
        log_value("identity_code_refused", fn_ctrl_inst);
        return;
    }

    uint32_t control = ((uint32_t (*)(void))fn_ctrl_inst)();
    log_value("control", control);
    if (!control) return;

    uint32_t iface_slot = base_usb + (USB_INTERFACE_SLOT - USB_BASE_REF);
    uint32_t state = word(control + 0x104);
    log_value("initial_state", state);
    if (state == 7) {
        int sres = send_command(control, 0x83002, fn_send_event, fn_malloc, fn_free,
                                fn_objmsg_ctor, fn_objmsg_dtor, fn_param_ctor, fn_param_dtor, fn_add_param);
        log_value("stop_result", (uint32_t)sres);
        for (unsigned i = 0; i < 100 && word(control + 0x104) != 1; i++) {
            uint32_t delay[2] = {0, 10000000};
            ml_sc3(162, (int)delay, 0, 0);
        }
        state = word(control + 0x104);
        log_value("after_stop_state", state);
    }
    if (state == 1) {
        int res = send_command(control, 0x83003, fn_send_event, fn_malloc, fn_free,
                               fn_objmsg_ctor, fn_objmsg_dtor, fn_param_ctor, fn_param_dtor, fn_add_param);
        log_value("configure_result", (uint32_t)res);
        if (res != 0) return;
        for (unsigned i = 0; i < 50 && word(control + 0x104) != 5; i++) {
            uint32_t delay[2] = {0, 10000000};
            ml_sc3(162, (int)delay, 0, 0);
        }
        state = word(control + 0x104);
        log_value("configured_state", state);
    }
    if (state == 5) {
        int res = send_command(control, 0x83001, fn_send_event, fn_malloc, fn_free,
                               fn_objmsg_ctor, fn_objmsg_dtor, fn_param_ctor, fn_param_dtor, fn_add_param);
        log_value("start_result", (uint32_t)res);
        for (unsigned i = 0; i < 100 && word(control + 0x104) != 7; i++) {
            uint32_t delay[2] = {0, 10000000};
            ml_sc3(162, (int)delay, 0, 0);
        }
        state = word(control + 0x104);
    }
    uint8_t created = 0;
    for (unsigned i = 0; i < 100 && !created; i++) {
        uint32_t iface = word(iface_slot);
        uint32_t svc = iface ? word(iface + 0x3c) : 0;
        uint32_t reg = svc ? word(svc + 0xc0) : 0;
        created = reg ? *(volatile uint8_t *)(reg + 8) : 0;
        if (!created) {
            uint32_t delay[2] = {0, 10000000};
            ml_sc3(162, (int)delay, 0, 0);
        }
    }
    log_value("final_state", state);
    uint32_t iface = word(iface_slot);
    log_value("interface", iface);
    if (iface) {
        uint32_t svc = word(iface + 0x3c);
        log_value("service", svc);
        if (svc) {
            log_value("service_state", word(svc + 0x98));
            uint32_t reg = word(svc + 0xc0);
            log_value("registry", reg);
            if (reg) {
                log_value("gadget_created", *(volatile uint8_t *)(reg + 8));
            }
        }
    }
    if (state == 7) {
        write_text_file("/tmp_bt/uvc_mod.cmd", "start\n", 6);
    }
}
