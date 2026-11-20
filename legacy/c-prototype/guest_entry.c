/* Lab PID 1. Do not run Android services until the remaining isolation gates pass. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <linux/loop.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <unistd.h>

int skyward_guest_volumes(int held[2]);
int skyward_claim_format(int image, int loop);
int skyward_rom_loop(const char *, const char *);
static const struct part { const char *name, *target, *fs; } parts[] = {
    {"system", "/android", "ext4"}, {"vendor", "/android/vendor", "ext4"},
    {"product", "/android/product", "ext4"}, {"system_ext", "/android/system_ext", "ext4"},
    {"odm", "/android/odm", "ext4"}, {"system_dlkm", "/android/system_dlkm", "erofs"},
    {"vendor_dlkm", "/android/vendor_dlkm", "erofs"}
};

static void stop(const char *msg) {
    dprintf(2,"skyward-guest: STOP %s (%s)\n", msg, strerror(errno));
    for (;;) pause();
}

static void check_after_exec(void) {
    /* Revalidate both reopened block devices against kernel-recorded backing inodes. */
    const char *files[] = {"/volumes/metadata.ext4", "/volumes/userdata.raw"};
    const char *nodes[] = {"/dev/block/skyward/metadata", "/dev/block/skyward/userdata"};
    for (size_t i=0;i<2;i++) {
        struct stat st;
        struct loop_info64 info;
        int fd = open(nodes[i],O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
        if (fd<0 || stat(files[i], &st) || ioctl(fd, LOOP_GET_STATUS64, &info) ||
            info.lo_inode!=st.st_ino || info.lo_device!=(unsigned long long)st.st_dev ||
            info.lo_offset || info.lo_sizelimit || (info.lo_flags & (LO_FLAGS_READ_ONLY|LO_FLAGS_AUTOCLEAR))) stop(nodes[i]);
        close(fd);
    }
    for (size_t i=0;i<sizeof(parts)/sizeof(*parts);i++) {
        struct statfs s;
        struct stat a, b;
        char node[96];
        snprintf(node,sizeof(node),"/dev/block/skyward/%s",parts[i].name);
        if (stat(node,&a) || stat(parts[i].target,&b) || a.st_rdev!=b.st_dev ||
            statfs(parts[i].target,&s) || !(s.f_flags & MS_RDONLY)) stop(parts[i].name);
    }
    if (access("/android/system/bin/init",X_OK)) stop("Android init missing");
    int image = open(files[1], O_RDWR|O_NOFOLLOW|O_CLOEXEC);
    int loop = open(nodes[1], O_RDONLY|O_NOFOLLOW|O_CLOEXEC);
    int claim = skyward_claim_format(image, loop);
    if (claim < 0) stop("format authorization refused");
    close(image); close(loop);
    dprintf(2,"skyward-guest: format authorization %s\n", claim ? "consumed" : "absent or spent");
    dprintf(2,"skyward-guest: PASS seven read-only mounts and both writable loop identities after exec\n");
    stop("Android handoff withheld: credential and storage isolation not verified");
}

int main(int argc, char **argv) {
    if (getpid()!=1) return 1;
    if (argc==2 && !strcmp(argv[1],"--check-exec")) check_after_exec();
    int held[2];
    if (argc!=1 || skyward_guest_volumes(held)) stop("guest volumes");
    for (size_t i=0;i<sizeof(parts)/sizeof(*parts);i++) {
        char file[64], node[96];
        snprintf(file,sizeof(file),"%s.img",parts[i].name);
        snprintf(node,sizeof(node),"/dev/block/skyward/%s",parts[i].name);
        int fd = skyward_rom_loop(parts[i].name,file);
        struct stat st;
        if (fd<0 || lstat(parts[i].target,&st) || !S_ISDIR(st.st_mode) ||
            mount(node,parts[i].target,parts[i].fs,MS_RDONLY|MS_NODEV,
                  strcmp(parts[i].fs,"ext4")==0 ? "noload" : NULL)) stop(parts[i].name);
        close(fd);
    }
    /* No inherited loop fd is needed after exec. The check runs as PID 1. */
    close(held[0]); close(held[1]);
    execl("/guest-entry","/guest-entry","--check-exec",(char *)NULL);
    stop("exec");
}
