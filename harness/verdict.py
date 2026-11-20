#!/usr/bin/env python3
"""Evaluate serial, stopped-disk and exit evidence; no success from QEMU exit alone."""
import argparse
import json
from pathlib import Path
import re
import sys

PATTERNS = {
    'system_init_restore': r'putting back what misc held',
    'system_init_handoff': r'handing over to /guest-entry',
    'guest_entry_restore': r'guest-entry: phase=restore misc .*; next=exec init',
    'guest_entry_handoff': r'Android handoff fuxi images=',
    'second_stage': r'init: init second stage started',
    'boot_completed': r'init: processing action \(sys\.boot_completed=1\) from ',
    'format_succeeded': r'\bvold\s*:\s*Format of \S+ for /data succeeded\.',
    'data_mounted': r'__mount\(source=[^,]+,target=/data,type=ext4\)=0(?:\D|$)',
    'token_consumed': r'Skyward mkfs: consumed',
    'keymint_wait': r'IKeyMintDevice/default.*(?:NAME_NOT_FOUND|could not be found|not available)|(?:Waited one second for|could not find).*IKeyMintDevice/default',
    'keystore2_wait': (
        r'Waited one second for android\.system\.keystore2\.IKeystoreService/default|'
        r"IKeystoreService/default' could not be found|"
        r"(?:dropped 'interface_start' for|Could not find) 'aidl/android\.system\.keystore2\.IKeystoreService/default'"),
    'keystore2_published': r'keystore2.*Successfully registered Keystore 2\.0 service\.',
    'keystore2_keymint_reply': r'vold:\s+keystore2 Keystore generateKey returned service specific error: -67\b',
    'soft_keymint_started': r"init: starting service 'vendor\.keymint-default'",
    'gatekeeper_wait': r'Waited one second for .*IGatekeeper/default|IGatekeeper/default.*(?:could not be found|not available|NAME_NOT_FOUND)',
    'stall': r'QEMU-only resize2fs stall injected',
    'watchdog': r'guest-entry: .*timed out after 600s during grow (?:guest )?root',
    'recovery_restart': r"reboot: Restarting system with command 'recovery'",
    'orderly_shutdown': r'reboot: Power down',
    'pstore_backend': r'pstore: Registered .* as persistent store backend',
    'pstore_diagnostic': r'guest-entry: archive pstore:.*',
}


def events(path):
    text = path.read_text(errors='replace')
    result = {}
    for label, pattern in PATTERNS.items():
        found = re.search(pattern, text)
        result[label] = None if found is None else dict(line=text[:found.start()].count('\n')+1,
            text=text[text.rfind('\n', 0, found.start())+1:text.find('\n', found.start()) if '\n' in text[found.start():] else len(text)].strip())
    return result


def load(path):
    return json.loads(path.read_text())


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2) + '\n')


def ordered(e, *keys):
    return all(e[k] for k in keys) and all(e[a]['line'] < e[b]['line'] for a, b in zip(keys, keys[1:]))


