/* Only installed in the isolated S4 rootfs. The real guest-entry stays PID 1. */
#include <time.h>
#include <unistd.h>

int main(void) {
    static const char marker[] = "QEMU-only resize2fs stall injected\n";
    (void)write(STDERR_FILENO, marker, sizeof(marker) - 1);
    for (;;) {
        const struct timespec interval = {.tv_sec = 3600};
        nanosleep(&interval, NULL);
    }
}
