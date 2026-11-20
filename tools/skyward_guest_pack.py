#!/usr/bin/env python3
"""Assemble and verify a P5.4 package without mounting or modifying any input."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid
import zipfile


HERE = Path(__file__).resolve().parent
SHIM = HERE.parent
ROOT = Path(os.environ.get('SKYWARD_EVIDENCE_ROOT', SHIM)).resolve()
ROM = ('system.img', 'system_ext.img', 'product.img', 'vendor.img', 'odm.img')
DLKM = ('system_dlkm.img', 'vendor_dlkm.img')
BINDINGS = ('boot.img', 'vendor_boot.img', 'dtbo.img')
PORT_FULL = ('init_boot', 'vendor_boot', 'dtbo')
PORT_KEYS = {'slot', 'boot_bytes', 'boot_prefixes',
             *(f'{part}_{suffix}' for part in PORT_FULL for suffix in ('bytes', 'sha256'))}
IMAGES = ROM + DLKM
DEVICES = ('fuxi', 'dada')
CANDIDATE = ('boot.img', *IMAGES)
TOOLS = ('resize2fs', 'e2fsck', 'mke2fs')
DIRS = ('/', '/bin', '/dev', '/proc', '/sys', '/images', '/volumes')
FILES = {'/guest-entry': 0o755, '/skyward-guest.toml': 0o644,
         **{f'/bin/{name}': 0o755 for name in TOOLS},
         **{f'/images/{name}': 0o444 for name in (*IMAGES, 'SHA256SUMS')}}
STAMP = 1700000000
MIB = 1024**2
GIB = 1024**3
CHUNK = 4 * MIB
SHA = r'[0-9a-fA-F]{64}'
FS_CONFIG = '''[defaults]
    base_features = sparse_super,large_file,filetype,resize_inode,dir_index,ext_attr
    default_mntopts = acl,user_xattr
    enable_periodic_fsck = 0
[fs_types]
    ext4 = {
        features = has_journal,extent,huge_file,flex_bg,metadata_csum,64bit,dir_nlink,extra_isize
    }
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)



def input_path(variable):
    value = os.environ.get(variable)
    require(value, f'set {variable} to its approved input')
    return Path(value).resolve(strict=True)


def helper_dir():
    return input_path('SKYWARD_STATIC_E2FS_DIR')

def check_release_hold():
    hold = SHIM / 'legacy/policy/selinux-rollback/RELEASE-HOLD'
    require(not hold.exists(), f'guest publication suspended after SELinux rollback: {hold}')


def digest(value, description):
    require(isinstance(value, str) and re.fullmatch(SHA, value),
            f'{description}: expected a SHA-256 hex digest')
    return value.lower()


def regular(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_size > 0,
            f'{path}: expected a nonempty regular file, not a symlink')
    return s.st_size


def checksum_list(path, names, *, exact=False):
    regular(path)
    text = path.read_text(encoding='utf-8')
    # sha256sum text/binary form; retain only named inputs, and never open a
    # manifest-supplied path. Accept relative or absolute paths from the producer.
    result = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = re.fullmatch(rf'({SHA}) [ *](.+)', line)
        require(match, f'{path}:{number}: invalid sha256sum record')
        name = Path(match[2]).name
        require(not exact or match[2] == name and name in names,
                f'{path}:{number}: unexpected record {match[2]}')
        if name in names:
            require(name not in result, f'{path}: duplicate {name}')
            result[name] = match[1].lower()
    require(set(result) == set(names), f'{path}: missing {sorted(set(names) - result.keys())}')
    return result


def stage(source, target, expected):
    size = regular(source)
    # Reflink on the project filesystem when possible; never stage a hard link
    # to mutable input. Verify the private copy, which is what goes into ext4/ZIP.
    subprocess.run(['cp', '--reflink=auto', '--no-preserve=all', '--', source, target], check=True)
    require(regular(target) == size and file_hash(target) == expected,
            f'{source}: size or SHA-256 mismatch')
    return size


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def selected_bootset(device, record, expected):
    pin = record / 'bootset.sha256'
    if not os.path.lexists(pin):
        return {}, None
    base = ROOT / 'evidence/P4.6/bootsets'
    handoff = base / device / 'handoff.json'
    regular(handoff)
    require(file_hash(handoff) == checksum_list(pin, ('handoff.json',))['handoff.json'],
            'bootset handoff does not match the same-lock build record')
    data = json.loads(handoff.read_text(encoding='utf-8'))
    require(isinstance(data, dict) and data.get('schema') == 'bootset-offline-handoff/v1'
            and data.get('path_base') == 'evidence/P4.6/bootsets'
            and data.get('device') == device, 'wrong bootset schema or device')

    def referenced(entry, label):
        require(isinstance(entry, dict) and set(entry) == {'path', 'bytes', 'sha256'}
                and isinstance(entry['path'], str), f'invalid bootset {label} record')
        path = base / entry['path']
        require(path.is_relative_to(base / device) and path.resolve() == path
                and type(entry['bytes']) is int and regular(path) == entry['bytes']
                and file_hash(path) == digest(entry['sha256'], label),
                f'bootset {label}: invalid path, length or SHA-256')
        return path

    inputs = data.get('packaging_inputs')
    require(isinstance(inputs, dict) and set(inputs) == {'boot.img', *DLKM},
            'bootset must supply exactly one boot and two DLKM images')
    paths = {}
    for name, entry in inputs.items():
        paths[name] = referenced(entry, name)
        require(paths[name].name == name and entry['sha256'].lower() == expected[name],
                f'{name}: approved candidate digest differs from selected bootset')
    meta = data.get('metadata')
    require(isinstance(meta, dict), 'missing bootset kernel metadata')
    release = referenced(meta.get('release'), 'release')
    config = referenced(meta.get('ikconfig'), 'ikconfig')
    require(release.read_text().strip() == data.get('kernel_release'), 'bootset kernel release mismatch')
    return paths, (handoff, release, config)


def candidate_build(device, lock, record):
    src = input_path('SKYWARD_ANDROID_SRC')
    locked = checksum_list(record / 'lock.sha256', ('manifest.xml', 'SHA256SUMS'))
    for name, expected in locked.items():
        path = lock / name
        regular(path)
        require(file_hash(path) == expected, f'build record lock mismatch: {name}')
    subprocess.run(['sha256sum', '--check', '--quiet', 'SHA256SUMS'], cwd=lock, check=True)
    require(not os.path.lexists(record / 'REJECTED.txt'), 'candidate build handoff is rejected')
    regular(record / 'OUT_DIR')
    name = (record / 'OUT_DIR').read_text().strip()
    require(re.fullmatch(r'out-[A-Za-z0-9._-]+', name), 'expected a relative out-* directory')
    out = src / name
    prod = out / 'target/product' / device
    require(prod.is_dir() and prod.resolve() == prod, f'{prod}: missing or aliased PRODUCT_OUT')
    sums = ROOT / f'evidence/P4.6/{device}.sha256'
    expected = checksum_list(sums, CANDIDATE, exact=True)
    require(expected == checksum_list(record / 'images.sha256', CANDIDATE, exact=True),
            'P4.6 candidate manifest differs from selected build record')
    prebuilt, kernel = selected_bootset(device, record, expected)
    sources = {name: prebuilt.get(name, prod / name) for name in CANDIDATE}
    sizes = {name: regular(path) for name, path in sources.items()}
    return out, prod, expected, sources, sizes, kernel


def candidate_image(source, target, expected, out):
    # Normalize only Android sparse filesystem images, with the same OUT's tool.
    # The evidence digests always cover the actual raw bytes packed for guest-entry.
    with source.open('rb') as stream:
        sparse = stream.read(4) == b'\x3a\xff\x26\xed'
    if sparse:
        subprocess.run([out / 'host/linux-x86/bin/simg2img', source, target], check=True)
        size = regular(target)
        require(file_hash(target) == expected, f'{source}: converted candidate SHA-256 mismatch')
    else:
        size = stage(source, target, expected)
    raw_filesystem(target)
    return size


def host_credentials(device, manifest, raw_dir, candidate_sources, boot_size):
    regular(manifest)
    data = tomllib.loads(manifest.read_text(encoding='utf-8'))
    require(data.get('device') == device and isinstance(data.get('capture'), str)
            and data['capture'].strip(), f'{manifest}: expected device and independent capture reference')
    seen = {(s.st_dev, s.st_ino) for s in (path.stat() for path in candidate_sources.values())}

    def raw(name):
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]+\.img', name),
                f'{manifest}: expected an image basename')
        path = raw_dir / name
        size = regular(path)
        s = path.stat()
        require((s.st_dev, s.st_ino) not in seen, f'{path}: aliases a candidate image')
        return path, size

    def full(part, capacity, expected):
        path, size = raw(f'{part}.img')
        require(type(capacity) is int and capacity == size, f'{path}: wrong whole host partition size')
        expected = digest(expected, part)
        require(file_hash(path) == expected, f'{path}: host SHA-256 mismatch')
        return expected

    if 'host_port_v1' not in data:
        require(set(data) == {'device', 'slot', 'capture', 'partitions'} and data['slot'] in ('_a', '_b'),
                f'{manifest}: expected capture for the booted host slot')
        parts = data['partitions']
        require(isinstance(parts, dict) and set(parts) == {name[:-4] for name in BINDINGS},
                f'{manifest}: expected exactly the three host binding partitions')
        result = {}
        for name in BINDINGS:
            part = name[:-4]
            meta = parts[part]
            require(isinstance(meta, dict) and set(meta) == {'bytes', 'sha256'},
                    f'{manifest}: {name} needs independently recorded bytes and sha256')
            result[f'host_{part}_sha256'] = full(part, meta['bytes'], meta['sha256'])
        require(boot_size <= parts['boot']['bytes'], 'candidate boot exceeds host boot partition capacity')
        return result

    port = data['host_port_v1']
    require(set(data) == {'device', 'capture', 'host_port_v1'} and isinstance(port, dict)
            and set(port) == PORT_KEYS and port['slot'] in ('_a', '_b'),
            f'{manifest}: invalid or mixed host_port_v1 capture')
    capacities = [port['boot_bytes'], *(port[f'{part}_bytes'] for part in PORT_FULL)]
    require(all(type(n) is int and n > 0 and n % 512 == 0 for n in capacities)
            and 3 * (capacities[0] + capacities[1]) + 4096 < 2**63,
            f'{manifest}: invalid host_port_v1 partition capacities')
    require(boot_size <= port['boot_bytes'], 'candidate boot exceeds host boot partition capacity')
    prefixes = port['boot_prefixes']
    require(isinstance(prefixes, list) and 1 <= len(prefixes) <= 32,
            f'{manifest}: missing boot prefix evidence')
    verified = set()
    for prefix in prefixes:
        require(isinstance(prefix, dict) and set(prefix) in ({'bytes', 'sha256'}, {'bytes', 'sha256', 'file'})
                and type(prefix['bytes']) is int, f'{manifest}: invalid boot prefix record')
        path, size = raw(prefix.get('file', 'boot.img'))
        length = prefix['bytes']
        expected = digest(prefix['sha256'], 'boot prefix')
        require(0 < length <= size <= port['boot_bytes'] and (length, expected) not in verified,
                f'{manifest}: invalid or duplicate boot prefix')
        with path.open('rb') as stream:
            hasher = hashlib.sha256()
            remaining = length
            while remaining:
                block = stream.read(min(remaining, CHUNK))
                require(block, f'{path}: truncated host boot sample')
                hasher.update(block)
                remaining -= len(block)
        require(hasher.hexdigest() == expected, f'{path}: host boot prefix SHA-256 mismatch')
        verified.add((length, expected))
    port['boot_prefixes'] = [{'bytes': length, 'sha256': expected} for length, expected in sorted(verified)]
    for part in PORT_FULL:
        port[f'{part}_sha256'] = full(part, port[f'{part}_bytes'], port[f'{part}_sha256'])
    return {'host_port_v1': port}


def add_handoff_args(p):
    p.add_argument('--lock', type=Path, required=True, help='approved final frozen source lock')
    p.add_argument('--build-record', type=Path, required=True, help='approved same-OUT candidate build handoff')
    p.add_argument('--host-credentials', type=Path, required=True, help='independent host capture TOML')
    p.add_argument('--host-raw-dir', type=Path, required=True,
                   help='independent raw captures for the regions specified in host credentials')


def candidate_boot(path):
    with path.open('rb') as stream:
        require(stream.read(8) == b'ANDROID!', f'{path}: expected an Android boot image')


def static_aarch64(path):
    info = subprocess.check_output(['readelf', '-W', '-h', '-l', '-d', path], text=True)
    require(re.search(r'^\s*Class:\s+ELF64\s*$', info, re.M)
            and re.search(r'^\s*Data:.*little endian\s*$', info, re.M)
            and re.search(r'^\s*Machine:\s+AArch64\s*$', info, re.M)
            and re.search(r'^\s*Type:\s+(?:EXEC|DYN)\b', info, re.M)
            and re.search(r'^\s*LOAD\s+.*\bR\s+E\b', info, re.M)
            and not re.search(r'^\s*INTERP\s|\(NEEDED\)', info, re.M),
            f'{path.name}: expected a statically linked AArch64 executable')


def raw_filesystem(path):
    with path.open('rb') as stream:
        stream.seek(1024)
        block = stream.read(58)
    require(block[:4] == b'\xe2\xe1\xf5\xe0' or block[56:58] == b'\x53\xef',
            f'{path.name}: expected raw ext4 or EROFS (no sparse/DSU conversion)')


def guest_config(path, device, boot_sha, reserve):
    config = tomllib.loads(path.read_text(encoding='utf-8'))
    require(set(config) == {'device', 'host_baseline_sha256', 'writable_partitions', 'volumes'},
            'skyward-guest.toml: unexpected P5.1 schema')
    baseline = digest(config['host_baseline_sha256'], 'host_baseline_sha256')
    require(config['device'] == device and (boot_sha is None or baseline == boot_sha),
            'skyward-guest.toml: wrong device or independently captured host boot binding')
    writable = config['writable_partitions']
    require(isinstance(writable, list) and all(isinstance(n, str) for n in writable)
            and len(writable) == len(set(writable))
            and {'userdata', 'misc'} <= set(writable) <= {'userdata', 'misc', 'modemst1', 'modemst2'},
            'skyward-guest.toml: invalid writable_partitions')
    volumes = config['volumes']
    require(isinstance(volumes, dict) and set(volumes) == {'metadata_bytes', 'userdata_bytes'},
            'skyward-guest.toml: invalid volumes table')
    for key, minimum in [('metadata_bytes', 16 * MIB), ('userdata_bytes', 4 * GIB)]:
        size = volumes[key]
        require(type(size) is int and minimum <= size <= reserve and size % 4096 == 0,
                f'skyward-guest.toml: invalid {key}')
    require(sum(volumes.values()) < reserve,
            'skyward-guest.toml: volume sizes leave no space for persist/state/logs')


def system_manifest(device, rev, minimum, host):
    values = {'id': f'uwu-{device}', 'name': 'uwuAOSP 17', 'version': f'17.0.182-{rev}',
              'filesystem': 'ext4', 'init': '/guest-entry', 'device': device}
    lines = [f'{key} = "{value}"\n' for key, value in values.items()]
    lines += [f'minimum = {minimum}\n']
    if 'host_port_v1' not in host:
        lines += [f'{field} = "{value}"\n' for field, value in host.items()]
    else:
        port = host['host_port_v1']
        lines += ['\n[host_port_v1]\n', f'slot = "{port["slot"]}"\n', f'boot_bytes = {port["boot_bytes"]}\n']
        for part in PORT_FULL:
            lines += [f'{part}_bytes = {port[f"{part}_bytes"]}\n',
                      f'{part}_sha256 = "{port[f"{part}_sha256"]}"\n']
        for prefix in port['boot_prefixes']:
            lines += ['\n[[host_port_v1.boot_prefixes]]\n', f'bytes = {prefix["bytes"]}\n',
                      f'sha256 = "{prefix["sha256"]}"\n']
    return ''.join(lines).encode('ascii')


def debugfs(tool, work, *args):
    result = subprocess.run([tool, *args, 'rootfs.img'], cwd=work, check=True,
                            capture_output=True, text=True)
    # debugfs can return zero for a command-level failure. Ignore only its banner.
    errors = [line for line in result.stderr.splitlines()
              if not re.fullmatch(r'debugfs \d+\.\d+(?:\.\d+)? \([^)]*\)', line)]
    require(not errors, '\n'.join(errors))
    return result.stdout


def verify_root_file(tool, work, path, expected, size):
    # Use a single reusable on-disk dump, so no debugfs stdout text or sparse
    # readback can be mistaken for member bytes.
    copy = work / 'readback'
    debugfs(tool, work, '-R', f'dump {path} readback')
    require(regular(copy) == size and file_hash(copy) == expected, f'rootfs readback mismatch: {path}')
    copy.unlink()


def make_rootfs(work, root_size, fsid, inputs, host_tools):
    mke2fs, e2fsck, dbg = host_tools
    conf = work / 'mke2fs.conf'
    conf.write_text(FS_CONFIG, encoding='ascii')
    image = work / 'rootfs.img'
    with image.open('xb') as stream:
        stream.truncate(root_size)
    env = os.environ | {'MKE2FS_CONFIG': str(conf), 'E2FSPROGS_FAKE_TIME': str(STAMP)}
    subprocess.run([mke2fs, '-q', '-t', 'ext4', '-F', '-b', '4096', '-I', '256', '-N', '256',
                    '-m', '0', '-J', 'size=32', '-L', 'skyward-guest', '-U', fsid, '-E',
                    f'root_owner=0:0,hash_seed={fsid},lazy_itable_init=0,lazy_journal_init=0,nodiscard',
                    image], check=True, env=env)
    commands = ['rmdir /lost+found', *(f'mkdir {path}' for path in DIRS[1:])]
    commands += [f'write payload{path} {path}' for path in FILES]
    for path in (*DIRS, *FILES):
        mode = 0o40755 if path in DIRS else 0o100000 | FILES[path]
        commands += [f'set_inode_field {path} {key} {value}' for key, value in
                     [('uid', '0'), ('gid', '0'), ('mode', f'0{mode:o}'),
                      *[(t, f'@{STAMP}') for t in ('atime', 'ctime', 'mtime', 'crtime')]]]
    (work / 'populate').write_text(''.join(f'{cmd}\n' for cmd in commands), encoding='ascii')
    # Import bytes only. The outer FS starts unlabeled; Android restorecon
    # applies guest policy after handoff. Nested image bytes/xattrs are intact.
    debugfs(dbg, work, '-w', '-f', 'populate')
    subprocess.run([e2fsck, '-fn', image], check=True)
    for path in (*DIRS, *FILES):
        info = debugfs(dbg, work, '-R', f'stat {path}')
        match = re.search(r'Type: (\S+)\s+Mode:\s+0*([0-7]+)', info)
        require(match and match[1] == ('directory' if path in DIRS else 'regular')
                and int(match[2], 8) == FILES.get(path, 0o755)
                and re.search(r'User:\s+0\s+Group:\s+0\b', info), f'rootfs inode mismatch: {path}')
        require('security.selinux' not in info, f'unexpected SELinux context: {path}')
    for path, (expected, size) in inputs.items():
        verify_root_file(dbg, work, path, expected, size)
    listing = debugfs(dbg, work, '-R', 'ls -p /volumes')
    names = [line.split('/')[5] for line in listing.splitlines() if line.startswith('/')]
    require(names == ['.', '..'], 'rootfs /volumes is not empty')
    return image


def make_zip(work, members, manifest, boot_sha):
    package = work / 'package.zip'
    with zipfile.ZipFile(package, 'x', allowZip64=True) as z:
        for name, path in members.items():
            info = zipfile.ZipInfo(name, (2023, 11, 14, 22, 13, 20))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.file_size = path.stat().st_size
            info.compress_type = zipfile.ZIP_STORED
            # BOOT and manifest are small; allow the root image to cross 4 GiB.
            with path.open('rb') as src, z.open(info, 'w', force_zip64=(name == 'rootfs.img')) as dst:
                shutil.copyfileobj(src, dst, CHUNK)
    expected = {'system.toml': hashlib.sha256(manifest).hexdigest(), 'boot.img': boot_sha,
                'rootfs.img': file_hash(members['rootfs.img'])}
    with zipfile.ZipFile(package) as z:
        require(z.namelist() == list(members), 'ZIP member set mismatch')
        for name, path in members.items():
            require(z.getinfo(name).file_size == path.stat().st_size, f'ZIP size mismatch: {name}')
            with z.open(name) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected[name],
                        f'ZIP readback mismatch: {name}')
    return package


def verify_package(package, device, revision, minimum, boot_size, root_size, manifest_path):
    checker = input_path('SKYWARD_PACKAGE_CHECK')
    regular(checker)
    subprocess.run([checker, package, device, f'17.0.182-{revision}', str(minimum),
                    str(boot_size), str(root_size), manifest_path], check=True)


def pack(args):
    check_release_hold()
    require(re.fullmatch(r'[a-zA-Z0-9._-]{1,23}', args.revision),
            'revision must fit the SkyWard version field')
    out_dir, _, rom, candidate_sources, sizes, _ = candidate_build(args.device, args.lock, args.build_record)
    host = host_credentials(args.device, args.host_credentials, args.host_raw_dir,
                            candidate_sources, sizes['boot.img'])
    guest = checksum_list(ROOT / f'evidence/P5.1/{args.device}/SHA256SUMS', ('guest-entry',))
    helpers = helper_dir()
    tools = checksum_list(helpers / 'SHA256SUMS', TOOLS)
    out = args.output.absolute()
    require(not os.path.lexists(out), f'{out}: refuses to replace an existing path')
    require(out.parent.is_dir(), f'{out.parent}: output parent must exist')
    host_tools = tuple((args.host_e2fs_dir / name).resolve() for name in ('mke2fs', 'e2fsck', 'debugfs'))
    for tool in host_tools:
        require(tool.is_file() and os.access(tool, os.X_OK), f'missing host tool: {tool}')
    for command in ('cp', 'readelf'):
        require(shutil.which(command), f'missing host command: {command}')
    input_hashes = tools | guest
    sources = {'/guest-entry': args.guest_entry,
               **{f'/bin/{name}': helpers / 'bin' / name for name in TOOLS}}
    regular(args.guest_config)
    with tempfile.TemporaryDirectory(prefix='.skyward-pack-', dir=out.parent) as tmp:
        work = Path(tmp)
        payload = work / 'payload'
        for directory in DIRS:
            (payload / directory.lstrip('/')).mkdir()
        boot = work / 'boot.img'
        boot_size = stage(candidate_sources['boot.img'], boot, rom['boot.img'])
        candidate_boot(boot)
        checks = {}
        for path, source in sources.items():
            name = path.rsplit('/', 1)[-1]
            dest = payload / path.lstrip('/')
            expected = input_hashes[name]
            checks[path] = (expected, stage(source, dest, expected))
            static_aarch64(dest)
        config = payload / 'skyward-guest.toml'
        shutil.copyfile(args.guest_config, config)
        checks['/skyward-guest.toml'] = (file_hash(config), regular(config))
        for name in IMAGES:
            path = f'/images/{name}'
            checks[path] = (rom[name], candidate_image(candidate_sources[name], payload / path[1:], rom[name], out_dir))
        sums = ''.join(f'{rom[name]}  {name}\n' for name in IMAGES).encode('ascii')
        (payload / 'images/SHA256SUMS').write_bytes(sums)
        checks['/images/SHA256SUMS'] = (hashlib.sha256(sums).hexdigest(), len(sums))
        image_bytes = sum(checks[f'/images/{name}'][1] for name in IMAGES)
        minimum = image_bytes + 8 * GIB
        content = sum(size for _, size in checks.values())
        root_size = (content + 128 * MIB + content // 100 + 4095) // 4096 * 4096
        require(root_size < minimum < 2**63, 'rootfs size exceeds manifest allocation')
        guest_config(payload / 'skyward-guest.toml', args.device, host.get('host_boot_sha256'), minimum - root_size)
        manifest = system_manifest(args.device, args.revision, minimum, host)
        manifest_path = work / 'system.toml'
        manifest_path.write_bytes(manifest)
        identity = hashlib.sha256(manifest + bytes.fromhex(rom['boot.img']))
        for path in sorted(checks):
            identity.update(bytes.fromhex(checks[path][0]))
        fsid = str(uuid.uuid5(uuid.NAMESPACE_OID, identity.hexdigest()))
        image = make_rootfs(work, root_size, fsid, checks, host_tools)
        package = make_zip(work, {'system.toml': manifest_path, 'boot.img': boot, 'rootfs.img': image},
                           manifest, rom['boot.img'])
        verify_package(package, args.device, args.revision, minimum, boot_size, root_size, manifest_path)
        package_sha = file_hash(package)
        # Temporary bundle on the same filesystem; publish atomically, no clobber.
        os.link(package, out)
    print(f'{package_sha}  {out}')


def main():
    p = argparse.ArgumentParser(description=__doc__, epilog=(
        'Requires an accepted P4.6 boot + seven-image candidate and independent '
        'whole-host-partition credentials. No stock or donor fallback.'))
    p.add_argument('device', choices=DEVICES)
    p.add_argument('--revision', required=True, help='accepted ROM revision for system.toml')
    add_handoff_args(p)
    p.add_argument('--guest-entry', type=Path, required=True, help='P5.1 static executable')
    p.add_argument('--guest-config', type=Path, required=True, help='P5.1 schema with this host boot binding')
    p.add_argument('--host-e2fs-dir', type=Path, default=Path('/usr/sbin'),
                   help='host e2fsprogs directory (default /usr/sbin, not Android SDK)')
    p.add_argument('--output', type=Path, required=True, help='new ZIP path in an existing directory')
    args = p.parse_args()
    os.environ.update(LC_ALL='C', TZ='UTC', E2FSPROGS_FAKE_TIME=str(STAMP))
    try:
        pack(args)
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        p.exit(1, f'P5.4 refused: {error}\n')


if __name__ == '__main__':
    main()
