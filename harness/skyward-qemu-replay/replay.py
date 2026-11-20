#!/usr/bin/env python3
"""Prepare, boot, and inspect a production Skyward package on an isolated QEMU virt twin."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import tempfile
import tomllib
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'fuxi-twin'))
import fixtures as twin

PROJECT = twin.PROJECT
MEMBERS = {'system.toml', 'boot.img', 'rootfs.img'}
DADA_RELEASE = '6.6.118-android15-8-gb9cc6ec16bc8-abogki536571621-4k'
DADA_MODULES = ('virtio_pci_legacy_dev', 'virtio_pci_modern_dev', 'virtio_pci', 'virtio_blk')
DADA_REFUSAL = 'bootloader/DT device identity does not match dada'


def checksums(path):
    entries = {}
    for line in twin.regular(path).read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.removeprefix('*')
        twin.require(re.fullmatch('[0-9a-fA-F]{64}', digest) and name not in entries,
                     f'{path}: invalid or duplicate checksum: {line}')
        entries[name] = digest.lower()
    return entries


def checked_input(path, digest):
    path = twin.regular(path).resolve()
    twin.require(twin.file_sha(path) == digest, f'SHA-256 mismatch: {path}')
    return path


def input_digest(path, value):
    if value is not None:
        twin.require(re.fullmatch('[0-9a-fA-F]{64}', value), f'{path}: expected SHA-256 hex')
        return value.lower()
    entries = checksums(path.with_name(path.name + '.sha256'))
    twin.require(path.name in entries, f'{path}: no matching entry in checksum sidecar')
    return entries[path.name]


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def checker():
    return twin.regular(Path(os.environ['SKYWARD_PACKAGE_CHECK']).absolute()).resolve()


def cargo(*args):
    twin.run(checker(), *args)


def package_inputs(args, out):
    hashes = {}
    with zipfile.ZipFile(args.package) as archive:
        items = archive.infolist()
        twin.require(len(items) == len(MEMBERS) and {item.filename for item in items} == MEMBERS,
                     'expected exactly system.toml, boot.img and rootfs.img (no own init_boot)')
        cfg = archive.getinfo('system.toml')
        twin.require(0 < cfg.file_size <= 16 * 1024, 'invalid system.toml size')
        raw = archive.read(cfg)
        package = tomllib.loads(raw.decode())
        info = archive.getinfo('rootfs.img')
        twin.require(package.get('device') == args.device and package.get('id') == 'uwu-' + args.device
                     and package.get('filesystem') == 'ext4' and package.get('init') == '/guest-entry'
                     and type(package.get('minimum')) is int and package['minimum'] >= info.file_size
                     and info.file_size > 4096 and info.file_size % 4096 == 0,
                     'wrong device, init, filesystem or size in production package')
        for key in ('host_boot_sha256', 'host_vendor_boot_sha256', 'host_dtbo_sha256'):
            twin.require(isinstance(package.get(key), str) and re.fullmatch('[0-9a-f]{64}', package[key]),
                         f'missing production host binding: {key}')
        system = out / 'system.toml'
        system.write_bytes(raw)
        hashes[cfg.filename] = hashlib.sha256(raw).hexdigest()
        boot = archive.getinfo('boot.img')
        cargo(args.package, args.device, package['version'], str(package['minimum']),
              str(boot.file_size), str(info.file_size), system)
        for item in (boot, info):
            sha = hashlib.sha256()
            with archive.open(item) as src:
                if item == boot:
                    # The package boot is recorded, not used instead of the virt kernel.
                    sha = hashlib.file_digest(src, 'sha256')
                else:
                    with (out / item.filename).open('xb') as dst:
                        while data := src.read(4 * twin.MIB):
                            sha.update(data)
                            if data == bytes(len(data)):
                                dst.seek(len(data), os.SEEK_CUR)
                            else:
                                dst.write(data)
                        twin.require(dst.tell() == item.file_size, 'short rootfs extraction')
                        dst.truncate()
            hashes[item.filename] = sha.hexdigest()
    debugfs = '/usr/sbin/debugfs'
    root = out / 'rootfs.img'
    guest = twin.run(debugfs, '-R', 'cat /skyward-guest.toml', root, capture_output=True).stdout
    twin.require(tomllib.loads(guest.decode()).get('device') == args.device, 'rootfs guest device differs from package')
    (out / 'skyward-guest.toml').write_bytes(guest)
    twin.run(debugfs, '-R', 'dump /guest-entry guest-entry.elf', root, cwd=out, capture_output=True)
    twin.static_arm64(out / 'guest-entry.elf')
    (out / 'members.sha256').write_text(''.join(f'{sha}  {name}\n' for name, sha in hashes.items()))
    return package, hashes


def dada_kernel(entries):
    source = twin.HERE / 'dada-gki.sha256'
    files = {Path(name).name: checked_input(PROJECT / name, sha) for name, sha in checksums(source).items()}
    twin.require(set(files) == {'boot.img', *(name + '.ko' for name in DADA_MODULES)}, 'unexpected Dada GKI fixture manifest')
    with files['boot.img'].open('rb') as f:
        head = f.read(4096)
        size = struct.unpack_from('<I', head, 8)[0]
        twin.require(head[:8] == b'ANDROID!' and struct.unpack_from('<I', head, 40)[0] == 4,
                     'expected stock Dada boot v4')
        image = f.read(size)
        twin.require(len(image) == size, 'truncated Dada kernel')
    wanted = (*twin.GUEST_BUILTINS, 'ARM64_4K_PAGES', 'PCI', 'PCI_HOST_GENERIC', 'MODVERSIONS')
    config = twin.image_config(image, DADA_RELEASE, wanted)
    modular = ('VIRTIO_PCI', 'VIRTIO_PCI_LIB', 'VIRTIO_PCI_LIB_LEGACY', 'VIRTIO_BLK')
    twin.require(all(f'CONFIG_{name}=m' in config for name in modular), 'Dada PCI module config differs')
    checks = ['profile=dada-stock-gki-virtio-pci', f'release={DADA_RELEASE}',
              f'stock_inputs_manifest_sha256={twin.file_sha(source)}',
              *[f'CONFIG_{name}=y' for name in wanted], *[f'CONFIG_{name}=m' for name in modular]]
    dep, aliases = [], []
    for name in DADA_MODULES:
        path = files[name + '.ko']
        data = path.read_bytes()
        twin.require(data[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<HH', data, 16) == (1, 183),
                     f'{name}: not AArch64 relocatable ELF')

        def mod(field):
            return twin.run('modinfo', '-F', field, path, text=True, capture_output=True).stdout.strip()

        vermagic = mod('vermagic')
        deps = set(filter(None, mod('depends').split(',')))
        expected = set(DADA_MODULES[:2]) if name == 'virtio_pci' else set()
        twin.require(vermagic.startswith(DADA_RELEASE + ' ') and vermagic.endswith(' aarch64')
                     and deps == expected and not mod('softdep'), f'{name}: unexpected ABI or dependency closure')
        entries.append((f'lib/modules/{name}.ko', 0o100644, data))
        dep.append(f'{name}.ko:' + ''.join(f' {dep}.ko' for dep in DADA_MODULES if dep in deps) + '\n')
        aliases.extend(f'alias {alias} {name}\n' for alias in mod('alias').splitlines())
        checks.append(f'{name}.ko\t{vermagic}\tdepends={",".join(sorted(deps)) or "(none)"}')
    lists = {'modules.load': ''.join(f'{name}.ko\n' for name in DADA_MODULES),
             'modules.dep': ''.join(dep), 'modules.alias': ''.join(aliases), 'modules.softdep': ''}
    entries.extend((f'lib/modules/{name}', 0o100644, value.encode()) for name, value in lists.items())
    return image, checks


def prepare_kernel(device, out, start, template, system_init, init_sha):
    out.mkdir()
    entries = [('lib', 0o40755, b''), ('lib/modules', 0o40755, b'')]
    image, checks = (twin.pinned_gki if device == 'fuxi' else dada_kernel)(entries)
    (out / 'Image').write_bytes(image)
    (out / 'kernel-checks.txt').write_text('\n'.join(checks) + '\n')
    # For fuxi use the existing virt-compatible production path exercised by boot05.
    # For dada these boot properties do not satisfy its board or UFS checks.
    cmdline = (f'console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 androidboot.hardware=qcom '
               f'androidboot.device={device} androidboot.slot_suffix=_a androidboot.init_fatal_reboot_target=recovery '
               'printk.devkmsg=on random.trust_bootloader=on')
    (out / 'cmdline.txt').write_text(cmdline + '\n')
    init_boot = out / 'init_boot.img'
    cargo('--qemu-wrap', start, template, system_init, init_boot)
    twin.rust('ramdisk', start, init_boot, out)
    twin.require(twin.file_sha(out / 'init.elf') == init_sha, 'wrapped /init differs from release binary')
    vendor = twin.newc(entries)
    (out / 'vendor-ramdisk.cpio').write_bytes(vendor)
    (out / 'initramfs.img').write_bytes(vendor + (out / 'skyward-ramdisk.lz4').read_bytes())
    twin.write_sums(out, (item.name for item in out.iterdir()))


def prepare(args):
    zip_sha = input_digest(args.package, args.package_sha256)
    init_sha = input_digest(args.system_init, args.system_init_sha256)
    args.package = checked_input(args.package, zip_sha)
    system_init = checked_input(args.system_init, init_sha)
    twin.static_arm64(system_init)
    images = PROJECT / 'host-baseline' / args.device / 'images'
    template = checked_input(images / 'init_boot.img', checksums(images / 'SHA256SUMS')['init_boot.img'])
    args.out.mkdir(mode=0o700)
    out = args.out.resolve()
    os.environ['CARGO_TARGET_DIR'] = str(out / 'cargo')
    os.environ['CARGO_NET_OFFLINE'] = 'true'
    package, hashes = package_inputs(args, out)
    disk = out / 'twin'
    disk.mkdir()
    args.rootfs = out / 'rootfs.img'
    args.seed = f'production:{args.device}:{hashes["rootfs.img"]}'
    args.scale_divisor = twin.DEFAULT_DIVISOR
    twin.disk_fixture(args, disk, package=package)
    fixture = json.loads((disk / 'fixture.json').read_text())
    twin.require(fixture['rootfs_sha256'] == hashes['rootfs.img'], 'rootfs digest differs on disk')
    args.rootfs.unlink()
    (disk / 'disk.raw').chmod(0o444)
    prepare_kernel(args.device, out / 'kernel', disk / 'start.toml', template, system_init, init_sha)
    write_json(out / 'prepared.json', dict(device=args.device, package=str(args.package), package_sha256=zip_sha,
              package_members=hashes, release_system_init=str(system_init), system_init_sha256=init_sha,
              guest_entry_sha256=twin.file_sha(out / 'guest-entry.elf'), template_sha256=twin.file_sha(template),
              stock_init_boot_template=str(template), disk_sums_sha256=twin.file_sha(disk / 'SHA256SUMS'),
              kernel_sums_sha256=twin.file_sha(out / 'kernel/SHA256SUMS')))
    print(f'prepared {args.device} {package["version"]}: {out}')


def prepared(path, verify=False):
    work = path.resolve()
    info = json.loads(twin.regular(work / 'prepared.json').read_text())
    twin.require(info['device'] in ('fuxi', 'dada'), 'unknown prepared replay profile')
    for name, key in [('twin', 'disk'), ('kernel', 'kernel')]:
        directory = work / name
        sums = checked_input(directory / 'SHA256SUMS', info[f'{key}_sums_sha256'])
        if verify:
            twin.check_inputs(directory, sums)
    return work, info['device']


def run_vm(args):
    work, device = prepared(args.work, verify=True)
    base = twin.regular(work / 'twin/disk.raw')
    twin.require(not base.stat().st_mode & 0o222, 'base disk must be read-only')
    qemu = twin.regular(args.qemu.resolve())
    args.out.mkdir(mode=0o700)
    out = args.out.resolve()
    overlay = out / 'disk.qcow2'
    twin.run('qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', base, overlay)
    kernel = work / 'kernel'
    block = {'driver': 'qcow2', 'node-name': 'twin', 'file': {'driver': 'file', 'filename': str(overlay)},
             'backing': {'driver': 'raw', 'read-only': True, 'file': {'driver': 'file', 'filename': str(base)}}}
    transport = 'virtio-blk-device' if device == 'fuxi' else 'virtio-blk-pci'
    cmd = ['timeout', '--kill-after=10s', f'{args.limit}s', str(qemu), '-machine', 'virt,accel=tcg',
           '-cpu', 'max', '-smp', '4', '-m', '4096', '-no-user-config', '-nodefaults', '-display', 'none', '-serial', 'stdio',
           '-monitor', 'none', '-nic', 'none', '-no-reboot', '-kernel', str(kernel / 'Image'),
           '-initrd', str(kernel / 'initramfs.img'), '-append', (kernel / 'cmdline.txt').read_text().strip(),
           '-blockdev', json.dumps(block), '-device', f'{transport},drive=twin']
    version = twin.run(qemu, '--version', capture_output=True, text=True).stdout
    (out / 'command.log').write_text(version + shlex.join(cmd) + '\n')
    with (out / 'serial.log').open('xb') as log:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    rc = result.returncode
    status = {0: 'qemu-exit-inspect-serial', 124: 'host-timeout', 137: 'sigkill'}.get(rc, 'launcher-error')
    write_json(out / 'exit.json', dict(work=str(work), device=device, status=status, exit_status=rc, limit=args.limit,
                                      prepared_sha256=twin.file_sha(work / 'prepared.json')))
    print(f'{device}: {status} ({rc}); see {out / "serial.log"}')
    return 0 if rc in (0, 124) else 1


def image_slice(overlay, offset, size, target):
    # Commas in a host filename are escaped in QEMU keyval syntax.
    filename = str(overlay).replace(',', ',,')
    spec = (f'driver=raw,offset={offset},size={size},file.driver=qcow2,'
            f'file.file.driver=file,file.file.filename={filename}')
    twin.run('qemu-img', 'convert', '--image-opts', '-O', 'raw', '-S', '4k', spec, target)


def readback(args):
    out = args.run.resolve()
    work, device = prepared(args.work)
    result = json.loads(twin.regular(out / 'exit.json').read_text())
    twin.require(result['work'] == str(work) and result['device'] == device
                 and result['prepared_sha256'] == twin.file_sha(work / 'prepared.json'), 'run belongs to another fixture')
    overlay = twin.regular(out / 'disk.qcow2')
    info = json.loads(twin.run('qemu-img', 'info', '-f', 'qcow2', '--output=json', overlay, capture_output=True).stdout)
    twin.require(info.get('backing-filename') == str(twin.regular(work / 'twin/disk.raw'))
                 and info.get('backing-filename-format') == 'raw', 'unexpected overlay backing image')
    ranges = work / 'twin/ranges.tsv'
    twin.require(twin.file_sha(ranges) == checksums(work / 'twin/SHA256SUMS')['ranges.tsv'], 'range manifest changed')
    with ranges.open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    misc, = (row for row in rows if row['range'] == 'misc@0')
    root, = (row for row in rows if row['range'].startswith('userdata@') and row['policy'] == 'mutable')
    bcb = out / 'bcb.bin'
    twin.require(not os.path.lexists(bcb), 'readback already exists')
    image_slice(overlay, int(misc['offset']), 2048, bcb)
    raw = bcb.read_bytes()
    command = raw[0:32].split(b'\0', 1)[0].decode(errors='replace')
    recovery = raw[64:832].split(b'\0', 1)[0].decode(errors='replace').splitlines()
    serial = twin.regular(out / 'serial.log').read_text(errors='replace')
    report = dict(bcb_sha256=twin.file_sha(bcb), command=command, recovery=recovery,
                  exit_status=result['exit_status'], exit_reason=result['status'],
                  handoff_seen='handing over to /guest-entry' in serial,
                  android_second_stage_seen='init second stage started' in serial,
                  bootstrap_apex_seen=bool(re.search(r'apexd-bootstrap.*Activated', serial, re.IGNORECASE)),
                  boot_completed_action_seen=bool(re.search(r'init: processing action \(sys\.boot_completed=1\) from ', serial)))
    if device == 'dada':
        report['expected_identity_refusal_observed'] = (report['handoff_seen'] and DADA_REFUSAL in serial
            and command == 'boot-recovery' and recovery[:2] == ['recovery', '--system_stopped']
            and any(line.startswith('--reason=') and DADA_REFUSAL in line for line in recovery[2:])
            and not report['android_second_stage_seen'] and not report['boot_completed_action_seen'])
    write_json(out / 'readback.json', report)
    with tempfile.TemporaryDirectory(prefix='readback-', dir=out) as td:
        temp = Path(td)
        if args.verify_ranges:
            raw = temp / 'after.raw'
            twin.run('qemu-img', 'convert', '-f', 'qcow2', '-O', 'raw', '-S', '4k', overlay, raw)
            with (out / 'ranges.log').open('xb') as log:
                twin.run(sys.executable, twin.HERE / 'fixtures.py', 'verify', '--disk', raw,
                         '--ranges', ranges, '--allow-mutable', stdout=log)
            raw.unlink()
        if args.volumes:
            image = temp / 'rootfs.img'
            image_slice(overlay, int(root['offset']), int(root['size']), image)
            with (out / 'volumes.log').open('xb') as log:
                for cmd in ('ls -l /volumes', 'ls -l /volumes/state', 'cat /volumes/logs/guest-entry',
                            'cat /volumes/state/volumes-ready', 'cat /volumes/state/probe-ok',
                            'ea_list /volumes/userdata.raw',
                            *(f'ea_get /volumes/{name} security.selinux' for name in
                              ('metadata.ext4', 'persist.img', 'userdata.raw')),
                            *(f'ea_get /images/{name}.img security.selinux' for name in
                              ('system', 'system_ext', 'product', 'vendor', 'odm', 'system_dlkm', 'vendor_dlkm'))):
                    log.write(f'\n$ debugfs -R {cmd}\n'.encode())
                    log.flush()
                    twin.run('/usr/sbin/debugfs', '-R', cmd, image, stdout=log, stderr=log)
    print(json.dumps(report, indent=2))
    return int(device == 'dada' and not report['expected_identity_refusal_observed'])


def main():
    cli = argparse.ArgumentParser(description=__doc__, epilog=(
        'Fuxi replays up to the stage reached by the production guest. Dada uses a matched Android 15 '
        '6.6/4K PCI kernel and must refuse plain virt DT; the UFS check would also reject its disk. '
        'A QEMU exit or host timeout does not establish Android boot success. No real block device, '
        'DT override, phone recovery image, or network is attached.'))
    sub = cli.add_subparsers(dest='command', required=True)
    new = sub.add_parser('prepare', help='verify a production ZIP and prepare private disk and initramfs fixtures')
    new.add_argument('--device', choices=('fuxi', 'dada'), required=True)
    new.add_argument('--package', type=Path, required=True)
    new.add_argument('--package-sha256', help='approved digest; defaults to matching entry in ZIP.sha256')
    new.add_argument('--system-init', type=Path, required=True, help='unmodified release AArch64 system-init')
    new.add_argument('--system-init-sha256', help='approved digest; defaults to matching entry in system-init.sha256')
    new.add_argument('--rootfs-growth-mib', type=int, default=0, help='reserve at least this much growth, in addition to honoring package minimum')
    new.add_argument('--out', type=Path, required=True, help='new work directory')
    vm = sub.add_parser('run', help='run QEMU virt with a fresh qcow2 overlay and preserved serial output')
    vm.add_argument('--work', type=Path, required=True)
    vm.add_argument('--qemu', type=Path, required=True, help='qemu-system-aarch64 executable')
    vm.add_argument('--limit', type=int, default=650, help='host timeout in seconds')
    vm.add_argument('--out', type=Path, required=True, help='new run directory')
    inspect = sub.add_parser('readback', help='read BCB and optionally volumes / range integrity without host mounts')
    inspect.add_argument('--work', type=Path, required=True)
    inspect.add_argument('--run', type=Path, required=True)
    inspect.add_argument('--verify-ranges', action='store_true')
    inspect.add_argument('--volumes', action='store_true')
    args = cli.parse_args()
    if args.command == 'prepare':
        twin.require(args.rootfs_growth_mib >= 0, 'negative rootfs growth')
        return prepare(args)
    if args.command == 'run':
        twin.require(args.limit > 0, 'host timeout must be positive')
        return run_vm(args)
    return readback(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile) as e:
        sys.exit(f'skyward-qemu-replay: {e}')
