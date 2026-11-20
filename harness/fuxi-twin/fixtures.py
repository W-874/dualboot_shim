#!/usr/bin/env python3
"""Offline fuxi fixture preparation (no mounts, block devices or emulator)."""

import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib


HERE = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get('SKYWARD_EVIDENCE_ROOT', '.')).resolve()
P1_GPT = PROJECT / 'evidence/P1.1/fuxi/gpt'
PREBUILT = PROJECT / 'evidence/P6.2/fuxi/prebuilt'
RELEASE = '5.15.41-android13-8-00029-g24d27dff64c4-ab9178016'
GKI_SUMS_SHA256 = 'ae07155b95fa6605504d496d56f74488e6cc00d2eb94d07e86db9133b2f227d4'
SYMVER_SHA256 = '71d2e4cc2fca99c7558a2b765c329a789a4f49de75f4b3f20e190f6aa6f779e8'
SCENARIO_RELEASE = '5.15.41-android13-fuxi-p63-g24d27dff64c4'
GUEST_BUILTINS = (
    'ARM64', 'VIRTIO', 'BLK_DEV_DM', 'BLK_DEV_LOOP', 'EFI_PARTITION', 'EXT4_FS',
    'EROFS_FS', 'F2FS_FS', 'DM_CRYPT', 'DM_DEFAULT_KEY', 'DM_SNAPSHOT', 'DM_VERITY',
    'BLK_INLINE_ENCRYPTION', 'BLK_INLINE_ENCRYPTION_FALLBACK', 'FS_ENCRYPTION',
    'FS_VERITY', 'KEYS', 'CRYPTO_AES', 'CRYPTO_XTS', 'CRYPTO_CTS', 'CRYPTO_SHA256',
    'ANDROID_BINDER_IPC', 'ANDROID_BINDERFS', 'SECURITY_SELINUX',
    'SERIAL_AMBA_PL011_CONSOLE', 'RD_LZ4', 'BLK_DEV_INITRD', 'BOOT_CONFIG',
)
SCENARIO_BUILTINS = ('VIRTIO_MMIO', 'VIRTIO_BLK', 'SCSI', 'SCSI_VIRTIO', 'BLK_DEV_SR', 'BLK_DEV_BSG', 'CDROM')
MODULES = ('virtio_mmio.ko', 'virtio_blk.ko')
SEED = 'fuxi-OS3.0.309.0.WMCCNXM-p6-v1'
EPOCH = '1700000000'
SECTOR, BLOCK, MIB = 512, 4096, 1 << 20
DEFAULT_DIVISOR = 128


def require(ok, message):
    if not ok:
        raise ValueError(message)


def regular(path):
    path = Path(path)
    require(stat.S_ISREG(path.lstat().st_mode), f'{path}: must be a regular file, not a symlink or device')
    return path


