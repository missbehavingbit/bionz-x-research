/* Offline QEMU stimulus, not a UVC implementation or physical-camera mod. */
#include "mlv2_sys.h"
#include "usb_symbols.h"

static int contains(const char *s, const char *needle) {
    for (; *s; s++) { const char *a=s, *b=needle; while (*b && *a==*b) {a++;b++;} if (!*b) return 1; }
    return 0;
}
static void log_value(const char *phase, uint32_t value) {
    char out[100]; unsigned n=0;
    while (*phase && n<75) out[n++]=*phase++;
    out[n++]=' ';
    for (unsigned i=0;i<8;i++) out[n++]="0123456789abcdef"[(value >> (28-4*i)) & 15];
    out[n++]='\n';
    int fd=ml_open("/tmp_bt/usb-service-probe.log", ML_O_WR_CREAT_APP);
    if (fd>=0) { ml_write(fd,out,n); ml_close(fd); }
}
static int check_file(const char *path, uint32_t expected, uint32_t length) {
    int fd=ml_open(path,0), n; char buf[512]; uint32_t hash=2166136261u, size=0;
    if(fd<0) {log_value("file_open_error",(uint32_t)fd);return 0;}
    while((n=ml_read(fd,buf,sizeof(buf)))>0) {
        size+=(uint32_t)n;
        if(size>length) {ml_close(fd);log_value("file_oversize",size);return 0;}
        for(int i=0;i<n;i++) hash=(hash^(uint8_t)buf[i])*16777619u;
    }
    ml_close(fd);
    if(n!=0 || size!=length || hash!=expected) {
        log_value("file_read_result",(uint32_t)n);
        log_value("file_size",size);log_value("file_fnv",hash);
        return 0;
    }
    return 1;
}
static int same_code(uint32_t address, const uint8_t *bytes) {
    for(unsigned i=0;i<16;i++) if(((volatile uint8_t *)(address & ~1u))[i]!=bytes[i]) return 0;
    return 1;
}
static uint32_t word(uint32_t a) {return *(volatile uint32_t *)a;}

static int send_command(uint32_t control, uint32_t id) {
    uint32_t msg=((uint32_t (*)(uint32_t))F_MALLOC)(8);
    uint32_t param=((uint32_t (*)(uint32_t))F_MALLOC)(12);
    if (!msg || !param) {
        if(msg) ((void (*)(uint32_t))F_FREE)(msg);
        if(param) ((void (*)(uint32_t))F_FREE)(param);
        return -1;
    }
    ((void (*)(uint32_t,uint32_t,uint32_t))F_OBJMSG_CTOR)(msg,0x6103,id);
    ((void (*)(uint32_t,int))F_PARAM_CTOR)(param,4);
    ((void (*)(uint32_t,uint32_t,uint32_t))F_ADD_PARAM)(msg,0x1005,param);
    ((void (*)(uint32_t))F_PARAM_DTOR)(param);
    ((void (*)(uint32_t))F_FREE)(param);
    int result=((int (*)(uint32_t,uint32_t))F_SEND_EVENT)(control+0x28,msg);
    ((void (*)(uint32_t))F_OBJMSG_DTOR)(msg);
    ((void (*)(uint32_t))F_FREE)(msg);
    return result;
}

__attribute__((used)) void usb_probe_on(int on) {
    char command[2048]; int fd, n;
    if (!on) return;
    fd=ml_open("/tmp_bt/qemu-usb-service.allow",0);
    if(fd<0) return;
    ml_close(fd);
    fd=ml_open("/proc/cmdline",0);
    if(fd<0) return;
    n=ml_read(fd,command,sizeof(command)-1);ml_close(fd);
    if(n<0) return;
    command[n]=0;
    if(!contains(command,"console=ttyAM0,")) return;
    if(!verify_inputs()) {log_value("identity_refused",0);return;}
    uint32_t control=((uint32_t (*)(void))F_CONTROL_INSTANCE)();
    log_value("control",control);
    if (!control) return;
    log_value("initial_state",word(control+0x104));
    if(word(control+0x104)!=1) return;
    int result=send_command(control,0x83003);
    log_value("configure_result",(uint32_t)result);
    if(result) return;
    for(unsigned i=0;i<100 && word(control+0x104)!=5;i++) {
        uint32_t delay[2]={0,10000000}; ml_sc3(162,(int)delay,0,0);
    }
    log_value("configured_state",word(control+0x104));
    if(word(control+0x104)!=5) return;
    log_value("start_result",(uint32_t)send_command(control,0x83001));
    log_value("state_after_send",word(control+0x104));
    for(unsigned i=0;i<100 && word(control+0x104)!=7;i++) {
        uint32_t delay[2]={0,10000000}; ml_sc3(162,(int)delay,0,0);
    }
    log_value("final_state",word(control+0x104));
    log_value("configured_mask",word(control+0x94));
    log_value("started_mask",word(control+0x98));
    uint32_t interface=word(USB_INTERFACE_SLOT);
    log_value("interface",interface);
    if(!interface) return;
    uint32_t service=word(interface+0x3c);
    log_value("service",service);
    if(!service) return;
    log_value("service_state",word(service+0x98));
    uint32_t registry=word(service+0xc0);
    log_value("registry",registry);
    if(registry) {
        log_value("gadget_created",*(volatile uint8_t *)(registry+8));
        log_value("gadget_fid",word(registry+0x34));
    }
}
