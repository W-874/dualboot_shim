/* Android early-init component. Keep both returned fds open until mounted.
 * No formatting, physical block lookups, fallback, or credential operations. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/loop.h>
#include <linux/magic.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static int fail(void) { errno = EINVAL; return -1; }

static int backing(int dir, const char *name, dev_t root, off_t size, int access) {
    int fd = openat(dir, name, O_PATH | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_dev != root ||
        st.st_nlink != 1 || st.st_size <= 0 || (st.st_size % 512) ||
        (size && st.st_size != size) || st.st_uid || (st.st_mode & 022)) {
        close(fd); return fail();
    }
    char path[48];
    snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
    int rw = open(path, access | O_CLOEXEC);
    close(fd); return rw;
}

static int attach(int dir, int ctl, int image, const char *alias, int readonly) {
    int n = ioctl(ctl, LOOP_CTL_GET_FREE);
    if (n < 0) return -1;
    /* Fresh private directory; never open or replace an existing alias. */
    if (mknodat(dir, alias, S_IFBLK | 0600, makedev(7, n))) return -1;
    int fd = openat(dir, alias, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    struct loop_config cfg = {0};
    cfg.fd = image;
    /* Mounted ROM partitions hold their own references. Writable volumes must
     * survive *all* closes, including before vold opens userdata. They live
     * for this boot only. Roll back uncommitted attachments explicitly. */
    cfg.info.lo_flags = readonly ? LO_FLAGS_READ_ONLY | LO_FLAGS_AUTOCLEAR : 0;
    if (fd < 0 || ioctl(fd, LOOP_CONFIGURE, &cfg)) {
        int err = errno;
        if (fd >= 0) close(fd);
        unlinkat(dir, alias, 0); errno = err; return -1;
    }
    return fd;
}

/* Returns zero on success. Called before starting any other guest processes. */
int skyward_guest_volumes(int held[2]) {
    struct stat root, node, parent;
    struct statfs fs;
    char path[128], name[32];
    int vol = -1, images[2] = {-1, -1}, ctl = -1, dir = -1, rc = -1;
    held[0] = held[1] = -1;
    if (getpid() != 1 || stat("/", &root) || lstat("/dev/skyward-root", &node) ||
        !S_ISBLK(node.st_mode) || node.st_rdev != root.st_dev ||
        statfs("/", &fs) || fs.f_type != EXT4_SUPER_MAGIC ||
        statfs("/dev", &fs) || fs.f_type != TMPFS_MAGIC) return fail();
    snprintf(path, sizeof(path), "/sys/dev/block/%u:%u/dm/name", major(root.st_dev), minor(root.st_dev));
    int id = open(path, O_RDONLY | O_CLOEXEC);
    if (id < 0) return -1;
    ssize_t len = read(id, name, sizeof(name)); close(id);
    if (len != 13 || memcmp(name, "skyward-root\n", 13)) return fail();
    vol = open("/volumes", O_RDONLY | O_NOFOLLOW | O_DIRECTORY | O_CLOEXEC);
    if (vol < 0 || fstat(vol, &parent) || parent.st_dev != root.st_dev ||
        parent.st_uid || (parent.st_mode & 022)) goto out;
    images[0] = backing(vol, "metadata.ext4", root.st_dev, 64LL << 20, O_RDWR);
    images[1] = backing(vol, "userdata.raw", root.st_dev, 4LL << 30, O_RDWR);
    if (images[0] < 0 || images[1] < 0) goto out;
    if (mknod("/dev/loop-control", S_IFCHR | 0600, makedev(10, 237)) && errno != EEXIST) goto out;
    if (lstat("/dev/loop-control", &node) || !S_ISCHR(node.st_mode) || node.st_rdev != makedev(10, 237)) goto out;
    ctl = open("/dev/loop-control", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (ctl < 0 || mkdir("/dev/block/skyward", 0700)) goto out;
    dir = open("/dev/block/skyward", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) goto out;
    held[0] = attach(dir, ctl, images[0], "metadata", 0);
    if (held[0] < 0) goto out;
    held[1] = attach(dir, ctl, images[1], "userdata", 0);
    if (held[1] < 0) {
        ioctl(held[0], LOOP_CLR_FD, 0); close(held[0]); held[0] = -1; unlinkat(dir, "metadata", 0); goto out;
    }
    rc = 0;
out:
    if (dir >= 0) close(dir);
    if (ctl >= 0) close(ctl);
    if (vol >= 0) close(vol);
    for (int i = 0; i < 2; ++i) if (images[i] >= 0) close(images[i]);
    return rc;
}

/* These inputs are unique, regular files within the proven outer root. */
int skyward_rom_loop(const char *alias, const char *file) {
    struct stat root, st;
    int images = open("/images", O_RDONLY|O_CLOEXEC|O_DIRECTORY|O_NOFOLLOW);
    int img = -1, dir = -1, ctl = -1, loop = -1;
    if (images < 0 || stat("/", &root) || fstat(images, &st) || st.st_dev != root.st_dev ||
        st.st_uid || (st.st_mode & 022)) goto done;
    img = backing(images, file, root.st_dev, 0, O_RDONLY);
    dir = open("/dev/block/skyward", O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    ctl = open("/dev/loop-control", O_RDWR|O_NOFOLLOW|O_CLOEXEC);
    if (img >= 0 && dir >= 0 && ctl >= 0) loop = attach(dir, ctl, img, alias, 1);
done:
    if (images >= 0) close(images);
    if (dir >= 0) close(dir);
    if (img >= 0) close(img);
    if (ctl >= 0) close(ctl);
    return loop;
}