def judge(scenario, out):
    readback = load(out / 'readback.json')
    rc = load(out / 'exit.json')['exit_status']
    v = readback['volumes']
    data = v['userdata']
    head = '\n'.join(v.get('guest_entry_log_head', []))
    e = events(out / 'serial.log')
    check = {'protected_ranges_ok': readback['protected_ranges_ok'], 'qemu_started': rc in (0, 124)}
    if scenario == 'S4':
        reason = readback['recovery']
        check.update(stub_watchdog_restart=ordered(e, 'system_init_handoff', 'stall', 'watchdog', 'recovery_restart'),
            recovered_exit=rc == 0, no_android=not e['second_stage'], no_new_userdata=not data['present'],
            watchdog_bcb=(readback['command'] == 'boot-recovery' and len(reason) == 3
                          and reason[:2] == ['recovery', '--system_stopped']
                          and re.fullmatch(r'--reason=timed out after 600s during grow (?:guest )?root', reason[2]) is not None))
    else:
        check.update(system_init_handoff=ordered(e, 'system_init_restore', 'system_init_handoff', 'second_stage'),
                     guest_entry_handoff=bool(e['guest_entry_handoff'] or 'Android handoff fuxi images=' in head),
                     bcb_restored=readback['bcb_restored_bytes'], volumes_ready=v['volumes_ready'],
                     delegated_userdata=data.get('size') == 4 << 30 and data.get('marker') == 'delegated-v2')
        if scenario == 'S3':
            seed = load(out / 'injected.json')
            check['grant_reissued'] = bool(re.search(r'\bformat_grant=reissued\b', head))
            if seed['key_source'] == 'structural-invalid-test-key':
                check.update(key_retained_shape=bool(v.get('metadata_key_files_present')),
                             new_token_v2=v.get('token') == 'v2', userdata_still_zero=data.get('all_zero') is True)
                return write_verdict(scenario, out, check, e, head, structural_only=True)
            check['real_key_files_present'] = bool(v.get('metadata_key_files_present'))
        check.update(android_boot_completed=bool(e['boot_completed']), data_mounted=bool(e['data_mounted']),
                     no_remaining_token=v.get('token') == 'absent', userdata_nonzero=data.get('all_zero') is False)
        if scenario == 'S2':
            check.update(no_format=not e['format_succeeded'] and not e['token_consumed'],
                         first_boot_passed=load(out.parent / 'S1/verdict.json')['result'] == 'pass',
                         orderly_seed=load(out / 'seed.json')['orderly_shutdown'])
        else:
            check['consume_then_format_then_mount'] = ordered(e, 'token_consumed', 'format_succeeded', 'data_mounted')
    return write_verdict(scenario, out, check, e, head)


def write_verdict(scenario, out, checks, e, head, *, structural_only=False):
    publication = ('observed: explicit keystore2 registration log' if e['keystore2_published'] else
                   'observed via Binder: vold generateKey got KeyMint ROLLBACK_RESISTANCE_UNAVAILABLE (-67) from keystore2'
                   if e['keystore2_keymint_reply'] else
                   'observed indirectly: vold consumed format token after creating/using metadata key'
                   if e['token_consumed'] else 'not observed')
    scope = 'structural-only (not usable key or Android boot)' if structural_only else 'full scenario'
    status = ('partial' if structural_only else 'pass') if all(checks.values()) else 'fail'
    result = dict(scenario=scenario, result=status, scope=scope, checks=checks, serial_events=e,
                  guest_entry_handoff_snapshot=head, keystore2_publication=publication)
    save(out / 'verdict.json', result)
    return result


def s5(root):
    observations = {}
    for scenario in ('S1', 'S2'):
        out = root / scenario
        if not (out / 'readback.json').exists():
            observations[scenario] = {'bcb_pass': False, 'pstore': 'not inspected (missing readback)'}
            continue
        r, e = load(out / 'readback.json'), events(out / 'serial.log')
        observations[scenario] = dict(
            system_init_restore_before_handoff=ordered(e, 'system_init_restore', 'system_init_handoff'),
            bcb_pass=r['bcb_restored_bytes'] and ordered(e, 'system_init_restore', 'system_init_handoff',
                                                      'guest_entry_restore', 'second_stage'),
            bcb_matches_preboot=r['bcb_restored_bytes'], pstore_archive=r['volumes']['pstore_archive'],
            pstore=('archive observed' if any(r['volumes']['pstore_archive'].values()) else
                    'not observed (backend logged but no archive)' if e['pstore_backend'] else
                    'not observed (no persistent backend registration in serial)'),
            pstore_backend=e['pstore_backend'], pstore_diagnostic=e['pstore_diagnostic'])
    result = dict(scenario='S5', result='pass' if all(a['bcb_pass'] for a in observations.values()) else 'fail',
                  observations=observations)
    save(root / 'S5/verdict.json', result)
    return result


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('scenario', choices=('S1', 'S2', 'S3', 'S4', 'S5'))
    cli.add_argument('directory', type=Path, help='scenario directory (matrix root for S5)')
    args = cli.parse_args()
    report = s5(args.directory) if args.scenario == 'S5' else judge(args.scenario, args.directory)
    print(json.dumps(report, indent=2))
    sys.exit(report['result'] != 'pass')
