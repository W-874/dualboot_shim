"""Build and prepare the fuxi test package using the existing release-code readers."""
import argparse
import hashlib
import os
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
SHIM = Path(__file__).resolve().parents[1]
EVIDENCE = Path(os.environ.get('SKYWARD_REPLAY_OUT', 'replay-out')).resolve()
SYS_SHA = '168494c70b7a2955e73d5f9c74371578d8a7facf597714a25f15a35f4642d0f6'
PREFIX = None
PREFIX_SHA = '203f941390faa3540631c80f0f6013b407a5869179dbd39695b3fc1228f91384'
GIB = 1 << 30
RUN_ERRORS = (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError)

sys.path[:0] = [str(SHIM / 'harness/skyward-qemu-replay'), str(SHIM / 'tools')]
import replay

twin = replay.twin
run = twin.run
sha = twin.file_sha
require = twin.require


def check_space(path, log):
    result = run('df', '-B1', '--output=source,fstype,size,used,avail,pcent,target', path,
                 text=True, capture_output=True).stdout
    avail = int(result.splitlines()[-1].split()[4])
    with log.open('a') as f:
        f.write(result)
        if avail < 100 * GIB:
            f.write('STOP: less than 100 GiB available\n')
    require(avail >= 100 * GIB, f'less than 100 GiB available: {path}; see {log}')


def prepare_kernel(archive, device, out, start, template, system_init, init_sha):
    out.mkdir()
    require(device == 'fuxi', 'expected the fuxi USB profile')
    with zipfile.ZipFile(archive) as z, z.open('boot.img') as f:
        head = f.read(4096)
        require(len(head) == 4096 and head[:8] == b'ANDROID!' and struct.unpack_from('<I', head, 40)[0] == 4,
                'expected Android boot v4')
        length, = struct.unpack_from('<I', head, 8)
        require(64 <= length <= z.getinfo('boot.img').file_size - 4096, 'invalid kernel size')
        image = f.read(length)
    require(len(image) == length and image[56:60] == b'ARM\x64', 'expected an uncompressed AArch64 Image')
    (out / 'Image').write_bytes(image)
    with PREFIX.open('rb') as f:
        prefix = f.read(912)
    require(hashlib.sha256(prefix).hexdigest() == PREFIX_SHA, 'historical USB ramdisk prefix drift')
    (out / 'usb-prefix.cpio').write_bytes(prefix)
    init_boot = out / 'init_boot.img'
    replay.cargo('--qemu-wrap', start, template, system_init, init_boot)
    twin.rust('ramdisk', start, init_boot, out)
    require(sha(out / 'init.elf') == init_sha, 'wrapped /init differs from system-init')
    (out / 'initramfs.img').write_bytes(prefix + (out / 'skyward-ramdisk.lz4').read_bytes())
    (out / 'cmdline.txt').write_text(
        'console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 androidboot.hardware=qcom '
        'androidboot.device=fuxi androidboot.slot_suffix=_a androidboot.init_fatal_reboot_target=recovery '
        'printk.devkmsg=on random.trust_bootloader=on\n')
    twin.write_sums(out, (p.name for p in out.iterdir()))


def make_package(args, out):
    mod = runpy.run_path(str(SHIM / 'tools/skyward-candidate.py'))
    config = mod['pack'].__globals__
    src_out = args.android_out
    prod = src_out / 'target/product/fuxi'
    require(prod.is_dir() and prod.resolve() == prod, f'invalid PRODUCT_OUT: {prod}')
    five = args.five_images
    config['image_out'] = lambda device: (src_out, prod)
    build = out / 'package'
    build.mkdir()
    shutil.copyfile(args.guest_entry, build / 'guest-entry')
    checker = build / 'skyward-package-check'
    shutil.copy2(replay.checker(), checker)
    mod['write_sums'](build / 'guest.sha256', {n: sha(build / n) for n in mod['GUEST_FILES']})
    archive = build / f'uwu-fuxi-{args.images}-test.zip'
    config['test_target'] = lambda device, revision, work: archive
    (build / 'input-out.txt').write_text(f'{src_out}\n{five}\nsource ELF: {args.guest_entry}\n')
    os.environ.update(E2FSPROGS_FAKE_TIME=str(mod['core'].STAMP), TZ='UTC')
    mod['pack']('fuxi', f'qemu-{args.images}', build, five, offline=True)
    os.environ.pop('E2FSPROGS_FAKE_TIME', None)
    return archive


def prepare(args):
    import fcntl
    global PREFIX
    require(all((args.android_out, args.five_images, args.system_init, args.usb_prefix, args.build_lock)),
            'prepare requires --android-out, --five-images, --system-init, --usb-prefix and --build-lock')
    PREFIX = args.usb_prefix

    out = args.out
    if not out.is_absolute():
        out = EVIDENCE / out
    out = out.resolve()
    require(out.is_relative_to(EVIDENCE) and out != EVIDENCE, '--out must be inside SKYWARD_REPLAY_OUT')
    out.mkdir(mode=0o700, parents=True)
    check_space(out, out / 'df.log')
    os.environ.update(LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')
    tmp = out / 'tmp'
    tmp.mkdir()
    os.environ['TMPDIR'] = str(tmp)
    with args.build_lock.open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        check_space(out, out / 'df.log')
        archive = make_package(args, out)
    replay.prepare_kernel = lambda *params: prepare_kernel(archive, *params)
    replay.prepare(argparse.Namespace(package=archive, package_sha256=None, system_init=args.system_init,
                 system_init_sha256=args.system_init_sha256, device='fuxi', out=out / 'work', rootfs_growth_mib=0))
    # Prepare validates the complete ZIP and the extracted ELF independently.
    require(sha(out / 'work/guest-entry.elf') == sha(out / 'package/guest-entry'), 'prepared ELF mismatch')
    return out


def add_arguments(cli, *, optional=False):
    cli.add_argument('guest_entry', type=lambda s: Path(s).resolve(), nargs='?' if optional else None,
                     help='static AArch64 guest-entry ELF')
    cli.add_argument('images', choices=('official', 'softkm'), nargs='?' if optional else None)
    cli.add_argument('--out', type=Path, required=not optional, help='new directory below SKYWARD_REPLAY_OUT')
    cli.add_argument('--android-out', type=lambda s: Path(s).resolve(), help='explicit Android OUT (including for softkm)')
    cli.add_argument('--five-images', type=lambda s: Path(s).resolve(), help='approved input image SHA-256 list')
    cli.add_argument('--system-init', type=lambda s: Path(s).resolve())
    cli.add_argument('--usb-prefix', type=lambda s: Path(s).resolve(), help='historical USB initramfs prefix input')
    cli.add_argument('--build-lock', type=lambda s: Path(s).resolve(), help='same-OUT read lock shared with the build')
    cli.add_argument('--system-init-sha256', default=SYS_SHA)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    add_arguments(cli)
    try:
        prepare(cli.parse_args())
    except RUN_ERRORS as err:
        cli.exit(1, f'prepare: {err}\n')
