#!/usr/bin/env python3
"""P6.3 verifier for a real, instrumented Android userdebug guest on a private twin disk.

P63 serial markers are test hooks, not proof of an Android boot. The runner
also checks guest properties, mounted data, the persisted boot log and misc.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import time
import tomllib
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
B = 4 * 1024 * 1024
HOOK = '/vendor/bin/p63-control'
DATA_MARK = '/data/local/tmp/skyward-p63-data'
BOOT_READY = 'P63 android-console-ready'
CASES = range(1, 10)
STAGES = (
    # 'probe-commit' denotes the durable first verification, not a reboot.
    'grow-begin', 'provision-publish', 'probe-commit',
    'format-grant', 'mkfs-begin', 'mkfs-end', 'boot-completed',
)


def need(ok, why):
    if not ok:
        raise RuntimeError(why)


def regular(p):
    need(stat.S_ISREG(p.lstat().st_mode), f'not a regular file: {p}')
    return p


def digest(p):
    with regular(p).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def sum_manifest(directory, names):
    sums = {}
    for line in regular(directory / 'SHA256SUMS').read_text().splitlines():
        sha, name = line.split()
        need(name not in sums, f'duplicate checksum: {name}')
        sums[name] = sha
    for name in names:
        need(digest(directory / name) == sums.get(name), f'bad or missing checksum: {directory / name}')
    return {name: sums[name] for name in names}


def rows(path):
    with regular(path).open() as f:
        return list(csv.DictReader(f, delimiter='\t'))


def copy_range(source, dest, offset, size):
    with regular(source).open('rb') as src, dest.open('xb') as dst:
        src.seek(offset)
        left = size
        while left:
            chunk = src.read(min(B, left))
            need(chunk, 'short read during extraction')
            if chunk.count(0) == len(chunk):
                dst.seek(len(chunk), os.SEEK_CUR)
            else:
                dst.write(chunk)
            left -= len(chunk)
        dst.truncate(size)


class Layout:
    def __init__(self, directory):
        self.directory = directory
        self.base = regular(directory / 'disk.raw')
        self.ranges = rows(directory / 'ranges.tsv')
        self.parts = {}
        for r in rows(directory / 'partitions.tsv'):
            name = r['name']
            need(re.fullmatch(r'[A-Za-z0-9_.-]+', name) and name not in self.parts, 'invalid partition name')
            self.parts[name] = int(r['offset']), int(r['size'])
        start = tomllib.loads(regular(directory / 'start.toml').read_text())
        area = start['root']
        need(start['init'] == '/guest-entry' and start['filesystem'] == 'ext4' and area['partition'] == 'userdata', 'wrong Start')
        need(len(area['extents']) == 1, 'expected TwinInputs single extent')
        at, size = area['extents'][0]
        userdata, length = self.parts['userdata']
        need(at >= 0 and 4096 < size == area['size'] and at + size <= length, 'guest extent out of bounds')
        self.rootfs = userdata + at + 4096, size - 4096
        allowed = {self.parts[name] for name in ('misc', 'modemst1', 'modemst2')} | {self.rootfs}
        offset = 0
        marked = set()
        for r in self.ranges:
            at, length = int(r['offset']), int(r['size'])
            need(at == offset and length > 0 and r['policy'] in ('protected', 'mutable'), 'invalid range coverage')
            offset += length
            if r['policy'] == 'mutable':
                marked.add((at, length))
        need(offset == self.base.stat().st_size and marked == allowed, 'mutable ranges do not match P6.3 whitelist')

    def snapshot(self, disk, dest, prior=None):
        out = {}
        failures = []
        with regular(disk).open('rb') as src, dest.open('x') as log:
            log.write('range\toffset\tsize\tpolicy\tbaseline\tbefore\tsha256\tstatus\n')
            for r in self.ranges:
                name, size = r['range'], int(r['size'])
                need(name not in out, 'duplicate range label')
                digestor = hashlib.sha256()
                remaining = size
                while remaining:
                    part = src.read(min(B, remaining))
                    need(part, 'short read hashing disk')
                    remaining -= len(part)
                    digestor.update(part)
                sha = out[name] = digestor.hexdigest()
                before = prior[name] if prior is not None else r['sha256']
                changed = sha != before
                baseline_changed = sha != r['sha256']
                bad = baseline_changed and (prior is None or r['policy'] == 'protected')
                if bad:
                    failures.append(name)
                status = 'FAIL' if bad else ('CHANGED' if changed else 'OK')
                log.write(f"{name}\t{r['offset']}\t{size}\t{r['policy']}\t{r['sha256']}\t{before}\t{sha}\t{status}\n")
            need(not src.read(1), 'disk size changed')
        need(not failures, 'sentinels differ: ' + ', '.join(failures))
        return out


def debugfs(image, command, log):
    regular(image)
    p = subprocess.run(['debugfs', '-R', command, image], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, check=True)
    with log.open('a') as f:
        f.write(f'$ debugfs -R {command}\n{p.stdout}')
    return p.stdout


class VM:
    def __init__(self, args, disk, log):
        regular(disk)
        self.log = log
        self.tmp = tempfile.TemporaryDirectory(prefix='p63-', dir='/tmp')
        self.sel = selectors.DefaultSelector()
        self.proc = None
        self.peers = {}
        self.buffers = {'serial': '', 'qmp': ''}
        self.events = []
        self.replies = {}
        self.lines = []
        self.exit = (log / 'qemu.stderr').open('xb')
        self.serial_log = (log / 'serial.log').open('xb')
        self.qmp_log = (log / 'qmp.log').open('xb')
        cmdline = regular(args.kernel / 'cmdline.txt').read_text().strip()
        cmd = [str(args.qemu), '-S', '-machine', 'virt,accel=tcg', '-cpu', 'max',
               '-smp', '4', '-m', '4096', '-nodefaults', '-no-reboot', '-display', 'none',
               '-nic', 'none', '-monitor', 'none', '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny',
               '-qmp', 'unix:qmp,server=on,wait=off', '-serial', 'unix:serial,server=on,wait=off',
               '-kernel', str(args.kernel / 'Image'), '-initrd', str(args.kernel / 'initramfs.img'),
               '-append', cmdline + ' androidboot.console=ttyAMA0',
               '-drive', f'if=none,id=twin,format=raw,cache=directsync,file={str(disk).replace(",", ",,")}',
               '-device', 'virtio-blk-device,drive=twin',
               '-device', 'virtio-scsi-device,id=p63scsi', '-device', 'scsi-cd,bus=p63scsi.0']
        (log / 'qemu.argv.json').write_text(json.dumps(cmd, indent=2) + '\n')
        try:
            self.proc = subprocess.Popen(cmd, cwd=self.tmp.name, stdin=subprocess.DEVNULL,
                                         stdout=self.exit, stderr=self.exit, close_fds=True)
            time_limit = time.monotonic() + 20
            for name in ('qmp', 'serial'):
                peer = socket.socket(socket.AF_UNIX)
                self.peers[name] = peer
                while True:
                    try:
                        peer.connect(str(Path(self.tmp.name) / name))
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        need(time.monotonic() < time_limit and self.proc.poll() is None, f'QEMU {name} socket unavailable')
                        time.sleep(.05)
                self.sel.register(peer, selectors.EVENT_READ, name)
            for command in ('qmp_capabilities', 'cont'):
                self.peers['qmp'].sendall((json.dumps({'execute': command, 'id': command}) + '\n').encode())
                self.wait(lambda: command in self.replies, 10, f'QMP {command}')
                need('return' in self.replies[command], f'QMP rejected {command}')
        except BaseException:
            self.close()
            raise

    def pump(self, timeout):
        for key, _ in self.sel.select(timeout):
            data = key.fileobj.recv(65536)
            name = key.data
            if not data:
                self.sel.unregister(key.fileobj)
                continue
            (self.serial_log if name == 'serial' else self.qmp_log).write(data)
            segments = (self.buffers[name] + data.decode(errors='replace')).split('\n')
            self.buffers[name] = segments.pop()
            for line in segments:
                if name == 'serial':
                    self.lines.append(line.rstrip('\r'))
                else:
                    msg = json.loads(line)
                    if 'event' in msg:
                        self.events.append(msg)
                    if 'id' in msg:
                        self.replies[msg['id']] = msg

    def wait(self, predicate, timeout, description):
        end = time.monotonic() + timeout
        while not predicate():
            need(time.monotonic() < end, f'timeout: {description}')
            self.pump(.1)
            need(self.proc.poll() is None or predicate() or self.sel.get_map(), f'QEMU exited before {description}')

    def line(self, pattern, timeout, start=0):
        self.wait(lambda: any(re.fullmatch(pattern, s) for s in self.lines[start:]), timeout, pattern)

    def shell(self, cmd, timeout):
        token = uuid.uuid4().hex
        start = len(self.lines)
        script = f"{cmd}; rc=$?; printf '\\nP63-%s:%s\\n' {token} \"$rc\"\n"
        self.peers['serial'].sendall(script.encode())
        self.line(f'P63-{token}:[0-9]+', timeout, start)
        out = self.lines[start:]
        need(f'P63-{token}:0' in out, f'guest command failed: {cmd}')
        return out

    def stop(self, timeout):
        self.wait(lambda: any(e['event'] == 'SHUTDOWN' and e.get('data', {}).get('guest') is True
                              for e in self.events), timeout, 'guest shutdown/reset')
        need(self.proc.wait(timeout=15) == 0, 'nonzero QEMU exit')
        (self.log / 'exit.txt').write_text('guest shutdown/reset (QMP); QEMU exit=0\n')

    def cut(self, checkpoint):
        need(self.proc.poll() is None, f'guest already exited before cut {checkpoint}')
        self.proc.kill()
        need(self.proc.wait(timeout=15) == -signal.SIGKILL, 'QEMU did not die from SIGKILL')
        (self.log / 'exit.txt').write_text(f'SIGKILL while guest paused at {checkpoint}; exit=-9\n')

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=15)
            (self.log / 'exit.txt').write_text('cleanup SIGKILL (not a power-cut checkpoint)\n')
        for _ in range(4):
            self.pump(0)
        self.sel.close()
        for peer in self.peers.values():
            peer.close()
        for f in (self.exit, self.serial_log, self.qmp_log):
            f.close()
        self.tmp.cleanup()


class Suite:
    def __init__(self, args, work):
        self.a, self.work = args, work
        self.layout = Layout(args.disk)
        self.disk = work / 'disk.raw'
        self.tool = work / 'cargo/debug/fuxi-twin-host'
        self.mark = uuid.uuid4().hex

    def reset(self, seed):
        regular(seed)
        self.disk.unlink(missing_ok=True)
        subprocess.run(['/usr/bin/cp', '--reflink=auto', '--sparse=always', '--', seed, self.disk], check=True)
        m = regular(self.disk).stat()
        need(m.st_uid == os.geteuid() and m.st_nlink == 1, 'disk is not a private copy')
        os.chmod(self.disk, 0o600)

    def save(self, name):
        p = self.work / name
        subprocess.run(['/usr/bin/cp', '--reflink=auto', '--sparse=always', '--', self.disk, p], check=True)
        return p

    def evidence(self, where, stage, mode):
        off, size = self.layout.parts['misc']
        raw = where / f'{stage}.misc.raw'
        copy_range(self.disk, raw, off, size)
        (where / f'{stage}.misc.sha256').write_text(digest(raw) + '\n')
        with tempfile.TemporaryDirectory(dir=self.work) as d:
            tmp = Path(d)
            copy_range(self.disk, tmp / 'metadata', *self.layout.parts['metadata'])
            copy_range(self.disk, tmp / 'init_boot_a', *self.layout.parts['init_boot_a'])
            state = tmp / 'systems.toml'
            log = where / f'{stage}.bcb.log'
            debugfs(tmp / 'metadata', f'dump /skyward/systems.toml {state}', log)
            with log.open('a') as out:
                subprocess.run([self.tool, raw, tmp / 'misc', state, tmp, mode], stdout=out,
                               stderr=subprocess.STDOUT, check=True)
            with (tmp / 'misc').open('rb') as src:
                return src.read(2048)

    def before(self, dest):
        # Take snapshots against both the pristine fixture and the immediate predecessor.
        baseline = {r['range']: r['sha256'] for r in self.layout.ranges}
        return self.layout.snapshot(self.disk, dest, baseline)

    def boot(self, name, action, mode='safe', carry=False):
        where = self.a.out / name
        where.mkdir(parents=True)
        prior = self.before(where / 'before.tsv')
        self.evidence(where, 'before', 'safe')
        errors = []
        vm = None
        try:
            vm = VM(self.a, self.disk, where)
            action(vm)
        except Exception as e:
            errors.append(f'VM: {e}')
        finally:
            if vm is not None:
                try:
                    vm.close()
                except Exception as e:
                    errors.append(f'VM cleanup: {e}')
            try:
                self.layout.snapshot(self.disk, where / 'after.tsv', prior)
            except Exception as e:
                errors.append(f'comparison: {e}')
            try:
                consume = self.evidence(where, 'after', mode)
            except Exception as e:
                errors.append(f'misc: {e}')
        (where / 'result.txt').write_text(('FAIL\n' + '\n'.join(errors) if errors else 'OK') + '\n')
        need(not errors, f'{name}: ' + '; '.join(errors))
        if carry:
            # Direct kernel boot has no recovery. Replay only the already-verified
            # P3.1 BCB clear, and only after preserving the original full misc.
            need(consume == bytes(2048), 'unexpected BCB consumption')
            with regular(self.disk).open('r+b', buffering=0) as f:
                f.seek(self.layout.parts['misc'][0])
                need(f.write(consume) == len(consume), 'short BCB clear')
                os.fsync(f.fileno())

    def corrupt(self, name, image):
        """Modify allocated blocks of a guest file, never a host filesystem or partition."""
        where = self.a.out / name
        where.mkdir(parents=True)
        prior = self.before(where / 'before.tsv')
        offset, size = self.layout.rootfs
        with tempfile.TemporaryDirectory(dir=self.work) as d:
            root = Path(d) / 'guest.ext4'
            copy_range(self.disk, root, offset, size)
            log = where / 'mutation.log'
            info = debugfs(root, 'stats', log)
            block = int(re.search(r'^Block size:\s+(\d+)$', info, re.M)[1])
            need(block in (1024, 2048, 4096), 'unsupported ext4 block size')
            count = 8 if image == '/volumes/userdata.raw' else 1
            changed = []
            with regular(self.disk).open('r+b', buffering=0) as f:
                for logical in range(count):
                    m = debugfs(root, f'bmap {image} {logical}', log).splitlines()[-1]
                    need(m.isdecimal(), 'debugfs did not map file block')
                    physical = int(m) * block
                    if not physical:
                        continue
                    need(physical + block <= size, 'ext4 block outside guest area')
                    f.seek(offset + physical)
                    original = f.read(block)
                    f.seek(offset + physical)
                    mask = b'\xff' * block if count > 1 else b'\xff'
                    data = bytes(x ^ y for x, y in zip(original, mask))
                    need(f.write(data) == len(data), 'short fault injection')
                    changed.append((logical, offset + physical, len(data)))
                os.fsync(f.fileno())
            with log.open('a') as f:
                f.write(f'{image} changed (file block, disk offset, bytes): {changed!r}\n')
            need(changed, f'{image}: no allocated blocks to corrupt')
        self.layout.snapshot(self.disk, where / 'after.tsv', prior)

    def checkpoint(self, vm, stage):
        vm.line(r'(?:\[[^]]+\]\s*)?P63 cut-ready ' + re.escape(stage), self.a.timeout)
        vm.cut(stage)

    def ready(self, vm):
        vm.line(BOOT_READY, self.a.timeout)
        out = vm.shell('test "$(getprop sys.boot_completed)" = 1 && '
                       'test "$(getprop ro.debuggable)" = 1 && test "$(getenforce)" = Enforcing && '
                       f'su 0 {HOOK} assert-data-mounted', self.a.timeout)
        need(any(line.split()[:3] == ['P63', 'data-mounted', 'crypto=metadata'] for line in out),
             'no metadata-encrypted Android data mount evidence')

    def android(self, vm, action, format_expected):
        self.ready(vm)
        events = [s for s in vm.lines if re.fullmatch(r'(?:\[[^]]+\]\s*)?P63 mkfs-(?:begin|end) userdata', s)]
        expected = ['begin', 'end'] if format_expected else []
        need([re.search(r'mkfs-(begin|end)', s)[1] for s in events] == expected, f'unexpected guest userdata mkfs evidence: {events}')
        vm.shell(action, self.a.timeout)
        vm.peers['serial'].sendall(b'su 0 reboot -p\n')
        vm.stop(self.a.timeout)

    def first_verification(self, where):
        # Read the guest's own report from the same boot, before any restart.
        with tempfile.TemporaryDirectory(dir=self.work) as d:
            root = Path(d) / 'guest.ext4'
            copy_range(self.disk, root, *self.layout.rootfs)
            report = debugfs(root, 'cat /volumes/logs/guest-entry', where / 'verification.log')
        need(re.search(r'^Android handoff fuxi images=7/7 ', report, re.M),
             'first Android handoff did not report a full image check')

    def writes(self, vm):
        self.ready(vm)
        names = sorted(self.layout.parts)
        out = vm.shell('su 0 /vendor/bin/p63-write-probe ' + ' '.join(map(shlex.quote, names)), self.a.timeout)
        result = [tuple(s.split()[1:]) for s in out if re.fullmatch(r'WRITE \S+ (?:EPERM|EACCES)', s)]
        keys = [k for k, _ in result]
        fixed = set(names) | {'whole'}
        bsg = {k for k in keys if k.startswith('bsg:')}
        need(bsg and len(keys) == len(set(keys)) and set(keys) == fixed | bsg, 'missing/duplicate raw-node deny results')
        vm.peers['serial'].sendall(b'su 0 reboot -p\n')
        vm.stop(self.a.timeout)

    def hook(self, vm, action, wait=None):
        self.ready(vm)
        vm.peers['serial'].sendall(f'su 0 {HOOK} {action}\n'.encode())
        if wait:
            vm.line(wait, self.a.timeout)
        vm.stop(self.a.timeout)

    def case(self, n, action):
        try:
            action()
        except Exception as e:
            self.status[n] = f'FAIL: {e}'
        else:
            self.status[n] = 'PASS' if n not in (1, 2, 3, 4, 5) else 'OBSERVED (see BCB; empty is not a refusal/reason)'
        self.record()

    def record(self):
        (self.a.out / 'status.json').write_text(json.dumps(self.status, ensure_ascii=False, indent=2) + '\n')

    def run(self):
        self.status = {n: 'BLOCKED: prerequisite did not finish' for n in CASES}
        self.layout.snapshot(self.layout.base, self.a.out / 'pristine.tsv')
        self.reset(self.layout.base)
        # Cases 1 and 2 share the pristine disk's first VM and BCB evidence.
        first = '01-02-first-android'
        self.case(2, lambda: self.boot(first, lambda vm: self.android(vm,
                  f'printf %s {self.mark} > {DATA_MARK} && sync', True), 'empty'))
        formatted = self.save('formatted.raw') if self.status[2].startswith('OBSERVED') else None
        if formatted:
            self.case(1, lambda: self.first_verification(self.a.out / first))
            self.case(3, lambda: self.boot('03-data-retained', lambda vm: self.android(vm,
                      f'test "$(cat {DATA_MARK})" = {self.mark}', False)))

        def cut_matrix():
            failures = []
            for stage in STAGES:
                try:
                    self.reset(self.layout.base)
                    name = f'04-cut/{stage}'
                    self.boot(name + '/poweroff', lambda vm: self.checkpoint(vm, stage), carry=True)
                    # During/after formatting, an interrupted one-shot grant may
                    # fail closed instead of allowing a second format.
                    self.boot(name + '/restart', lambda vm: self.resume(vm, stage))
                except Exception as e:
                    failures.append(f'{stage}: {e}')
            need(not failures, '; '.join(failures))

        self.case(4, cut_matrix)
        if formatted:
            def denial():
                self.reset(formatted)
                self.boot('05-root-writes', self.writes)
            self.case(5, denial)

            def requests():
                errors = []
                for kind in ('reset', 'rescue', 'ota'):
                    for mode in ('request', 'stopped'):
                        try:
                            self.reset(formatted)
                            policy = f'request:{kind}' if mode == 'request' else f'stopped:{kind}'
                            self.boot(f'06-requests/{kind}/{mode}', lambda vm: self.hook(vm, f'{kind} {mode}'), policy)
                        except Exception as e:
                            errors.append(f'{kind}/{mode}: {e}')
                need(not errors, '; '.join(errors))
            self.case(6, requests)

            def userdata_fault():
                self.reset(formatted)
                self.corrupt('07-volume/fault', '/volumes/userdata.raw')
                self.boot('07-volume/boot', lambda vm: vm.stop(self.a.timeout),
                          'stopped:android did not finish booting within 8 minutes')
            self.case(7, userdata_fault)

            def zygote():
                self.reset(formatted)
                self.boot('09-zygote', self.zygote, 'stopped:android did not finish booting within 8 minutes')
            self.case(9, zygote)

        def image_fault():
            self.reset(self.layout.base)
            self.corrupt('08-images/fault', '/images/system.img')
            self.boot('08-images/boot', lambda vm: vm.stop(self.a.timeout), 'stopped:SHA-256 mismatch for /images/system.img')
        self.case(8, image_fault)

        self.record()
        return 0 if all(s == 'PASS' for s in self.status.values()) else 1

    def resume(self, vm, stage):
        self.wait_ready_or_stop(vm)
        formats = sum(bool(re.fullmatch(r'(?:\[[^]]+\]\s*)?P63 mkfs-begin userdata', s)) for s in vm.lines)
        need(formats <= (0 if stage in ('format-grant', 'mkfs-begin', 'mkfs-end', 'boot-completed') else 1),
             f'{stage}: a spent format grant was reused')

    def wait_ready_or_stop(self, vm):
        vm.wait(lambda: BOOT_READY in vm.lines or any(e['event'] == 'SHUTDOWN' for e in vm.events),
                self.a.timeout, 'Android console or guest recovery reboot')
        if BOOT_READY in vm.lines and vm.proc.poll() is None:
            self.ready(vm)
            vm.peers['serial'].sendall(b'su 0 reboot -p\n')
        vm.stop(self.a.timeout)

    def zygote(self, vm):
        vm.stop(self.a.timeout)
        deaths = set(match[1] for s in vm.lines if (match := re.fullmatch(r'(?:\[[^]]+\]\s*)?P63 zygote-fatal pid=(\d+)', s)))
        need(len(deaths) >= 3 and BOOT_READY not in vm.lines, 'no early repeated zygote deaths preceding watchdog')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--disk', type=Path, default=ROOT / 'evidence/P6.1/fuxi/disk')
    cli.add_argument('--kernel', type=Path, default=ROOT / 'evidence/P6.2/fuxi/kernel')
    qemu = shutil.which('qemu-system-aarch64') or ROOT / '.p0-tools/fedora/usr/bin/qemu-system-aarch64'
    cli.add_argument('--qemu', type=Path, default=qemu)
    cli.add_argument('--out', type=Path, required=True, help='new evidence directory')
    cli.add_argument('--timeout', type=int, default=1200, help='seconds per guest operation (watchdog is 480s)')
    args = cli.parse_args()
    need(os.geteuid() != 0, 'do not run the verifier as host root')
    need(args.timeout > 480, 'timeout must allow the real 8 minute watchdog')
    for name in ('qemu', 'disk', 'kernel', 'out'):
        setattr(args, name, getattr(args, name).absolute())
    args.out.mkdir(parents=True, mode=0o700)
    disk_names = ('disk.raw', 'ranges.tsv', 'partitions.tsv', 'start.toml', 'systems.toml', 'fixture.json')
    kernel_names = ('Image', 'initramfs.img', 'cmdline.txt', 'vendor-ramdisk.cpio', 'skyward-ramdisk.lz4')
    want = [(args.qemu.parent, [args.qemu.name]), (args.disk, [*disk_names, 'SHA256SUMS']),
            (args.kernel, [*kernel_names, 'SHA256SUMS'])]
    missing = [str(p / name) for p, names in want for name in names if not (p / name).is_file()]
    missing.extend(tool for tool in ('cargo', 'debugfs') if not shutil.which(tool))
    if missing:
        (args.out / 'BLOCKED.txt').write_text('Missing real inputs:\n' + '\n'.join(missing) + '\n')
        (args.out / 'status.json').write_text(json.dumps({n: 'BLOCKED: no matched real inputs' for n in CASES}, indent=2) + '\n')
        return 2
    try:
        disk_sha = sum_manifest(args.disk, disk_names)
        kernel_sha = sum_manifest(args.kernel, kernel_names)
        need(os.access(regular(args.qemu), os.X_OK), 'QEMU is not executable')
        (args.out / 'inputs.json').write_text(json.dumps({'disk': disk_sha, 'kernel': kernel_sha,
                                                         'qemu': digest(args.qemu)}, indent=2) + '\n')
        with tempfile.TemporaryDirectory(prefix='.p63-work-', dir=args.out) as work:
            suite = Suite(args, Path(work))
            with (args.out / 'bcb-build.log').open('xb') as log:
                subprocess.run(['cargo', 'build', '--offline', '--locked', '--manifest-path', HERE / 'Cargo.toml',
                                '--target-dir', suite.work / 'cargo'], stdout=log, stderr=subprocess.STDOUT, check=True)
            return suite.run()
    except Exception as e:
        (args.out / 'run-error.txt').write_text(str(e) + '\n')
        raise


if __name__ == '__main__':
    raise SystemExit(main())
