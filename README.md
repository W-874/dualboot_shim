# Skyward dualboot shim

这是 Skyward recovery 到 Android guest 之间的技术遗产归档：PID 1 交接、私有卷、平台补丁、产品模板和只读回放，不是独立 Android ROM、通用刷机包或 fuxi 单系统的构建依赖。**共享 TEE 上同 UID 登记可能破坏宿主解锁，先读 [事故教训](docs/lessons-shared-tee.md)。**

## 独立构建 guest-entry

Rust `1.100.0`、`cargo-ndk 4.1.2`、Android NDK r29 `29.0.14206865`；预装 `aarch64-linux-android` std。用仓库 `rust-toolchain.toml`（或直接将同版固定工具链置于 `PATH`）。从本仓根执行：

```sh
cd guest-entry
unset RUSTFLAGS CARGO_ENCODED_RUSTFLAGS
cargo test --locked

: "${ANDROID_NDK_HOME:?set the pinned NDK r29 path}"
export ANDROID_NDK_HOME
RUSTFLAGS='-C target-feature=+crt-static'   cargo ndk -t arm64-v8a --platform 30 build --locked --release
elf="${CARGO_TARGET_DIR:-../target}/aarch64-linux-android/release/guest-entry"
file "$elf"
readelf -h -l -d "$elf"
sha256sum "$elf"
```

主机测试需 Linux 与支持 `user.*` xattr、`O_TMPFILE` 的临时文件系统，不需 root。Rust 本地依赖只有仓内镜像 `crates/skyward-{block,sys}`，不消费 Android、手机镜像或外部 recovery checkout；crates.io 依赖用本仓锁解析，离线环境先准备 cache。Android 要 Bionic arm64 静态 ELF（无 `PT_INTERP`/`DT_NEEDED`），不能拿 Linux musl 替代 PID 1。实际运行需要另行装配匹配的 system-init、宿主构件和静态 ext4 helper。独立 `tools/skyward-package-check` 使用原版外部 recovery reader，构建步骤见 [tools](tools/README.md)。

## 导航与移植

- [从 recovery 到 Android 的启动链](docs/boot-chain.md) · [状态机](docs/guest-entry-state.md) · [格式授权](docs/format-authorization.md)
- [无进展看门狗与 BCB](docs/recovery-watchdogs-bcb.md) · [pstore](docs/logs-pstore.md) · [启动参数](docs/boot-parameters.md)
- [去机型化进度和移植清单](docs/porting.md) · [补丁基线](platform-patches/README.md) · [产品模板](product-template/README.md)
- [QEMU 局限](docs/validation-limits.md) · [历史回放](harness/ARCHIVE.md) · [原型与撤回策略](legacy/README.md)
- [来源和取证说明](docs/evidence-index.md) · [逐文件快照比较](docs/snapshot.md)
