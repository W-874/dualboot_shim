"""Offline ext4 inspection and test-overlay-only scenario mutations."""
import csv
from pathlib import Path
import re
import subprocess
import tempfile

from prepare import replay, require, run, sha

DATA = '/volumes/userdata.raw'
META = '/volumes/metadata.ext4'
MARK = 'trusted.skyward.mkfs'
TOKEN = '/vold/skyward-mkfs-v2'
KEY_PARENT = '/vold/metadata_encryption'
KEY = f'{KEY_PARENT}/key'
KEY_FILES = ('version', 'secdiscardable', 'keymaster_key_blob', 'encrypted_key')
USERDATA_BYTES = 4 << 30


def ranges(work):
    path = work / 'twin/ranges.tsv'
    require(sha(path) == replay.checksums(path.parent / 'SHA256SUMS')['ranges.tsv'], 'changed ranges.tsv')
    with path.open() as f:
        return list(csv.DictReader(f, delimiter='\t'))


def extent(work, what):
    row, = [r for r in ranges(work) if r['policy'] == 'mutable' and r['range'].startswith(what + '@')]
    return int(row['offset']), int(row['size'])


def slice_root(work, overlay, path):
    replay.image_slice(overlay, *extent(work, 'userdata'), path)


def slice_bcb(work, overlay, path):
    offset, size = extent(work, 'misc')
    require(size == 2048, 'unexpected misc range')
    replay.image_slice(overlay, offset, size, path)


def dbg(img, cmd, log=None, *, missing_ok=False):
    result = run('/usr/sbin/debugfs', '-R', cmd, img, cwd=img.parent,
                 capture_output=True, text=True, errors='replace')
    stderr = '\n'.join(line for line in result.stderr.splitlines() if not line.startswith('debugfs '))
    if log:
        log.write(f'\n$ debugfs -R {cmd} {img.name}\n{result.stdout}{stderr}\n')
    if missing_ok and 'File not found by ext2_lookup' in stderr:
        return None
    require(not stderr, f'debugfs {img.name} {cmd}: {stderr}')
    return result.stdout


def stat(img, entry, log=None):
    return dbg(img, f'stat {entry}', log, missing_ok=True)


def data_state(root, tmp, log, *, inspect_contents):
    inode = stat(root, DATA, log)
    if inode is None:
        return {'present': False}
    attrs = dbg(root, f'ea_list {DATA}', log)
    mark = re.search(rf'^\s*{re.escape(MARK)} \(\d+\) = "([^"]+)"', attrs, re.M)
    size = int(re.search(r'^User:\s*\d+\s+Group:\s*\d+.*?\bSize:\s*(\d+)', inode, re.M)[1])
    require('Type: regular' in inode and 'EXTENTS:' in inode, 'unexpected userdata inode')
    data_extents = bool(inode.split('EXTENTS:', 1)[1].strip())
    all_zero = not data_extents
    if data_extents:
        all_zero = None
        if inspect_contents:
            dst = tmp / 'userdata-scan.raw'
            try:
                dbg(root, f'dump {DATA} {dst.name}')
                require(dst.stat().st_size == size, 'userdata dump size differs from inode')
                block = bytes(1 << 20)
                with dst.open('rb') as f:
                    all_zero = all(b == block[:len(b)] for b in iter(lambda: f.read(len(block)), b''))
            finally:
                dst.unlink(missing_ok=True)
    return {'present': True, 'marker': mark[1] if mark else None, 'size': size,
            'has_data_extents': data_extents, 'all_zero': all_zero}


def metadata_state(md, log):
    dbg(md, 'ls -l /', log)
    for entry in ('/vold', KEY_PARENT, KEY):
        if stat(md, entry, log) is not None:
            dbg(md, f'ls -l {entry}', log)
    token = stat(md, TOKEN, log)
    token_state = 'absent'
    if token is not None:
        token_state = 'invalid'
        if ('Type: regular' in token and 'Mode:  0600' in token
                and re.search(r'User:\s*0\s+Group:\s*0.*\bSize:\s*3\b', token)
                and re.search(r'Links:\s*1\b', token) and dbg(md, f'cat {TOKEN}', log) == 'v2\n'):
            token_state = 'v2'
    key = all('Type: regular' in (stat(md, f'{KEY}/{n}', log) or '') for n in KEY_FILES)
    return {'token': token_state, 'metadata_key_files_present': key,
            'metadata_in_progress': stat(md, f'{KEY_PARENT}/in_progress', log) is not None}


def proof(root, tmp, log, *, inspect_contents=True):
    state = {'userdata': data_state(root, tmp, log, inspect_contents=inspect_contents),
             'metadata_present': stat(root, META, log) is not None,
             'volumes_ready': stat(root, '/volumes/state/volumes-ready', log) is not None,
             'probe_ok': stat(root, '/volumes/state/probe-ok', log) is not None,
             'pstore_archive': {name: stat(root, f'/volumes/logs/{name}', log) is not None
                               for name in ('pstore', 'pstore.previous')}}
    if state['metadata_present']:
        md = tmp / 'volume-proof.ext4'
        dbg(root, f'dump {META} {md.name}', log)
        state.update(metadata_state(md, log))
        md.unlink()
    guest_log = dbg(root, 'cat /volumes/logs/guest-entry', log, missing_ok=True) or ''
    state['guest_entry_log_head'] = guest_log.splitlines()[:1]
    return state


