#!/usr/bin/env bash
# P5.3: build from the frozen LineageOS e2fsprogs 1.47.2 fork, not an AOSP OUT.
set -euo pipefail
export LC_ALL=C TZ=UTC
umask 022

usage() {
    cat <<'EOF'
Usage: bash tools/build-static-e2fsprogs.sh OUTPUT_DIR
OUTPUT_DIR must not exist; its parent must exist. On success, consume bin/ under it.
Linux x86_64 host; needs Git, GNU make, GCC, standard shell tools and file/readelf.
Optional environment: ANDROID_NDK_HOME (must be r29/29.0.14206865),
E2FSPROGS_SOURCE (Git checkout with the frozen commit), E2FSPROGS_JOBS (default 4).
EOF
}

die() { printf 'P5.3: %s\n' "$*" >&2; exit 1; }

if [[ $# == 1 && ($1 == -h || $1 == --help) ]]; then usage; exit 0; fi
if [[ $# != 1 || -z $1 ]]; then usage >&2; exit 2; fi
[[ $(uname -s):$(uname -m) == Linux:x86_64 ]] || die 'Linux x86_64 required'

# Source: https://github.com/LineageOS/android_external_e2fsprogs
# lock/frozen-r5/manifest.xml, external/e2fsprogs. SHA is for the exact
# uncompressed git archive below (including its prefix), not an upstream release.
readonly commit=347a1592a840e193b9c52674227d72c9c3cc1a69
readonly tar_sha256=4dd0212301fd870abacdf7f50e98b8a7166998185a94f0fca3620fd9b8ce0766
readonly ndk_revision=29.0.14206865
readonly api=30

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
repo=${E2FSPROGS_SOURCE:-"$root/uwuAOSP-src/external/e2fsprogs"}
ndk=${ANDROID_NDK_HOME:-"${ANDROID_HOME:-$HOME/Android/Sdk}/ndk/$ndk_revision"}
jobs=${E2FSPROGS_JOBS:-4}
[[ $jobs =~ ^[1-9][0-9]*$ ]] || die 'E2FSPROGS_JOBS must be a positive integer'
[[ -r $ndk/source.properties ]] || die "missing NDK at $ndk"
revision=$(awk -F ' *= *' '$1 == "Pkg.Revision" {print $2}' "$ndk/source.properties")
[[ $revision == "$ndk_revision" ]] || die "need NDK $ndk_revision; got $revision"
ndk=$(realpath -e -- "$ndk")
llvm="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
cc=aarch64-linux-android${api}-clang
for name in "$cc" "$cc++" llvm-ar llvm-ranlib llvm-strip; do
    [[ -x $llvm/$name ]] || die "missing NDK tool: $llvm/$name"
done
source_epoch=$(git --no-replace-objects -C "$repo" show -s --format=%ct "$commit^{commit}")

out=$(realpath -m -- "$1")
mkdir -- "$out" || die "need a new output directory with an existing parent: $out"
work=$(mktemp -d -- "$out/.work.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
archive=e2fsprogs-source.tar
git --no-replace-objects -C "$repo" -c tar.umask=0002 archive \
    --format=tar --prefix=e2fsprogs/ "$commit" >"$out/$archive"
(cd -- "$out" && printf '%s  %s\n' "$tar_sha256" "$archive" | sha256sum -c -)
tar -xf "$out/$archive" -C "$work"
mkdir -- "$work/build"

printf 'P5.3: building e2fsprogs at %s (log: %s/build.log)\n' "$commit" "$out"
if ! (
    cd -- "$work/build" && env -i PATH="$llvm:/usr/bin:/bin" HOME="$work" \
        LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH="$source_epoch" CONFIG_SITE=/dev/null \
        PKG_CONFIG_LIBDIR=/dev/null bash -e -u -x -s -- "$cc" "$jobs" <<'EOF'
cc=$1 jobs=$2
"$cc" --version
/usr/bin/gcc --version
make --version
# BUILD_CC is for generators that must run on the build host. All target
# probes, objects and links go through the NDK, including static Bionic.
CC="$cc" CXX="$cc++" BUILD_CC=/usr/bin/gcc BUILD_CFLAGS=-O2 \
AR=llvm-ar RANLIB=llvm-ranlib STRIP=llvm-strip CFLAGS=-O2 \
LDFLAGS='-static -Wl,-z,max-page-size=16384' \
    ../e2fsprogs/configure \
    --build="$(../e2fsprogs/config/config.guess)" --host=aarch64-linux-android \
    --prefix=/usr --sysconfdir=/etc \
    --disable-elf-shlibs --disable-bsd-shlibs --disable-nls --disable-uuidd \
    --enable-libuuid --enable-libblkid --disable-fuse2fs --without-libarchive
make -j"$jobs" libs
make -j"$jobs" -C resize resize2fs.static
make -j"$jobs" -C e2fsck e2fsck.static
make -j"$jobs" -C misc mke2fs.static
EOF
) >"$out/build.log" 2>&1; then
    if [[ -f $work/build/config.log ]]; then cp -- "$work/build/config.log" "$out/"; fi
    die "build failed; see $out/build.log"
fi

# Keep rejected or partial outputs out of bin/. Check both the interpreter and
# needed libraries independently (static PIE may contain a dynamic section).
mapping=(resize/resize2fs e2fsck/e2fsck misc/mke2fs)
mkdir -- "$work/bin"
{
    for target in "${mapping[@]}"; do
        name=${target##*/}
        binary="$work/bin/$name"
        install -m 0755 -- "$work/build/$target.static" "$binary"
        description=$(file -b -- "$binary")
        printf '%s: %s\n' "$name" "$description"
        [[ $description == 'ELF 64-bit LSB '* && $description == *executable* &&
            ($description == *'statically linked'* || $description == *'static-pie linked'*) ]] ||
            die "$name is not a static ELF64 executable"
        header=$(readelf -hW "$binary")
        segments=$(readelf -lW "$binary")
        dynamic=$(readelf -dW "$binary")
        printf '%s\n%s\n%s\n\n' "$header" "$segments" "$dynamic"
        grep -Eq '^[[:space:]]*Machine:[[:space:]]+AArch64[[:space:]]*$' <<<"$header" ||
            die "$name is not AArch64"
        if grep -Eq '^[[:space:]]*INTERP[[:space:]]' <<<"$segments" ||
            grep -Fq '(NEEDED)' <<<"$dynamic"; then
            die "$name has a runtime ELF dependency"
        fi
    done
} >"$out/elf.txt"
mv -T -- "$work/bin" "$out/bin"
(cd -- "$out" && sha256sum "$archive" bin/{resize2fs,e2fsck,mke2fs} > SHA256SUMS)
printf 'P5.3: %s/bin/{resize2fs,e2fsck,mke2fs}\n' "$out"
