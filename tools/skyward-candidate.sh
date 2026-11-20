#!/usr/bin/env bash
# Candidate integration only; does not flash, run guests, or remove releases.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ws=$root
export PYTHONDONTWRITEBYTECODE=1

usage() {
    cat >&2 <<'USAGE'
usage:
  skyward-candidate.sh guest EVIDENCE_DIR                       # must not exist
  skyward-candidate.sh pack DEVICE REV EVIDENCE_DIR FIVE_IMAGES_SHA256
  skyward-candidate.sh readback DEVICE REV EVIDENCE_DIR
Pack takes the SHA-256 records for the five just-built images, in sha256sum format.
USAGE
    exit 2
}

rust_inputs() (
    cd "$ws"
    { printf '%s\0' Cargo.toml Cargo.lock rust-toolchain.toml; find crates guest-entry profiles -type f -print0; } |
        LC_ALL=C sort -z | xargs -0 sha256sum
)

guest() (
    run=$1
    mkdir -- "$run"
    run=$(realpath -e -- "$run")
    echo "Guest evidence: $run"
    cd "$ws"
    : "${ANDROID_NDK_HOME:?select the pinned Android NDK}"
    test -x "$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang"
    unset RUSTFLAGS CARGO_ENCODED_RUSTFLAGS
    rust_inputs > "$run/rust-inputs.sha256"
    {
        set -x
        rustc --version
        cargo --version
        export CARGO_TARGET_DIR="$ws/target/guest-entry-offline"
        RUSTFLAGS='-C target-feature=+crt-static' cargo ndk -t arm64-v8a --platform 30 \
            build --locked --offline --release -p skyward-guest-entry
        cp -- "$CARGO_TARGET_DIR/aarch64-linux-android/release/guest-entry" "$run/guest-entry"
        : "${SKYWARD_PACKAGE_CHECK:?provide the locked recovery package checker}"
        install -m 0755 -- "$SKYWARD_PACKAGE_CHECK" "$run/skyward-package-check"
        readelf -W -h -l -d "$run/guest-entry"
    } 2>&1 | tee "$run/guest-build.log"
    rust_inputs | cmp -- "$run/rust-inputs.sha256" -
    (cd "$run"; sha256sum guest-entry skyward-package-check > guest.sha256)
)

pack() (
    # Same lock as the external five-image driver, held across readback.
    flock -x 9
    exec python3 "$root/tools/skyward-candidate.py" pack "$@"
) 9<"${SKYWARD_BUILD_LOCK:?set to the external build lock}"


case ${1-} in
    guest) [[ $# == 2 ]] || usage; guest "$2";;
    pack) [[ $# == 5 ]] || usage; pack "${@:2}";;
    readback) [[ $# == 4 ]] || usage; exec python3 "$root/tools/skyward-candidate.py" "$@";;
    *) usage;;
esac
