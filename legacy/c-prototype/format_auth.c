/* Called by single-threaded lab PID 1, before other guest processes exist. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <linux/loop.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/xattr.h>
#include <unistd.h>

#define AUTH "trusted.skyward.mkfs"
#define FRESH "new-v1"
#define USED "spent-v1"
#define BYTES (INT64_C(4) << 30)

/* 1: durable grant, 0: ordinary boot, -1: stop; never formats anything. */
int skyward_claim_format(int image, int loop) {
    struct stat st, node, root;
    struct loop_info64 li;
    uint64_t bytes = 0;
    char mark[32];
    if (getpid() != 1 || stat("/", &root) || fstat(image, &st) ||
        !S_ISREG(st.st_mode) || st.st_dev != root.st_dev || st.st_uid ||
        st.st_nlink != 1 || (st.st_mode & 022) || st.st_size != BYTES ||
        fstat(loop, &node) || !S_ISBLK(node.st_mode) || major(node.st_rdev) != 7 ||
        ioctl(loop, LOOP_GET_STATUS64, &li) || li.lo_device != (uint64_t)st.st_dev ||
        li.lo_inode != st.st_ino || li.lo_number != minor(node.st_rdev) ||
        li.lo_offset || li.lo_sizelimit || li.lo_encrypt_type ||
        (li.lo_flags & (LO_FLAGS_READ_ONLY | LO_FLAGS_AUTOCLEAR | LO_FLAGS_PARTSCAN)) ||
        ioctl(loop, BLKGETSIZE64, &bytes) || bytes != BYTES) return -1;
    ssize_t n = fgetxattr(image, AUTH, mark, sizeof(mark));
    if ((n < 0 && errno == ENODATA) ||
        (n == sizeof(USED)-1 && !memcmp(mark, USED, sizeof(USED)-1))) return 0;
    if (n != sizeof(FRESH)-1 || memcmp(mark, FRESH, sizeof(FRESH)-1)) return -1;
    static unsigned char data[256 * 1024];
    static const unsigned char zeros[sizeof(data)];
    for (off64_t off = 0; off < BYTES; off += sizeof(data)) {
        if (pread64(loop, data, sizeof(data), off) != sizeof(data) ||
            memcmp(data, zeros, sizeof(data))) return -1;
    }
    if (fsetxattr(image, AUTH, USED, sizeof(USED)-1, XATTR_REPLACE) || fsync(image)) return -1;
    return 1;
}
