/* VM-only PID 1. Never install in the ARM64 guest. */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/reboot.h>
#include <sys/xattr.h>
#include <unistd.h>
int skyward_guest_volumes(int [2]);
int skyward_claim_format(int, int);
#define CHECK(x) do { if (!(x)) { dprintf(2,"AUTH FAIL line %d\n",__LINE__); return 1; } } while (0)
int main(void) {
    int held[2]; char mark[32];
    CHECK(!skyward_guest_volumes(held));
    int fd = open("/volumes/userdata.raw", O_RDWR|O_NOFOLLOW|O_CLOEXEC);
    CHECK(fd >= 0);
    ssize_t n = fgetxattr(fd,"trusted.skyward.mkfs",mark,sizeof(mark));
    if (n == 8 && !memcmp(mark,"spent-v1",8)) {
        CHECK(skyward_claim_format(fd,held[1]) == 0);
        dprintf(2,"AUTH PASS reboot after claim before mkfs: denied\n");
    } else {
        CHECK(n == 6 && !memcmp(mark,"new-v1",6));
        CHECK(skyward_claim_format(fd,held[0]) == -1);
        dprintf(2,"AUTH PASS mismatched loop rejected\n");
        off64_t last = (4LL << 30)-1;
        CHECK(pwrite64(held[1],"X",1,last)==1 && !fsync(held[1]));
        CHECK(skyward_claim_format(fd,held[1]) == -1);
        char c; CHECK(pread64(held[1],&c,1,last)==1 && c=='X');
        dprintf(2,"AUTH PASS nonzero final byte rejected and retained\n");
        CHECK(pwrite64(held[1],"",1,last)==1 && !fsync(held[1]));
        CHECK(skyward_claim_format(fd,held[1])==1);
        CHECK(skyward_claim_format(fd,held[1])==0);
        dprintf(2,"AUTH PASS single grant, replay denied\n");
    }
    // Skip sync() and clean unmount: the claim itself must have been persisted.
    reboot(RB_POWER_OFF);
    return 1;
}
