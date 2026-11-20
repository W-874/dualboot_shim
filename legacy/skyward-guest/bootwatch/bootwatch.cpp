// SPDX-License-Identifier: Apache-2.0
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>

#include <android-base/file.h>
#include <android-base/logging.h>
#include <android-base/properties.h>
#include <android-base/unique_fd.h>

namespace {
using android::base::ReadFullyAtOffset;
using android::base::SetProperty;
using android::base::unique_fd;
using android::base::WaitForProperty;
using android::base::WriteFullyAtOffset;
using namespace std::chrono_literals;

constexpr char kMisc[] = "/dev/block/by-name/misc";
constexpr char kSwitchRequest[] = "vendor.skyward.switch_requested";
constexpr char kRecoveryReady[] = "vendor.skyward.recovery_ready";
constexpr auto kBootTimeout = 8min;

enum class RecoveryRequest { BootTimeout, SwitchToInstalled };

bool WriteRecoveryRequest(RecoveryRequest request) {
    // by-name is a symlink; ueventd labels the underlying misc inode using
    // the controller and PARTNAME, independently of the path used to open it.
    unique_fd fd(TEMP_FAILURE_RETRY(open(kMisc, O_RDWR | O_CLOEXEC | O_NONBLOCK)));
    struct stat st = {};
    if (fd < 0 || fstat(fd.get(), &st) != 0) return false;
    if (!S_ISBLK(st.st_mode)) {
        errno = ENOTBLK;
        return false;
    }

    // skyward-sys::bcb::write is a read-modify-write of offset zero. Keep
    // status, stage, reserved and every later misc region byte-for-byte.
    struct BootloaderMessage {
        char command[32];
        char status[32];
        char recovery[768];
        char stage[32];
        char reserved[1184];
    } message;
    static_assert(sizeof(BootloaderMessage) == 2048);
    constexpr char kCommand[] = "boot-recovery";
    constexpr char kTimeout[] =
            "recovery\n--system_stopped\n--reason=android did not finish booting within 8 minutes\n";
    constexpr char kSwitch[] = "recovery\n--switch_system=installed\n";
    if (!ReadFullyAtOffset(fd, &message, sizeof(message), 0)) return false;
    memset(message.command, 0, sizeof(message.command));
    memset(message.recovery, 0, sizeof(message.recovery));
    memcpy(message.command, kCommand, sizeof(kCommand));
    if (request == RecoveryRequest::BootTimeout) {
        memcpy(message.recovery, kTimeout, sizeof(kTimeout));
    } else {
        memcpy(message.recovery, kSwitch, sizeof(kSwitch));
    }
    return WriteFullyAtOffset(fd, &message, sizeof(message), 0) &&
           TEMP_FAILURE_RETRY(fsync(fd.get())) == 0;
}

// Set the readiness flag only once the BCB is on disk. Without it init
// must not perform the requested recovery reboot on the watchdog's behalf.
int Recover(RecoveryRequest request) {
    if (!WriteRecoveryRequest(request)) {
        PLOG(ERROR) << "Unable to commit Skyward recovery request to misc; not rebooting";
        return 1;
    }
    if (!SetProperty(kRecoveryReady, "1")) {
        LOG(ERROR) << "Skyward BCB committed, but failed to ask init to reboot";
        return 1;
    }
    return 0;
}

bool ReadBootTime(std::chrono::milliseconds* time) {
    struct timespec ts = {};
    if (clock_gettime(CLOCK_BOOTTIME, &ts) != 0) return false;
    *time = std::chrono::seconds(ts.tv_sec) +
            std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::nanoseconds(ts.tv_nsec));
    return true;
}
}  // namespace

int main(int argc, char** argv) {
    (void)argc;
    android::base::InitLogging(argv);
    std::chrono::milliseconds now;
    if (!ReadBootTime(&now)) {
        PLOG(ERROR) << "Reading watchdog clock";
        return 1;
    }
    const auto deadline = now + kBootTimeout;
    // Measure since service start (Android's init event) using a clock that
    // includes suspend. Bound each property wait to recheck that clock.
    while (!WaitForProperty("sys.boot_completed", "1", 0ms)) {
        if (!ReadBootTime(&now)) {
            PLOG(ERROR) << "Reading watchdog clock";
            return 1;
        }
        if (now >= deadline) return Recover(RecoveryRequest::BootTimeout);
        if (WaitForProperty("sys.boot_completed", "1", std::min(deadline - now, 1000ms))) break;
    }
    // The tile is useful after a successful boot; never let it replace a
    // pending timeout request before boot completion.
    if (!WaitForProperty(kSwitchRequest, "1")) {
        LOG(ERROR) << "Waiting for the Skyward switch request failed";
        return 1;
    }
    return Recover(RecoveryRequest::SwitchToInstalled);
}