def run(*argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def rust(*argv):
    run(Path(os.environ['SKYWARD_TWIN']).resolve(strict=True), *map(str, argv))


def div_up(n, d):
    return (n + d - 1) // d


def align(n, boundary):
    return div_up(n, boundary) * boundary


def sum_range(f, offset, size):
    f.seek(offset)
    sha = hashlib.sha256()
    while size:
        data = f.read(min(size, 4 * MIB))
        require(data, f'short read at {f.tell()} from {f.name}')
        sha.update(data)
        size -= len(data)
    return sha.hexdigest()


def file_sha(path):
    with regular(path).open('rb') as f:
        return sum_range(f, 0, os.fstat(f.fileno()).st_size)


def check_inputs(source, manifest):
    seen = set()
    for row in regular(manifest).read_text().splitlines():
        sha, name = row.split()
        require(name == Path(name).name and name not in seen, f'invalid checksum entry: {name}')
        require(file_sha(source / name) == sha, f'input SHA mismatch: {name}')
        seen.add(name)
    return seen


def identity(seed, label):
    return uuid.uuid5(uuid.NAMESPACE_URL, f'skyward:fuxi-twin:v1:{seed}:{label}')


def stock_partitions():
    names = {f'gpt_main{i}.bin' for i in range(6)}
    require(check_inputs(P1_GPT, P1_GPT / 'SHA256SUMS') == names, 'expected the six P1.1 fuxi primary GPT fragments')
    parts = []
    for lun in range(6):
        b = (P1_GPT / f'gpt_main{lun}.bin').read_bytes()
        h = b[BLOCK:2 * BLOCK]
        sig, rev, hsize, hcrc, _, current, _, lo, hi = struct.unpack_from('<8sIIIIQQQQ', h)
        require((sig, rev, hsize, current) == (b'EFI PART', 0x10000, 92, 1), f'LUN {lun}: unexpected primary GPT')
        require(zlib.crc32(h[:16] + bytes(4) + h[20:hsize]) == hcrc, f'LUN {lun}: header CRC mismatch')
        lba, count, width, want = struct.unpack_from('<QIII', h, 72)
        entries = b[lba * BLOCK:lba * BLOCK + count * width]
        require(width == 128 and len(entries) == count * width and zlib.crc32(entries) == want, f'LUN {lun}: entry-array CRC mismatch')
        prev = lo - 1
        for i in range(count):
            e = entries[i * width:(i + 1) * width]
            if not any(e[:16]):
                continue
            first, last, attrs = struct.unpack_from('<QQQ', e, 32)
            name = e[56:].decode('utf-16le').rstrip('\0')
            # The fastboot template is resized on-device: userdata is a named, zero-length tail placeholder.
            require((prev < first <= last <= hi) or (lun == 0 and name == 'userdata' and first == hi + 1 and last == hi), f'LUN {lun}: invalid LBA range for {name}')
            require(re.fullmatch(r'[A-Za-z0-9_-]+', name), f'invalid partition label: {name}')
            parts.append({'lun': lun, 'stock_index': i + 1, 'name': name, 'stock_bytes': (last - first + 1) * BLOCK,
                          'type_guid': str(uuid.UUID(bytes_le=e[:16])), 'attrs': attrs})
            prev = last
    require(len(parts) == len({p['name'] for p in parts}) == 129, 'unexpected fuxi GPT population')
    return parts


def dada_partitions():
    # Only the labels the replay needs; no Dada factory GPT was captured.
    linux_fs = '0fc63daf-8483-4772-8e79-3d69d8477de4'
    sizes = {'misc': MIB, 'metadata': 16 * MIB, 'persist': 16 * MIB,
             'boot_a': 96 * MIB, 'init_boot_a': 8 * MIB, 'vendor_boot_a': 96 * MIB,
             'dtbo_a': 24 * MIB, 'userdata': 0}
    return [dict(lun=0, stock_index=0, name=name, stock_bytes=size, type_guid=linux_fs, attrs=0)
            for name, size in sizes.items()]


def write_gpt(f, seed, parts):
    entry_count = 256  # 129 named entries: do not silently truncate to 128 GPT slots.
    table = bytearray(entry_count * 128)
    table_sectors = len(table) // SECTOR
    limit = align(parts[-1]['offset'] + parts[-1]['size'] + MIB, MIB) // SECTOR
    first, last = 2 + table_sectors, limit - 2 - table_sectors
    for i, p in enumerate(parts):
        off = i * 128
        start = p['offset'] // SECTOR
        end = start + p['size'] // SECTOR - 1
        struct.pack_into('<16s16sQQQ', table, off, uuid.UUID(p['type_guid']).bytes_le,
                         identity(seed, p['name']).bytes_le, start, end, p['attrs'])
        name = p['name'].encode('utf-16le')
        table[off + 56:off + 56 + len(name)] = name
    mbr = bytearray(SECTOR)
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\0\x02\0', 0xEE, b'\xff\xff\xff', 1, min(limit - 1, 0xffffffff))
    mbr[510:] = b'\x55\xaa'
    f.truncate(limit * SECTOR)
    f.write(mbr)
    disk_id = identity(seed, 'disk').bytes_le
    for current, backup, entry_lba in ((1, limit - 1, 2), (limit - 1, 1, last + 1)):
        h = bytearray(SECTOR)
        struct.pack_into('<8sIIIIQQQQ16sQIII', h, 0, b'EFI PART', 0x10000, 92, 0, 0, current, backup,
                         first, last, disk_id, entry_lba, entry_count, 128, zlib.crc32(table))
        struct.pack_into('<I', h, 16, zlib.crc32(h[:92]))
        f.seek(current * SECTOR)
        f.write(h)
        f.seek(entry_lba * SECTOR)
        f.write(table)
    return limit * SECTOR


def sentinel(f, seed, name, offset, size, except_range=None):
    sha = hashlib.sha256()
    prefix = f'{seed}\0{name}\0'.encode('utf-8')
    for i in range(0, size, MIB):
        length = min(MIB, size - i)
        data = hashlib.shake_256(prefix + struct.pack('<Q', i)).digest(length)
        sha.update(data)
        # Avoid a large write that an overlay (rootfs, boot store) will fully replace.
        a, b = except_range or (0, 0)
        lo = min(i + length, max(i, a))
        hi = min(i + length, max(i, b))
        if lo > i:
            f.seek(offset + i)
            f.write(data[:lo - i])
        if hi < i + length:
            f.seek(offset + hi)
            f.write(data[hi - i:])
    return sha.hexdigest()


def copy_at(source, disk, offset, length=None, source_offset=0, sparse=False):
    with regular(source).open('rb') as src:
        if length is None:
            length = os.fstat(src.fileno()).st_size - source_offset
        src.seek(source_offset)
        disk.seek(offset)
        while length:
            b = src.read(min(length, 4 * MIB))
            require(b, f'short read: {source}')
            if sparse and b == bytes(len(b)):
                disk.seek(len(b), os.SEEK_CUR)
            else:
                disk.write(b)
            length -= len(b)


def ext4(path, size, seed, label, state=None):
    with path.open('xb') as f:
        f.truncate(size)
    env = dict(os.environ, LC_ALL='C', TZ='UTC', E2FSPROGS_FAKE_TIME=EPOCH,
               MKE2FS_CONFIG=str(HERE / 'mke2fs.conf'))
    tool = '/usr/sbin/'
    run(tool + 'mke2fs', '-q', '-t', 'ext4', '-b', str(BLOCK), '-I', '256', '-i', '16384', '-m', '0',
        '-O', 'none,extent,filetype,dir_index,sparse_super,large_file,ext_attr,metadata_csum',
        '-U', str(identity(seed, label + '-uuid')), '-E', f'lazy_itable_init=0,hash_seed={identity(seed, label + "-hash")}',
        '-L', label, path, env=env)
    if state is not None:
        # Relative input avoids debugfs command-language quoting and uses fake-time for all ext4 timestamps.
        def command(text):
            run(tool + 'debugfs', '-w', '-R', text, path, cwd=state.parent, env=env, capture_output=True)
        command('mkdir /skyward')
        command('set_inode_field /skyward uid 0')
        command('set_inode_field /skyward gid 0')
        command('write systems.toml /skyward/systems.toml')
        command('set_inode_field /skyward/systems.toml mode 0100600')
        command('set_inode_field /skyward/systems.toml uid 0')
        command('set_inode_field /skyward/systems.toml gid 0')
        check = run(tool + 'debugfs', '-R', 'cat /skyward/systems.toml', path, env=env, capture_output=True).stdout
        require(check == state.read_bytes(), 'state did not round trip through metadata ext4')
    run(tool + 'e2fsck', '-fn', path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_sums(out, names):
    (out / 'SHA256SUMS').write_text(''.join(f'{file_sha(out / name)}  {name}\n' for name in sorted(names)))


def static_arm64(path):
    with regular(path).open('rb') as f:
        head = f.read(64)
        require(len(head) == 64 and head[:7] == b'\x7fELF\x02\x01\x01', f'{path}: not little-endian ELF64')
        kind, machine = struct.unpack_from('<HH', head, 16)
        phoff, entsize, count = struct.unpack_from('<Q14xHH', head, 32)
        require(machine == 183 and kind in (2, 3) and entsize == 56 and count > 0, f'{path}: not an AArch64 executable')
        f.seek(phoff)
        load = False
        for _ in range(count):
            entry = f.read(entsize)
            require(len(entry) == entsize, f'{path}: truncated ELF program header')
            typ = struct.unpack_from('<I', entry)[0]
            require(typ != 3, f'{path}: dynamic interpreter is not permitted')
            if typ == 1 and struct.unpack_from('<I', entry, 4)[0] & 1:
                load = True
            if typ == 2:
                # Static PIE may have PT_DYNAMIC for relocations, but not DT_NEEDED.
                at, _, _, size = struct.unpack_from('<QQQQ', entry, 8)
                resume = f.tell()
                require(size <= 4 * MIB and size % 16 == 0, f'{path}: invalid PT_DYNAMIC')
                f.seek(at)
                tags = f.read(size)
                require(len(tags) == size and all(tag != 1 for tag, _ in struct.iter_unpack('<qQ', tags)), f'{path}: not statically linked')
                f.seek(resume)
        require(load, f'{path}: no executable PT_LOAD')


def disk_fixture(args, out, *, package=None):
    rootfs = regular(args.rootfs).resolve()
    root_bytes = rootfs.stat().st_size
    require(root_bytes > BLOCK and root_bytes % BLOCK == 0, 'rootfs must be an aligned ext4 image')
    with rootfs.open('rb') as f:
        f.seek(1024 + 56)
        require(f.read(2) == b'\x53\xef', 'rootfs has no ext4 superblock')
    root_sha = file_sha(rootfs)
    dada = package is not None and package['device'] == 'dada'
    parts = dada_partitions() if dada else stock_partitions()
    by_name = {p['name']: p for p in parts}
    for p in parts:
        p['size'] = max(BLOCK, align(div_up(p['stock_bytes'], args.scale_divisor), BLOCK))
    # Keep these large enough for the synthetic fixture's known structured contents.
    by_name['misc']['size'] = max(64 * 1024, by_name['misc']['size'])
    for name in ('metadata', 'persist'):
        by_name[name]['size'] = max(5 * MIB, by_name[name]['size'])
    root_at = MIB
    minimum = 0 if package is None else package['minimum']
    root_size = BLOCK + align(max(root_bytes + args.rootfs_growth_mib * MIB, minimum), BLOCK)
    boot_size, init_size = (by_name[n]['size'] for n in ('boot_a', 'init_boot_a'))
    store_at, store_size = align(root_at + root_size, MIB), BLOCK + 3 * (boot_size + init_size)
    by_name['userdata']['size'] = max(by_name['userdata']['size'], align(store_at + store_size + MIB, MIB))
    offset = MIB
    for p in parts:
        p['offset'] = offset
        offset = align(offset + p['size'], MIB)
    raw = out / 'disk.raw'
    with tempfile.TemporaryDirectory(dir=out) as temp, raw.open('x+b') as f:
        temp = Path(temp)
        disk_bytes = write_gpt(f, args.seed, parts)
        for p in parts:
            p['sentinel_sha256'] = sentinel(f, args.seed, p['name'], p['offset'], p['size'],
                (root_at, root_at + root_size) if p['name'] == 'userdata' else None)
        misc = temp / 'misc.bcb'
        misc.write_bytes(bytes(2048))
        copy_at(misc, f, by_name['misc']['offset'])
        hashes = {n: by_name[n]['sentinel_sha256'] for n in ('boot_a', 'init_boot_a', 'vendor_boot_a', 'dtbo_a')}
        # The boot store entry holds fixture boot_a/init_boot_a sentinels, not a recovery/boot image.
        rust('state', out, package['id'] if package else 'uwu-fuxi',
             identity(args.seed, 'root-area').hex, f'{root_at}:{root_size}',
             identity(args.seed, 'store-area').hex, f'{store_at}:{store_size}', boot_size, init_size,
             *hashes.values(), misc, *([package['name'], package['version']] if package else []))
        offset = by_name['userdata']['offset']
        copy_at(out / 'root.header', f, offset + root_at)
        copy_at(rootfs, f, offset + root_at + BLOCK, sparse=True)
        require(sum_range(f, offset + root_at + BLOCK, root_bytes) == root_sha, 'rootfs changed while copying')
        copy_at(out / 'store.header', f, offset + store_at)
        entry = offset + store_at + BLOCK + 2 * (boot_size + init_size)
        f.flush()
        copy_at(raw, f, entry, boot_size, by_name['boot_a']['offset'])
        copy_at(raw, f, entry + boot_size, init_size, by_name['init_boot_a']['offset'])
        for pos, size, name in ((entry, boot_size, 'boot_a'), (entry + boot_size, init_size, 'init_boot_a')):
            require(sum_range(f, pos, size) == hashes[name], f'invalid store entry: {name}')
        # Keep a real ext4 metadata image and persist source, with a protected 1 MiB sentinel tail each.
        for label in ('metadata', 'persist'):
            p = by_name[label]
            fs = temp / f'{label}.ext4'
            ext4(fs, p['size'] - MIB, args.seed, label, out / 'systems.toml' if label == 'metadata' else None)
            copy_at(fs, f, p['offset'])
        f.flush()
        for p in parts:
            p['sha256'] = sum_range(f, p['offset'], p['size'])
        with (out / 'partitions.tsv').open('w', newline='') as table:
            writer = csv.DictWriter(table, fieldnames=parts[0].keys(), delimiter='\t', lineterminator='\n')
            writer.writeheader()
            writer.writerows(parts)
        # Divide the whole image into disjoint ranges; compare all, but only the marked changes may pass.
        ranges = []
        at = 0
        for p in parts:
            lo, size, name = p['offset'], p['size'], p['name']
            if at < lo:
                ranges.append((f'gap@{at}', at, lo - at, 'protected'))
            if name == 'userdata':
                # Include reserved rootfs growth in the mutable area; the store and area headers remain protected.
                splits = [(0, root_at + BLOCK, 'protected'), (root_at + BLOCK, root_size - BLOCK, 'mutable'),
                          (root_at + root_size, size - root_at - root_size, 'protected')]
            elif name == 'misc' and package is not None:
                splits = [(0, 2048, 'mutable'), (2048, size - 2048, 'protected')]
            else:
                legacy_mutable = package is None and name in ('misc', 'modemst1', 'modemst2')
                splits = [(0, size, 'mutable' if legacy_mutable else 'protected')]
            for within, length, policy in splits:
                ranges.append((f'{name}@{within}', lo + within, length, policy))
            at = lo + size
        ranges.append((f'gap@{at}', at, disk_bytes - at, 'protected'))
        with (out / 'ranges.tsv').open('w') as table:
            table.write('range\toffset\tsize\tpolicy\tsha256\n')
            for name, off, length, policy in ranges:
                table.write(f'{name}\t{off}\t{length}\t{policy}\t{sum_range(f, off, length)}\n')
    tool = run('/usr/sbin/mke2fs', '-V', text=True, capture_output=True).stderr.splitlines()[0]
    provenance = dict(seed=args.seed, scale_divisor=args.scale_divisor, rootfs_growth_mib=args.rootfs_growth_mib,
                      gpt_sha256=None if dada else file_sha(P1_GPT / 'SHA256SUMS'),
                      rootfs_bytes=root_bytes, rootfs_sha256=root_sha, disk_bytes=disk_bytes, state_path='/metadata/skyward/systems.toml',
                      source=('synthetic Dada replay labels; 512-byte virtual sectors; not a factory GPT' if dada else
                              'P1.1 fuxi OS3.0.309.0.WMCCNXM primary GPT; 4096-byte LUNs flattened to 512-byte sectors'),
                      e2fsprogs=tool, epoch=int(EPOCH))
    if package is not None:
        provenance.update(device=package['device'], package_id=package['id'], package_minimum=minimum,
                          root_payload_bytes=root_size - BLOCK)
    (out / 'fixture.json').write_text(json.dumps(provenance, indent=2) + '\n')
    write_sums(out, ('disk.raw', 'fixture.json', 'partitions.tsv', 'ranges.tsv', 'root.header', 'store.header', 'start.toml', 'systems.toml'))


def verify_ranges(args):
    ok = True
    at = 0
    with regular(args.disk).open('rb') as f, regular(args.ranges).open() as tsv:
        print('range\tstatus\tpolicy\tsha256')
        for row in csv.DictReader(tsv, delimiter='\t'):
            offset, size = int(row['offset']), int(row['size'])
            require(offset == at and size > 0, 'range manifest does not cover disk exactly once')
            at += size
            sha = sum_range(f, offset, size)
            changed = sha != row['sha256']
            allowed = row['policy'] == 'mutable' and args.allow_mutable
            ok &= not changed or allowed
            print(f"{row['range']}\t{'CHANGED' if changed else 'OK'}\t{row['policy']}\t{sha}")
        require(at == os.fstat(f.fileno()).st_size, 'disk size differs from range manifest')
    return 0 if ok else 1


def newc(entries):
    """The minimal newc format used for the simulated vendor ramdisk."""
    out = bytearray()
    for ino, (name, mode, data) in enumerate([*entries, ('TRAILER!!!', 0, b'')], 1):
        name = name.encode() + b'\0'
        fields = (ino, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0, len(data), 0, 0, 0, 0, len(name), 0)
        out.extend(b'070701' + ''.join(f'{v:08x}' for v in fields).encode() + name)
        out.extend(bytes(-len(out) % 4))
        out.extend(data)
        out.extend(bytes(-len(out) % 4))
    return out


def image_config(image, release, wanted):
    require(image[56:60] == b'ARMd' and re.search(rb'Linux version ' + re.escape(release.encode()) + rb' ', image),
            'wrong AArch64 Image or release')
    i = image.find(b'IKCFG_ST')
    require(i >= 0, 'Image has no embedded IKCONFIG')
    try:
        config = set(zlib.decompress(image[i + 8:], wbits=31).decode().splitlines())
    except zlib.error as e:
        raise ValueError('invalid embedded IKCONFIG') from e
    missing = [key for key in wanted if f'CONFIG_{key}=y' not in config]
    require(not missing, 'missing built-in kernel capabilities: ' + ', '.join(missing))
    return config


def pinned_gki(entries):
    files = {'Image', 'kernel-5.15-gz', 'prebuilt-info.txt', *MODULES}
    manifest = PREBUILT / 'SHA256SUMS'
    require(file_sha(manifest) == GKI_SUMS_SHA256, 'wrong pinned GKI checksum manifest')
    require(check_inputs(PREBUILT, manifest) == files, 'unexpected kernel checksum manifest')
    image = (PREBUILT / 'Image').read_bytes()
    config = image_config(image, RELEASE, (*GUEST_BUILTINS, 'MODVERSIONS'))
    with gzip.open(PREBUILT / 'kernel-5.15-gz', 'rb') as gz:
        require(hashlib.file_digest(gz, 'sha256').hexdigest() == hashlib.sha256(image).hexdigest(), 'Image does not match pinned gzip')
    require(json.loads((PREBUILT / 'prebuilt-info.txt').read_text())['kernel-build-id'] == 9178016, 'unexpected GKI CI build')
    symvers = PREBUILT.parent / 'vmlinux.symvers'
    require(file_sha(symvers) == SYMVER_SHA256, 'wrong pinned GKI vmlinux.symvers')
    versions = {}
    for line in symvers.read_text().splitlines():
        crc, symbol, owner, *_ = line.split()
        if owner == 'vmlinux':
            versions[symbol] = int(crc, 16)
    printout = ['profile=pinned-gki', f'release={RELEASE}', f'vmlinux.symvers_sha256={SYMVER_SHA256}',
                *[f'CONFIG_{key}=y' for key in (*GUEST_BUILTINS, 'MODVERSIONS')]]
    for key in ('SCSI', 'SCSI_VIRTIO', 'BLK_DEV_SR', 'BLK_DEV_BSG'):
        printout.append(next((line for line in sorted(config) if line.startswith((f'CONFIG_{key}=', f'# CONFIG_{key} '))),
                             f'CONFIG_{key} absent'))
    aliases = []
    for name in MODULES:
        path = PREBUILT / name
        data = path.read_bytes()
        require(data[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<HH', data, 16) == (1, 183), f'{name}: not AArch64 relocatable ELF')
        def mod(field):
            return run('modinfo', '-F', field, path, text=True, capture_output=True).stdout.strip()
        vermagic = mod('vermagic')
        require(vermagic.startswith(RELEASE + ' ') and vermagic.endswith(' aarch64'), f'{name}: vermagic mismatch')
        require(not mod('depends') and not mod('softdep'), f'{name}: module dependency closure changed')
        imports = run('modprobe', '--show-modversions', path, text=True, capture_output=True).stdout.splitlines()
        require(imports, f'{name}: missing module symbol versions')
        for row in imports:
            crc, symbol = row.split()
            require(versions.get(symbol) == int(crc, 16), f'{name}: missing or CRC-mismatched kernel symbol {symbol}')
        entries.append((f'lib/modules/{name}', 0o100644, data))
        aliases.extend(f'alias {alias} {Path(name).stem}\n' for alias in mod('alias').splitlines())
        printout.append(f'{name}\t{vermagic}\tdepends=(none)\tverified_symbol_crcs={len(imports)}')
    files = {'modules.load': ''.join(f'{m}\n' for m in MODULES),
             'modules.dep': ''.join(f'{m}:\n' for m in MODULES),
             'modules.alias': ''.join(aliases), 'modules.softdep': ''}
    entries.extend((f'lib/modules/{name}', 0o100644, value.encode()) for name, value in files.items())
    return image, printout


def kernel_inputs(out, scenario_image):
    entries = [('lib', 0o40755, b''), ('lib/modules', 0o40755, b'')]
    if scenario_image is None:
        image, printout = pinned_gki(entries)
    else:
        image = regular(scenario_image).read_bytes()
        wanted = (*GUEST_BUILTINS, *SCENARIO_BUILTINS)
        image_config(image, SCENARIO_RELEASE, wanted)
        printout = ['profile=scenario-test-only', f'release={SCENARIO_RELEASE}',
                    f'image_sha256={hashlib.sha256(image).hexdigest()}',
                    *[f'CONFIG_{key}=y' for key in wanted]]
        # Do not load any modules from the pinned GKI with this different Image.
        entries.extend((f'lib/modules/{name}', 0o100644, b'')
                       for name in ('modules.load', 'modules.dep', 'modules.alias', 'modules.softdep'))
    (out / 'vendor-ramdisk.cpio').write_bytes(newc(entries))
    (out / 'Image').write_bytes(image)
    (out / 'kernel-checks.txt').write_text('\n'.join(printout) + '\n')
    # Inputs for a virtio-mmio launcher, not the phone's vendor bootconfig.
    (out / 'cmdline.txt').write_text('console=ttyAMA0 earlycon=pl011,0x09000000 loglevel=7 androidboot.hardware=fuxi androidboot.slot_suffix=_a androidboot.init_fatal_reboot_target=recovery\n')
    return ['Image', 'vendor-ramdisk.cpio', 'kernel-checks.txt', 'cmdline.txt']


def kernel_fixture(args, out):
    files = kernel_inputs(out, args.scenario_image)
    if args.start:
        start, init_boot = regular(args.start), regular(args.init_boot)
        rust('ramdisk', start, init_boot, out)
        static_arm64(out / 'init.elf')
        vendor = (out / 'vendor-ramdisk.cpio').read_bytes()
        skyward = (out / 'skyward-ramdisk.lz4').read_bytes()
        (out / 'initramfs.img').write_bytes(vendor + skyward)
        (out / 'boot-inputs.json').write_text(json.dumps({
            'start_sha256': file_sha(start), 'init_boot_sha256': file_sha(init_boot),
        }, indent=2) + '\n')
        files.extend(('init.elf', 'skyward-ramdisk.lz4', 'initramfs.img', 'boot-inputs.json'))
    write_sums(out, files)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(required=True, dest='command')
    disk = sub.add_parser('disk', help='prepare a flat regular-file GPT twin, no emulator')
    disk.add_argument('--out', required=True, type=Path)
    disk.add_argument('--rootfs', required=True, type=Path)
    disk.add_argument('--seed', default=SEED)
    disk.add_argument('--scale-divisor', type=int, default=DEFAULT_DIVISOR)
    disk.add_argument('--rootfs-growth-mib', type=int, default=0)
    kernel = sub.add_parser('kernel', help='prepare matched GKI/modules; optionally consume P7.1 init_boot')
    kernel.add_argument('--out', required=True, type=Path)
    kernel.add_argument('--start', type=Path)
    kernel.add_argument('--init-boot', type=Path)
    kernel.add_argument('--scenario-image', type=Path, help='use the same-source, built-in-driver test Image from the P6.2 recipe')
    compare = sub.add_parser('verify', help='compare all disk byte ranges against pristine fixture')
    compare.add_argument('--disk', type=Path, required=True)
    compare.add_argument('--ranges', type=Path, required=True)
    compare.add_argument('--allow-mutable', action='store_true')
    args = cli.parse_args()
    if args.command == 'verify':
        return verify_ranges(args)
    if args.command == 'disk':
        require(args.seed and '\0' not in args.seed and args.scale_divisor > 1 and args.rootfs_growth_mib >= 0, 'invalid seed, scale or growth')
    else:
        require(bool(args.start) == bool(args.init_boot), '--start and --init-boot go together')
    out = args.out.absolute()
    require(not os.path.lexists(out), f'output must be a new directory: {out}')
    with tempfile.TemporaryDirectory(prefix='.fuxi-twin-', dir=out.parent) as tmp:
        stage = Path(tmp) / 'fixture'
        stage.mkdir()
        (disk_fixture if args.command == 'disk' else kernel_fixture)(args, stage)
        stage.rename(out)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        sys.exit(f'fuxi-twin: {e}')
