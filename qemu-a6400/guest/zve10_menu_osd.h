/* Adapted renderer -> upstream VPH ch53. This is a guest policy, not a
 * measurement of the stock Sony display initialization sequence.
 * Called only after the renderer retires a coherent completed frame.
 */
#ifndef ZVE10_MENU_OSD_H
#define ZVE10_MENU_OSD_H
#include <stdint.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

static void pmca_menu_present(uint32_t physical)
{
    static volatile uint32_t *vph;
    if (physical < 0x20000000u || physical > 0x28000000u - 640u * 480u * 2u)
        _exit(72);
    if (!vph) {
        int fd = open("/dev/mem", O_RDWR | O_SYNC);
        if (fd < 0) _exit(72);
        void *mapped = mmap(NULL, 8192, PROT_READ | PROT_WRITE,
                            MAP_SHARED, fd, 0xf3440000u);
        close(fd);
        if (mapped == MAP_FAILED) _exit(72);
        vph = mapped;
    }
    __sync_synchronize();
    vph[0x028 / 4] = 0x111;
    vph[0x144 / 4] = 2;
    vph[0x404 / 4] = 0;          /* RGBA4444 */
    vph[0x1320 / 4] = physical;
    vph[0x1324 / 4] = 1280;      /* bytes per row */
    vph[0x1328 / 4] = 0;
    vph[0x132c / 4] = 479;       /* lines minus one */
    vph[0x1348 / 4] = 1280;      /* signed row pitch */
    vph[0x1300 / 4] = 3;
    __sync_synchronize();
}
#endif
