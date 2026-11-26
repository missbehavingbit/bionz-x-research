/* Offline QEMU diagnostic only. No gadget creation or success substitution. */
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    unsigned char mode = 2;
    int fd, rc, saved;
    if (argc != 2 || strcmp(argv[1], "--offline-qemu-only")) {
        fputs("Requires --offline-qemu-only; never run on a camera.\n", stderr);
        return 2;
    }
    fd = open("/dev/usb/otgcore", O_RDWR);
    if (fd < 0) {
        printf("OTG_MODE open=-1 errno=%d\n", errno);
        glob_t paths;
        if (glob("/proc/[0-9]*/fd/*", 0, NULL, &paths) == 0) {
            size_t i;
            for (i = 0; i < paths.gl_pathc; i++) {
                char target[256];
                ssize_t n = readlink(paths.gl_pathv[i], target, sizeof(target) - 1);
                if (n < 0) {
                    continue;
                }
                target[n] = 0;
                if (!strcmp(target, "/dev/usb/otgcore")) {
                    printf("OTG_OWNER=%s\n", paths.gl_pathv[i]);
                }
            }
        }
        globfree(&paths);
        return 1;
    }
    errno = 0;
    rc = ioctl(fd, 0x4001e30cUL, &mode);
    saved = errno;
    printf("OTG_MODE mode=2 result=%d errno=%d\n", rc, saved);
    close(fd);
    return rc < 0 ? 1 : 0;
}
