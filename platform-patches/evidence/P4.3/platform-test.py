#!/usr/bin/env python3
"""Run P4.3 platform guard tests entirely on the host (Python, C++, Rust, JDK)."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def method(source, signature, indent=""):
    """Select a complete method at its source indentation; fail if ambiguous."""
    pattern = rf"^{indent}{signature}[^{{]*\{{\n.*?^{indent}\}}$"
    matches = re.findall(pattern, source, re.MULTILINE | re.DOTALL)
    if len(matches) != 1:
        raise ValueError(f"expected one method matching {signature!r}, got {len(matches)}")
    return matches[0]


def run(cmd, work):
    subprocess.run(cmd, cwd=work, check=True)


def test_native(src, work):
    gk = (src / "system/core/gatekeeperd/gatekeeperd.cpp").read_text()
    compat = (src / "system/security/keystore2/src/km_compat/km_compat.cpp").read_text()
    fixture = r'''
#include <cassert>
#include <cstdint>
#include <string>

std::string guest;
bool permitted = true;
namespace android::base {
std::string GetProperty(const char* name, const char* fallback) {
    assert(std::string(name) == "ro.skyward.guest");
    return guest.empty() ? fallback : guest;
}
}
#define ALOGI(...) ((void)0)
#define ALOGE(...) ((void)0)
#define LOG(...) LogSink{}
struct LogSink { template<class T> void operator<<(T) {} };
struct GatekeeperResponse {};
struct Status {
    static Status ok() { return {true}; }
    bool isOk() const { return success; }
    bool success;
};
struct IPCThreadState {
    static IPCThreadState* self() { static IPCThreadState ipc; return &ipc; }
    int getCallingPid() { return 0; }
    int getCallingUid() { return 0; }
};
constexpr int KEYGUARD_PERMISSION = 0;
struct PermissionCache { static bool checkPermission(int, int, int) { return permitted; } };
struct Hal {
    int all = 0, one = 0;
    void deleteAllUsers() { ++all; }
    template<class F> void deleteAllUsers(F) { deleteAllUsers(); }
    void deleteUser(uint32_t id) { assert(id == 42); ++one; }
    template<class F> void deleteUser(uint32_t id, F) { deleteUser(id); }
};
struct GateKeeperProxy {
    Hal *aidl_hw_device, *hw_device;
    bool is_running_gsi = false, clear_state_if_needed_done = false;
    int markers = 0, local_sid_clears = 0;
    bool mark_cold_boot() { return ++markers == 1; }
    void clear_sid(uint32_t id) { assert(id == 42); ++local_sid_clears; }
    Status adjust_userId(uint32_t id, uint32_t* out) { *out = id; return Status::ok(); }
    void clear_state_if_needed();
    Status clearSecureUserId(int32_t);
};
namespace KMV1 { enum class ErrorCode { OK, UNIMPLEMENTED, UNKNOWN_ERROR }; }
enum class V4_0_ErrorCode { UNIMPLEMENTED = static_cast<int>(KMV1::ErrorCode::UNIMPLEMENTED) };
struct ScopedAStatus {
    static ScopedAStatus fromServiceSpecificError(int code) { return {KMV1::ErrorCode(code)}; }
    bool isOk() const { return code == KMV1::ErrorCode::OK; }
    const char* description() const { return "unexpected"; }
    KMV1::ErrorCode code;
};
ScopedAStatus convertErrorCode(KMV1::ErrorCode code) { return {code}; }
ScopedAStatus convertErrorCode(ScopedAStatus status) { return status; }
struct LegacyHal {
    int calls = 0;
    ScopedAStatus deleteAllKeys() { ++calls; return {KMV1::ErrorCode::OK}; }
};
struct KeyMintDevice {
    LegacyHal* mDevice;
    ScopedAStatus deleteAllKeys();
    ScopedAStatus destroyAttestationIds();
};
'''.replace('void operator<<(T) {}', 'LogSink& operator<<(T) { return *this; }')
    native_methods = [
        method(gk, r"void GateKeeperProxy::clear_state_if_needed\("),
        method(gk, r"Status GateKeeperProxy::clearSecureUserId\("),
        method(compat, r"ScopedAStatus KeyMintDevice::deleteAllKeys\("),
        method(compat, r"ScopedAStatus KeyMintDevice::destroyAttestationIds\("),
    ]
    checks = r'''
int main() {
    for (const char* value : {"true", "false", ""}) {
        guest = value;
        bool blocked = guest == "true";
        for (bool aidl : {false, true}) {
            Hal hal;
            GateKeeperProxy proxy{aidl ? &hal : nullptr, aidl ? nullptr : &hal};
            proxy.clear_state_if_needed();
            proxy.clear_state_if_needed();
            assert(proxy.markers == 1);
            assert(hal.all == (blocked ? 0 : 1));
            assert(proxy.clearSecureUserId(42).isOk());
            assert(proxy.local_sid_clears == 1);
            assert(hal.one == (blocked ? 0 : 1));
            permitted = false;
            proxy.clearSecureUserId(42);
            permitted = true;
            assert(proxy.local_sid_clears == 1 && hal.one == (blocked ? 0 : 1));
        }
        LegacyHal hal;
        KeyMintDevice device{&hal};
        const auto status = device.deleteAllKeys();
        assert(status.code == (blocked ? KMV1::ErrorCode::UNIMPLEMENTED : KMV1::ErrorCode::OK));
        assert(device.destroyAttestationIds().code == KMV1::ErrorCode::UNIMPLEMENTED);
        assert(hal.calls == (blocked ? 0 : 1));
    }
    guest.clear();
    Hal hal;
    GateKeeperProxy gsi{&hal, nullptr, true};
    gsi.clear_state_if_needed();
    assert(gsi.markers == 1 && hal.all == 0);
}
'''
    (work / "guards.cpp").write_text("\n".join([fixture, *native_methods, checks]))
    run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "guards.cpp", "-o", "native"], work)
    run(["./native"], work)
    print("PASS gatekeeperd AIDL/HIDL and C++ Keymaster compatibility", flush=True)


def test_rust(src, work):
    keystore = src / "system/security/keystore2/src"
    maintenance = (keystore / "maintenance.rs").read_text()
    compat = (keystore / "km_compat.rs").read_text()
    fixture = r'''
#![allow(non_snake_case)] // Android AIDL names
use std::cell::Cell;
thread_local! {
    static PROPERTY: Cell<Option<&'static str>> = const { Cell::new(None) };
    static PERMITTED: Cell<bool> = const { Cell::new(true) };
    static COUNTS: Cell<(usize, usize, usize)> = const { Cell::new((0, 0, 0)) };
}
#[derive(Debug, PartialEq)]
enum Error { PermissionDenied }
impl Error { fn perm() -> Self { Self::PermissionDenied } }
type Result<T> = std::result::Result<T, Error>;
trait Context { fn context(self, _: &str) -> Self; }
impl<T, E> Context for std::result::Result<T, E> { fn context(self, _: &str) -> Self { self } }
macro_rules! ks_err { ($msg:literal) => { $msg }; }
macro_rules! info { ($msg:literal) => {}; }
macro_rules! error { ($msg:literal) => { let _ = format_args!($msg); }; }
mod rustutils { pub mod android { pub mod system_properties {
    pub fn read(name: &str) -> crate::Result<Option<String>> {
        assert_eq!(name, "ro.skyward.guest");
        Ok(crate::PROPERTY.with(|v| v.get().map(str::to_owned)))
    }
}}}
mod binder {
    pub type Result<T> = std::result::Result<T, Status>;
    #[derive(Debug, PartialEq)]
    pub struct Status(pub i32);
    impl Status {
        pub fn new_service_specific_error(code: i32, _: Option<()>) -> Self { Self(code) }
    }
}
struct ErrorCode(i32);
impl ErrorCode {
    const UNIMPLEMENTED: Self = Self(-100);
    const UNKNOWN_ERROR: Self = Self(-1000);
}
enum KeystorePerm { DeleteAllKeys }
fn check_keystore_permission(_: KeystorePerm) -> Result<()> {
    if PERMITTED.get() { Ok(()) } else { Err(Error::perm()) }
}
struct Device;
impl Device {
    fn deleteAllKeys(&self) -> binder::Result<()> {
        let (a, b, c) = COUNTS.get(); COUNTS.set((a, b+1, c)); Ok(())
    }
    fn destroyAttestationIds(&self) -> binder::Result<()> {
        let (a, b, c) = COUNTS.get(); COUNTS.set((a, b, c+1)); Ok(())
    }
}
struct Maintenance;
impl Maintenance {
    fn call_on_all_security_levels(name: &str, f: impl Fn(&Device) -> binder::Result<()>,
                                   extra: Option<()>) -> Result<()> {
        assert_eq!(name, "deleteAllKeys"); assert!(extra.is_none());
        let (a, b, c) = COUNTS.get(); COUNTS.set((a+1, b, c));
        f(&Device).unwrap(); Ok(())
    }
'''
    compat_methods = [method(compat, rf"fn {name}\(", "    ")
                      for name in ("deleteAllKeys", "destroyAttestationIds")]
    checks = r'''
fn main() {
    for property in [Some("true"), Some("false"), None] {
        PROPERTY.set(property);
        COUNTS.set((0, 0, 0));
        PERMITTED.set(false);
        assert_eq!(Maintenance::delete_all_keys(), Err(Error::perm()));
        assert_eq!(COUNTS.get(), (0, 0, 0));
        PERMITTED.set(true);
        let maintenance = Maintenance::delete_all_keys();
        let compat = Wrapper { real: Device };
        let delete = compat.deleteAllKeys();
        let destroy = compat.destroyAttestationIds();
        if property == Some("true") {
            assert_eq!(maintenance, Err(Error::perm()));
            assert_eq!(delete, Err(binder::Status(ErrorCode::UNIMPLEMENTED.0)));
            assert_eq!(destroy, Err(binder::Status(ErrorCode::UNIMPLEMENTED.0)));
            assert_eq!(COUNTS.get(), (0, 0, 0));
        } else {
            assert_eq!((maintenance, delete, destroy), (Ok(()), Ok(()), Ok(())));
            assert_eq!(COUNTS.get(), (1, 2, 1));
        }
    }
}
'''
    pieces = [fixture, method(maintenance, r"fn delete_all_keys\(", "    "), "}",
              method(compat, r"fn reject_skyward_guest_destructive_operation\("),
              "struct Wrapper { real: Device }\nimpl Wrapper {", *compat_methods, "}", checks]
    (work / "guards.rs").write_text("\n".join(pieces))
    run(["rustc", "--edition=2021", "-Dwarnings", "guards.rs", "-o", "keystore"], work)
    run(["./keystore"], work)
    print("PASS Keystore maintenance and Rust KeyMint compatibility", flush=True)


def java_prefix(source, signature, legacy_statement, nested=False):
    # Keep the original signature and all statements up to the specified boundary.
    # The legacy body is replaced with a sentinel: no file, Binder, or reboot API exists here.
    body = method(source, signature, "    ")
    if body.count(legacy_statement) != 1:
        raise ValueError(f"legacy boundary moved in {signature!r}")
    return (body[:body.index(legacy_statement)] + "throw new ReachedLegacyPath();\n"
            + ("        }\n" if nested else "") + "    }\n")


def test_java(src, work):
    frameworks = src / "frameworks/base"
    client = (frameworks / "core/java/android/os/RecoverySystem.java").read_text()
    service = (frameworks / "services/core/java/com/android/server/recoverysystem/"
               "RecoverySystemService.java").read_text()
    # signature regex, first legacy statement, invocation, guest result (null means IOException)
    client_cases = [
        (r"public static void processPackage\(", "String filename =", "processPackage(null,null,null,null)", None),
        (r"public static void installPackage\(Context context, File packageFile, boolean", "synchronized (", "installPackage(null,null,false)", None),
        (r"public static void prepareForUnattendedUpdate\(", "if (updateToken", "prepareForUnattendedUpdate(null,null,null)", None),
        (r"public static void clearPrepareForUnattendedUpdate\(", "RecoverySystem rs =", "clearPrepareForUnattendedUpdate(null)", None),
        (r"public static void rebootAndApply\(", "if (updateToken", "rebootAndApply(null,null,\"\")", None),
        (r"public static boolean isPreparedForUnattendedUpdate\(", "RecoverySystem rs =", "isPreparedForUnattendedUpdate(null)", "false"),
        (r"public static @ResumeOnRebootRebootErrorCode int rebootAndApply\(", "RecoverySystem rs =", "rebootAndApply(null,\"\",false)", "1000"),
        (r"public static void scheduleUpdateOnBoot\(", "String filename =", "scheduleUpdateOnBoot(null,null)", None),
        (r"public static void cancelScheduledUpdate\(", "RecoverySystem rs =", "cancelScheduledUpdate(null)", None),
        (r"public static void rebootWipeUserData\([^{}]*boolean keepMemtagMode\)", "UserManager um =", "rebootWipeUserData(null,false,null,true,true,true)", None),
        (r"public static boolean wipeEuiccData\(", "ContentResolver cr =", "wipeEuiccData(null,null)", "false"),
        (r"public static void rebootPromptAndWipeUserData\(", "boolean checkpointing =", "rebootPromptAndWipeUserData(null,null)", None),
        (r"public static void rebootWipeAb\(", "String reasonArg =", "rebootWipeAb(null,null,null)", None),
        (r"public void wipePartitionToExt4\(", "String command =", "new RecoverySystem().wipePartitionToExt4()", None),
        (r"private static void bootCommand\(", "LOG_FILE.delete()", "bootCommand(null,\"--wipe_cache\")", None),
    ]
    service_cases = [
        (r"public boolean uncrypt\(", "if (!checkAndWaitForUncryptService()", "s.uncrypt(null,null)", "false"),
        (r"public void rebootRecoveryWithCommand\(", r'if (DEBUG) Slog.d(TAG, "rebootRecoveryWithCommand:', 's.rebootRecoveryWithCommand(command)', "unsupported"),
        (r"public boolean requestLskf\(", "if (packageName == null)", "s.requestLskf(null,null)", "false"),
        (r"public boolean clearLskf\(", "if (packageName == null)", "s.clearLskf(null)", "false"),
        (r"private @ResumeOnRebootRebootErrorCode int rebootWithLskfImpl\(", "RebootPreparationError escrowError =", "s.rebootWithLskf(null,null,true)", "1000"),
        (r"public boolean allocateSpaceForUpdate\(", "final long token =", "s.allocateSpaceForUpdate(null)", "false"),
        (r"public boolean isLskfCaptured\(", "boolean captured;", "s.isLskfCaptured(null)", "false"),
        (r"private boolean setupOrClearBcb\(", "final boolean available =", "s.setupBcb(command)", "false"),
    ]
    fixture = r'''
import java.io.File;
import java.io.IOException;

class Guards {
    static class ReachedLegacyPath extends RuntimeException {}
    static class SystemProperties {
        static String value;
        static String get(String key) {
            if (!key.equals("ro.skyward.guest")) throw new AssertionError(key);
            return value;
        }
    }
    @interface NonNull {}
    @interface Nullable {}
    @interface ResumeOnRebootRebootErrorCode {}
    static class android { static class Manifest { static class permission {
        static final String RECOVERY = "android.permission.RECOVERY";
    }}}
    static class Context {
        void enforceCallingOrSelfPermission(String name, String message) {
            if (!name.equals(android.Manifest.permission.RECOVERY)) throw new AssertionError(name);
            enforcePermission();
        }
    }
    static boolean permitted = true;
    static void enforcePermission() { if (!permitted) throw new SecurityException(); }
    static class Handler {}
    static class IntentSender {}
    static class Slog { static void d(String tag, String message) {} }
    interface ProgressListener {}
    interface IRecoverySystemProgressListener {}
    static final int RESUME_ON_REBOOT_REBOOT_ERROR_UNSPECIFIED = 1000;
    static final boolean DEBUG = false;
    static final String TAG = "test";
    static final Object sRequestLock = new Object();

    interface Action { void run() throws IOException; }
    static void expect(Action action, Class<? extends Exception> expected) throws IOException {
        try {
            action.run();
        } catch (Exception e) {
            if (expected.isInstance(e)) return;
            throw e;
        }
        throw new AssertionError("Expected " + expected.getSimpleName());
    }
    static void check(Object expected, Object actual) {
        if (!expected.equals(actual)) throw new AssertionError("Expected " + expected + ", got " + actual);
    }
    static class RecoverySystem {
'''
    pieces = [fixture, method(client, r"private static boolean isSkywardGuest\(", "    "),
              method(client, r"private static void enforceNotSkywardGuest\(", "    ")]
    for sig, stop, _, _ in client_cases:
        if sig == r"public static void processPackage\(":
            sig += r"[^{}]*final Handler handler\)"
        pieces.append(java_prefix(client, sig, stop))
    pieces.append("""    }
    static class RecoverySystemService {
        final Context mContext = new Context();
        final Injector mInjector = new Injector();
        static class Injector { String systemPropertiesGet(String key) { return SystemProperties.get(key); } }
        void enforcePermissionForResumeOnReboot() { enforcePermission(); }
        void allocateSpaceForUpdate_enforcePermission() { enforcePermission(); }
        void rebootWithLskfAssumeSlotSwitch_enforcePermission() { enforcePermission(); }
""")
    for sig, stop, _, _ in service_cases:
        pieces.append(java_prefix(service, sig, stop, nested=sig.startswith("public boolean uncrypt")))
    for sig in (r"private boolean isSkywardGuest\(", r"public boolean setupBcb\(",
                r"public boolean clearBcb\(",
                r"public @ResumeOnRebootRebootErrorCode int rebootWithLskf\(",
                r"public @ResumeOnRebootRebootErrorCode int rebootWithLskfAssumeSlotSwitch\("):
        pieces.append(method(service, sig, "    "))
    pieces.append(r'''
    }
    public static void main(String[] args) throws IOException {
        var s = new RecoverySystemService();
        SystemProperties.value = "true";
''')
    for _, _, call, result in client_cases:
        if result is None:
            pieces.append(f"expect(() -> RecoverySystem.{call}, IOException.class);"
                          if not call.startswith("new ") else f"expect(() -> {call}, IOException.class);")
        else:
            pieces.append(f"check({result}, RecoverySystem.{call});")
    pieces.append(r'''
        for (String command : new String[] {"--wipe_data", "--format_data", "--prompt_and_wipe_data",
                "--wipe_cache", "--wipe_ab", "--update_package=x", "--sideload", "--rescue", "", null}) {
            check(false, s.setupBcb(command));
            expect(() -> s.rebootRecoveryWithCommand(command), UnsupportedOperationException.class);
        }
        check(false, s.clearBcb());
        check(1000, s.rebootWithLskfAssumeSlotSwitch(null, null));
        permitted = false;
        expect(() -> s.setupBcb("--wipe_data"), SecurityException.class);
        expect(() -> s.rebootRecoveryWithCommand("--wipe_data"), SecurityException.class);
        permitted = true;
''')
    for _, _, call, result in service_cases:
        if "command" not in call:
            pieces.append(f"check({result}, {call});")
    pieces.append('for (String value : new String[] {"", "false"}) {\n'
                  'SystemProperties.value = value;\nString command = "--wipe_data";')
    for _, _, call, _ in client_cases:
        target = call if call.startswith("new ") else f"RecoverySystem.{call}"
        pieces.append(f"expect(() -> {target}, ReachedLegacyPath.class);")
    for _, _, call, _ in service_cases:
        pieces.append(f"expect(() -> {call}, ReachedLegacyPath.class);")
    pieces.append("}\n}\n}")
    (work / "Guards.java").write_text("\n".join(pieces))
    run(["java", "Guards.java"], work)
    print("PASS RecoverySystem framework and Binder entry guards", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[2] / "uwuAOSP-src")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="p4.3-platform-") as directory:
        work = Path(directory)
        test_native(args.source, work)
        test_rust(args.source, work)
        test_java(args.source, work)


if __name__ == "__main__":
    main()
