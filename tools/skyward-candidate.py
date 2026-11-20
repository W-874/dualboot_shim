#!/usr/bin/env python3
"""Pack and read back a strict host-A dual-device guest candidate."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import uuid
import zipfile

import skyward_guest_pack as core
from skyward_guest_pack import ROOT, ROM, DLKM, IMAGES, TOOLS, file_hash, regular, require

DEVICES = core.DEVICES
MEMBERS = ('boot.img', 'rootfs.img', 'system.toml')
GUEST_FILES = ('guest-entry', 'skyward-package-check')
ERE = b'\xe2\xe1\xf5\xe0'
TOOLS_DIR = Path(os.environ.get('SKYWARD_HOST_E2FS_DIR', '/usr/sbin'))


def image_out(device):
    out = core.input_path('SKYWARD_ANDROID_OUT')
    prod = out / 'target/product' / device
    require(prod.is_dir() and prod.resolve() == prod, f'wrong or aliased PRODUCT_OUT: {prod}')
    return out, prod


def target(device, rev):
    return ROOT / f'deliver/{device}/uwu-{device}-{rev}-candidate.zip'


def hashes(path, names):
    regular(path)
    found = {}
    for line in path.read_text(encoding='ascii').splitlines():
        match = re.fullmatch(rf'({core.SHA}) [ *](.+)', line)
        require(match and match[2] in names and match[2] not in found,
                f'{path}: invalid, unexpected or duplicate checksum record: {line}')
        found[match[2]] = match[1].lower()
    require(set(found) == set(names), f'{path}: missing {sorted(set(names) - found.keys())}')
    return found


def write_sums(path, values):
    with path.open('x', encoding='ascii') as fd:
        for name, digest in values.items():
            fd.write(f'{digest}  {name}\n')


def snapshot_guest(run):
    expected = hashes(run / 'guest.sha256', GUEST_FILES)
    for name, digest in expected.items():
        require(regular(run / name) and file_hash(run / name) == digest, f'guest artifact drift: {name}')
    core.static_aarch64(run / 'guest-entry')
    return expected['guest-entry']


def bootset(device):
    base = ROOT / 'evidence/P4.6/bootsets' / device
    selected = json.loads((base / 'handoff.json').read_text(encoding='utf-8'))
    require(selected['schema'] == 'bootset-offline-handoff/v1' and selected['device'] == device,
            'wrong selected OEM bootset')
    members = ('boot.img', *DLKM)
    expected = core.checksum_list(base / 'packaging-inputs.sha256', members)
    entries = selected['packaging_inputs']
    require(set(entries) == set(members), 'wrong selected bootset members')
    paths = {}
    for name in members:
        path = base / 'oem/images' / name
        entry = entries[name]
        require(path.resolve() == path and entry['path'] == f'{device}/oem/images/{name}'
                and regular(path) == entry['bytes'] and expected[name] == entry['sha256'],
                f'bootset path/bytes/hash record mismatch: {name}')
        paths[name] = path
    return paths, expected


def legacy_host_a(device, boot_sha, boot_size):
    # Historical strict candidate credential, NOT a fresh raw capture. Do not
    # synthesize padded bytes or derive host credentials from the guest bootset.
    stamp = core.input_path('SKYWARD_HOST_A')
    pins = {}
    for line in stamp.read_text(encoding='ascii').splitlines():
        part, size, sha = line.split()
        require(part not in pins and int(size) > 0 and int(size) % 512 == 0, f'invalid host A row: {part}')
        pins[part] = (int(size), core.digest(sha, part))
    require(set(pins) == {'boot', 'init_boot', 'vendor_boot', 'dtbo'}, 'incomplete host A record')
    require(boot_size <= pins['boot'][0], 'candidate boot exceeds host A boot capacity')
    host = {f'host_{p}_sha256': pins[p][1] for p in ('boot', 'vendor_boot', 'dtbo')}
    evidence = core.input_path('SKYWARD_PREVIOUS_CANDIDATE')
    members = hashes(evidence / 'members.sha256', MEMBERS)
    manifest = (evidence / 'system.toml').read_bytes()
    meta = tomllib.loads(manifest.decode('ascii'))
    require(members['boot.img'] == boot_sha and members['system.toml'] == hashlib.sha256(manifest).hexdigest()
            and meta['device'] == device and meta['id'] == f'uwu-{device}'
            and {k: v for k, v in meta.items() if k.startswith('host_')} == host,
            'host A evidence differs from the last device candidate')
    return host


def policy_readback(work, images, out, evidence, prod=None):
    """Read the actual installed vendor/ODM policy, including init's mapping IDs."""
    policy_dir = work / 'policies'
    policy_dir.mkdir()
    trees = {}
    observed_hashes = {}

    def grab(part, rel):
        image = images / f'{part}.img'
        with image.open('rb') as fd:
            fd.seek(1024)
            is_erofs = fd.read(4) == ERE
        if is_erofs:
            if part not in trees:
                trees[part] = policy_dir / part
                trees[part].mkdir()
                subprocess.run([out / 'host/linux-x86/bin/fsck.erofs', '--no-preserve',
                                f'--extract={trees[part]}', image], check=True)
            source = trees[part] / rel
            regular(source)
            return source
        target = policy_dir / f'{part}-{Path(rel).name}'
        # Use short relative dump names (including when the workspace has spaces).
        result = subprocess.run([TOOLS_DIR / 'debugfs', '-R', f'dump /{rel} {target.name}', image],
                                cwd=policy_dir, check=True, capture_output=True, text=True)
        require(all(re.fullmatch(r'debugfs [\d.]+ \([^)]*\)', line)
                    for line in result.stderr.splitlines()), result.stderr)
        regular(target)
        return target

    def compare(part, rel, *, on_image=None):
        extracted = grab(part, on_image or rel)
        key = f'{part}/{rel}'
        observed_hashes[key] = file_hash(extracted)
        if prod is not None:
            staged = prod / key
            require(regular(staged) and file_hash(staged) == observed_hashes[key],
                    f'{key}: image does not match same OUT')
        print(f'{observed_hashes[key]}  {key}', flush=True)
        return extracted

    compare('vendor', 'etc/selinux/vendor_sepolicy.cil')
    compare('vendor', 'etc/selinux/vendor_file_contexts')
    compare('odm', 'etc/selinux/precompiled_sepolicy')
    if prod is not None:
        binaries = list((out / 'soong/.intermediates/system/sepolicy/precompiled_sepolicy')
                        .rglob('precompiled_sepolicy'))
        require(len(binaries) == 1 and regular(binaries[0])
                and file_hash(binaries[0]) == observed_hashes['odm/etc/selinux/precompiled_sepolicy'],
                'ODM-installed policy differs from the same-OUT Soong policy')
    for part, prefix in (('system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
        suffix = f'{prefix}_sepolicy_and_mapping.sha256'
        rel = f'etc/selinux/{suffix}'
        actual = compare(part, rel, on_image=f'system/{rel}' if part == 'system' else rel)
        compiled = compare('odm', f'etc/selinux/precompiled_sepolicy.{suffix}')
        a, b = (p.read_text(encoding='ascii').strip() for p in (actual, compiled))
        require(a == b == core.digest(a, f'{part} mappings'), f'{part}: init mapping ID mismatch')
    if prod is not None:
        write_sums(evidence, observed_hashes)
    else:
        require(hashes(evidence, tuple(observed_hashes)) == observed_hashes,
                'policy content differs from the candidate readback record')
    print('Policy readback complete; mapping IDs match', flush=True)


def read_zip(archive, expected, root=None):
    with zipfile.ZipFile(archive) as z:
        require(sorted(z.namelist()) == list(MEMBERS), 'ZIP must contain exactly boot.img, rootfs.img and system.toml')
        sizes = {}
        for name in MEMBERS:
            info = z.getinfo(name)
            require(info.compress_type == zipfile.ZIP_DEFLATED and info.file_size > 0,
                    f'{name}: expected nonempty deflated ZIP member')
            sizes[name] = info.file_size
            with z.open(info) as fd:
                if name == 'rootfs.img' and root is not None:
                    with root.open('xb') as dest:
                        shutil.copyfileobj(fd, dest, core.CHUNK)
                    digest = file_hash(root)
                else:
                    digest = hashlib.file_digest(fd, 'sha256').hexdigest()
            require(digest == expected[name], f'ZIP member SHA mismatch: {name}')
    return sizes


def check_parser(run, archive, device, rev, sizes, manifest):
    data = tomllib.loads(manifest.read_text(encoding='ascii'))
    subprocess.run([run / 'skyward-package-check', archive, device, f'17.0.182-{rev}',
                    str(data['minimum']), str(sizes['boot.img']), str(sizes['rootfs.img']), manifest], check=True)


def pack(device, rev, run, five, *, offline=False):
    if not offline:
        core.check_release_hold()
    guest_sha = snapshot_guest(run)
    out, prod = image_out(device)
    release = test_target(device, rev, run) if offline else target(device, rev)
    checksum = release.with_name(release.name + '.sha256')
    require(not any(os.path.lexists(p) for p in (release, checksum)), f'candidate already exists: {release}')
    boot_paths, selected = bootset(device)
    host = legacy_host_a(device, selected['boot.img'], regular(boot_paths['boot.img']))
    built = hashes(five, ROM)
    tools = core.checksum_list(core.helper_dir() / 'SHA256SUMS', TOOLS)
    evidence = run / device
    evidence.mkdir()
    write_sums(evidence / 'five-images.sha256', built)
    (evidence / 'OUT_DIR').write_text(f'{out}\n', encoding='ascii')
    shutil.copyfile(core.input_path('SKYWARD_HOST_A'), evidence / 'host-a.txt')
    with tempfile.TemporaryDirectory(prefix='.candidate-', dir=release.parent) as tmp:
        work = Path(tmp)
        payload = work / 'payload'
        for directory in core.DIRS:
            (payload / directory.lstrip('/')).mkdir()
        boot = work / 'boot.img'
        core.stage(boot_paths['boot.img'], boot, selected['boot.img'])
        core.candidate_boot(boot)
        manifest = evidence / 'system.toml'
        rom = {}
        checks = {}
        for name in IMAGES:
            source = boot_paths[name] if name in selected else prod / name
            location = f'/images/{name}'
            raw = payload / location[1:]
            expect = (selected | built)[name]
            if name in built:
                regular(source)
                with source.open('rb') as fd:
                    sparse = fd.read(4) == b'\x3a\xff\x26\xed'
                if sparse:
                    require(file_hash(source) == expect, f'{source}: not the just-built same-OUT image')
                    subprocess.run([out / 'host/linux-x86/bin/simg2img', source, raw], check=True)
                    expect = file_hash(raw)
                    size = regular(raw)
                    core.raw_filesystem(raw)
                else:
                    size = core.candidate_image(source, raw, expect, out)
            else:
                size = core.candidate_image(source, raw, expect, out)
            rom[name] = expect
            checks[location] = (expect, size)
        write_sums(payload / 'images/SHA256SUMS', rom)
        shutil.copyfile(payload / 'images/SHA256SUMS', evidence / 'images.sha256')
        policy_readback(work, payload / 'images', out, evidence / 'policy.sha256', prod)
        for name, source, digest in [('guest-entry', run / 'guest-entry', guest_sha),
                                     *((f'bin/{k}', core.helper_dir() / 'bin' / k, v) for k, v in tools.items())]:
            dst = payload / name
            checks['/' + name] = (digest, core.stage(source, dst, digest))
            core.static_aarch64(dst)
        cfg = payload / 'skyward-guest.toml'
        cfg.write_text(f'device="{device}"\nhost_baseline_sha256="{host["host_boot_sha256"]}"\n'
                       'writable_partitions=["misc","userdata"]\n[volumes]\n'
                       'metadata_bytes=16777216\nuserdata_bytes=4294967296\n', encoding='ascii')
        shutil.copyfile(cfg, evidence / 'skyward-guest.toml')
        for name in ('skyward-guest.toml', 'images/SHA256SUMS'):
            p = payload / name
            checks['/' + name] = (file_hash(p), regular(p))
        minimum = sum(checks[f'/images/{name}'][1] for name in IMAGES) + 8 * core.GIB
        content = sum(size for _, size in checks.values())
        root_size = (content + 128 * core.MIB + content // 100 + 4095) // 4096 * 4096
        require(root_size < minimum, 'rootfs sizing exceeds manifest budget')
        core.guest_config(cfg, device, host['host_boot_sha256'], minimum - root_size)
        manifest_bytes = core.system_manifest(device, rev, minimum, host)
        manifest.write_bytes(manifest_bytes)
        shutil.copyfile(manifest, work / 'system.toml')
        seed = hashlib.sha256(manifest_bytes + bytes.fromhex(selected['boot.img']))
        for name in sorted(checks):
            seed.update(bytes.fromhex(checks[name][0]))
        fsid = str(uuid.uuid5(uuid.NAMESPACE_OID, seed.hexdigest()))
        image = core.make_rootfs(work, root_size, fsid, checks,
                                 tuple(TOOLS_DIR / k for k in ('mke2fs', 'e2fsck', 'debugfs')))
        write_sums(evidence / 'rootfs.sha256', {key[1:]: value[0] for key, value in checks.items()})
        expected = {'boot.img': selected['boot.img'], 'rootfs.img': file_hash(image),
                    'system.toml': file_hash(manifest)}
        archive = work / 'package.zip'
        with (evidence / 'zip.log').open('x') as log:
            subprocess.run([os.environ.get('SKYWARD_7Z', '7z'), 'a', '-tzip', '-mm=Deflate', '-mx=5',
                            'package.zip', *MEMBERS], cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
        sizes = read_zip(archive, expected)
        check_parser(run, archive, device, rev, sizes, manifest)
        write_sums(evidence / 'members.sha256', expected)
        write_sums(work / 'zip.sha256', {release.name: file_hash(archive)})
        # Atomic creation of each file, never overwrite either release path.
        os.link(archive, release)
        os.link(work / 'zip.sha256', checksum)
        print(f'Candidate and readback complete: {release}', flush=True)


def test_target(device, rev, run):
    require(run.is_relative_to((ROOT / 'evidence').resolve()), 'test build records belong under evidence/')
    return target(device, rev).with_name(f'uwu-{device}-{rev}-test.zip')


def readback(device, rev, run, *, offline=False):
    guest_sha = snapshot_guest(run)
    archive = test_target(device, rev, run) if offline else target(device, rev)
    evidence = run / device
    expected = hashes(evidence / 'members.sha256', MEMBERS)
    checksum = hashes(archive.with_name(archive.name + '.sha256'), (archive.name,))
    require(file_hash(archive) == checksum[archive.name], 'candidate archive SHA mismatch')
    paths, selected = bootset(device)
    host = legacy_host_a(device, selected['boot.img'], regular(paths['boot.img']))
    require(expected['boot.img'] == selected['boot.img'], 'candidate guest boot mismatch')
    manifest = evidence / 'system.toml'
    require(file_hash(manifest) == expected['system.toml'], 'manifest evidence drift')
    with tempfile.TemporaryDirectory(prefix='.readback-', dir=archive.parent) as tmp:
        work = Path(tmp)
        image = work / 'rootfs.img'
        sizes = read_zip(archive, expected, image)
        check_parser(run, archive, device, rev, sizes, manifest)
        subprocess.run([TOOLS_DIR / 'e2fsck', '-fn', image], check=True)
        data = tomllib.loads(manifest.read_text(encoding='ascii'))
        require({k: v for k, v in data.items() if k.startswith('host_')} == host, 'wrong host A binding')
        expected_root = hashes(evidence / 'rootfs.sha256', tuple(p[1:] for p in core.FILES))
        require(expected_root['guest-entry'] == guest_sha, 'not the shared candidate guest-entry')
        rom = hashes(evidence / 'images.sha256', IMAGES)
        require(all(rom[n] == selected[n] for n in DLKM), 'wrong device DLKM')
        tools = core.checksum_list(core.helper_dir() / 'SHA256SUMS', TOOLS)
        for name in ('bin', 'images'):
            (work / name).mkdir()
        image_bytes = 0
        for name, digest in expected_root.items():
            core.debugfs(TOOLS_DIR / 'debugfs', work, '-R', f'dump /{name} {name}')
            dumped = work / name
            size = regular(dumped)
            require(file_hash(dumped) == digest, f'rootfs readback mismatch: /{name}')
            if name in {f'images/{n}' for n in IMAGES}:
                require(digest == rom[Path(name).name], f'ROM manifest mismatch: {name}')
                image_bytes += size
            if name.startswith('bin/'):
                require(digest == tools[Path(name).name], f'not the approved P5.3 helper: {name}')
        require(hashes(work / 'images/SHA256SUMS', IMAGES) == rom, 'nested SHA256SUMS mismatch')
        require(data['minimum'] == image_bytes + 8 * core.GIB, 'wrong guest storage budget')
        core.guest_config(work / 'skyward-guest.toml', device, host['host_boot_sha256'],
                          data['minimum'] - sizes['rootfs.img'])
        listing = core.debugfs(TOOLS_DIR / 'debugfs', work, '-R', 'ls -p /volumes')
        require([line.split('/')[5] for line in listing.splitlines() if line.startswith('/')] == ['.', '..'],
                'fresh rootfs unexpectedly carries guest volume/state')
        out, _ = image_out(device)
        policy_readback(work, work / 'images', out, evidence / 'policy.sha256')
    print(f'Candidate readback complete: {archive}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('pack', 'readback'):
        p = commands.add_parser(command)
        p.add_argument('device', choices=DEVICES)
        p.add_argument('revision')
        p.add_argument('run', type=Path)
        p.add_argument('--offline-test', action='store_true', help='produce a test ZIP in the device output directory')
        if command == 'pack':
            p.add_argument('five_images', type=Path)
    args = parser.parse_args()
    os.environ.update(LC_ALL='C', TZ='UTC', E2FSPROGS_FAKE_TIME=str(core.STAMP))
    try:
        require(re.fullmatch(r'[A-Za-z0-9._-]{1,23}', args.revision), 'invalid SkyWard revision')
        run = args.run.resolve(strict=True)
        if args.command == 'pack':
            pack(args.device, args.revision, run, args.five_images, offline=args.offline_test)
        else:
            readback(args.device, args.revision, run, offline=args.offline_test)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        parser.exit(1, f'{args.command} refused: {exc}\n')


if __name__ == '__main__':
    main()