def fsck(img, log, *, repair=False):
    cmd = ['/usr/sbin/e2fsck', '-p' if repair else '-fn', img]
    result = subprocess.run(cmd, stdout=log, stderr=log)
    require(result.returncode in ((0, 1) if repair else (0,)), f'e2fsck {img.name}: exit {result.returncode}')


def apply_root(work, overlay, before, after, log):
    """Write only changed 64 KiB blocks within the mutable extent; read back the result."""
    off, size = extent(work, 'userdata')
    require(off % 4096 == size % 4096 == 0 and before.stat().st_size == after.stat().st_size == size,
            'root slice geometry changed')
    commands = []
    with before.open('rb') as a, after.open('rb') as b:
        for i in range(0, size, 65536):
            x, y = a.read(65536), b.read(65536)
            if x != y:
                name = f'delta-{i}'
                (before.parent / name).write_bytes(y)
                commands.append(f'write -s {name} {off+i} {len(y)}')
    run('qemu-io', '-f', 'qcow2', overlay, cwd=before.parent, stdout=log, stderr=log,
        input='\n'.join([*commands, 'flush', 'quit', '']), text=True)
    verify = before.parent / 'verify.ext4'
    slice_root(work, overlay, verify)
    require(sha(verify) == sha(after), 'offline root mutation did not round trip to the qcow2')
    verify.unlink()


def prepare_wedge(root, tmp, log, profile, *, structural_only):
    """Use the seed's own real KeyMint files; only fabricate structure if explicitly recorded."""
    seed = proof(root, tmp, log, inspect_contents=False)
    require(seed['volumes_ready'] and seed['metadata_present'] and seed['userdata'].get('size') == USERDATA_BYTES,
            'S3 requires a seed that finished provisioning its private volumes')
    md = tmp / 'metadata.ext4'
    dbg(root, f'dump {META} {md.name}', log)
    fsck(md, log, repair=True)
    real = profile == 'softkm' and metadata_state(md, log)['metadata_key_files_present']
    require(real or structural_only, 'S3 needs real softkm metadata keys, or use --structural-s3 for shape-only')
    if not real:
        require(stat(md, KEY, log) is None, 'refusing to replace existing non-softkm key directory')
    actions = []
    if stat(md, TOKEN) is not None:
        actions.append(f'rm {TOKEN}')
    if stat(md, f'{KEY_PARENT}/in_progress') is not None:
        actions.append(f'rm {KEY_PARENT}/in_progress')
    if not real:
        for d in ('/vold', KEY_PARENT, KEY):
            if stat(md, d) is None:
                actions.append(f'mkdir {d}')
            actions.append(f'sif {d} mode 040700')
        (tmp / 'fake-key').write_bytes(b'qemu-only-invalid-key\n')
        (tmp / 'fake-version').write_bytes(b'1')
        for name in KEY_FILES:
            actions.extend((f'write fake-{"version" if name == "version" else "key"} {KEY}/{name}',
                            f'sif {KEY}/{name} mode 0100600'))
    mutate(md, actions, log)
    fsck(md, log)
    # Replace the nested ext4 preserving the backing inode's required label and mode.
    dbg(root, f'ea_get -f metadata-label {META} security.selinux', log)
    mutate(root, [f'punch {DATA} 0', f'ea_set {DATA} {MARK} delegated-v2', f'rm {META}',
                  f'write {md.name} {META}', f'sif {META} mode 0100600',
                  f'ea_set -f metadata-label {META} security.selinux'], log)
    got = proof(root, tmp, log, inspect_contents=False)
    require(got['token'] == 'absent' and got['metadata_key_files_present'] and not got['metadata_in_progress']
            and got['userdata'] == dict(present=True, size=USERDATA_BYTES, marker='delegated-v2',
                                       has_data_extents=False, all_zero=True), 'post-edit wedge shape mismatch')
    return dict(got, key_source='real-softkm-seed' if real else 'structural-invalid-test-key')


def mutate(img, actions, log):
    if actions:
        script = img.parent / 'debugfs.commands'
        script.write_text('\n'.join(actions) + '\n')
        result = run('/usr/sbin/debugfs', '-w', '-f', script.name, img, cwd=img.parent,
                     text=True, capture_output=True)
        log.write(result.stdout + result.stderr)
        require(all(line.startswith('debugfs ') for line in result.stderr.splitlines()),
                f'debugfs mutation failed on {img.name}: {result.stderr}')


def inject(work, overlay, out, scenario, profile, clang, structural_only):
    with tempfile.TemporaryDirectory(prefix='inject-', dir=out) as td, (out / 'patch.log').open('w') as log:
        tmp = Path(td)
        before, after = tmp / 'before.ext4', tmp / 'after.ext4'
        slice_root(work, overlay, before)
        run('cp', '--reflink=auto', '--sparse=always', before, after)
        fsck(after, log, repair=True)
        if scenario == 'S3':
            state = prepare_wedge(after, tmp, log, profile, structural_only=structural_only)
        else:
            stub = tmp / 'resize2fs-stall'
            run(clang, '-O2', '-static', '-o', stub, Path(__file__).with_name('resize2fs-stall.c'))
            replay.twin.static_arm64(stub)
            mutate(after, ['rm /bin/resize2fs', f'write {stub.name} /bin/resize2fs',
                           'sif /bin/resize2fs mode 0100755'], log)
            dbg(after, 'dump /bin/resize2fs check-stub', log)
            require(sha(stub) == sha(tmp / 'check-stub'), 'stub bytes differ in test rootfs')
            state = dict(resize2fs_stub_sha256=sha(stub))
        fsck(after, log)
        apply_root(work, overlay, before, after, log)
        replay.write_json(out / 'injected.json', state)
