# 归档时的验证记录

- Rust 1.100.0 `cargo test --locked`（`guest-entry/`）：**54 passed**，含原 52 项及两个 profile 回归。故障夹具的 pstore I/O 日志是预期测试输入。
- `cargo-ndk 4.1.2` + NDK `29.0.14206865`、arm64-v8a/API 30：静态 release 构建通过，`readelf` 显示 AArch64、无 `PT_INTERP`、没有动态段或 `DT_NEEDED`。clang 提示 `-no-pie` 参数未使用；本次 ELF SHA-256 `e6f47e1e4193348ce34eb5c15bfab16725910881fcd249cacd1418a3963a3228`，不是历史发布件。
- 快照 SHA 和原始提交逐文件相等，整理树为 13 不变 / 5 修改 / 1 新增 / 0 删除，见 [对照](snapshot.md)。
- 三份 TOML 全部可生成/校验 fstab，历史两机输入也通过 lint。工具及回放 Python CLI smoke、语法检查与 tools shell 语法检查通过。
- r26e 的 8 个补丁在从列明 base 取出的干净目标文件上均通过 `git apply --check` 和摘要验证。checker 以外置 recovery 工作树及锁在临时 staging 编译通过（不代表包或镜像验证）。

未做 AOSP 全量/设备产品 build、QEMU 执行、真机或凭据试验。历史 r25/r26 结果不当作本次结果。
