# 打包与只读校验工具

- `fstab-lint.py --profile ... --fstab ... --charger ...`，或用 `--render` 生成产品输入（Python 3.11+）。`p7-fstab-check.py --profile ...` 可从 vendor image/ZIP 回读并调用同一 lint。
- `mk-skyward-guest.sh` / `skyward_guest_pack.py` 是现有锁与独立宿主凭据校验器；`skyward-candidate.py` 保留历史 Host-A 对照，不是通用新机打包器。两者仍限制历史 reader 支持的设备及包 ABI。正常发布路径保留归档的 `RELEASE-HOLD`，默认拒绝，不提供发布绕路。`--offline-test` 只用于隔离回放，不是发布通过。
- 显式输入：`SKYWARD_EVIDENCE_ROOT` 指受控历史输入根（保留 `evidence/{P4.6,P5.1}` 及 `deliver/` 原内部布局）；`SKYWARD_STATIC_E2FS_DIR` 指已批准的静态 helper 目录（含 `SHA256SUMS` 和 `bin/`）；`SKYWARD_ANDROID_SRC` 是正式 build record 解析 `OUT_DIR` 的 AOSP 树。历史 candidate 使用 `SKYWARD_ANDROID_OUT`、`SKYWARD_HOST_A`（原 host-a.txt）、`SKYWARD_PREVIOUS_CANDIDATE`（原 members.sha256 + system.toml 目录）；shell 包装器还要 `SKYWARD_BUILD_LOCK`。无旧 checkout 的用户名、下载路径或随机 OUT 默认值。来源与版本见 [证据索引](../docs/evidence-index.md)。构建/冻结 Android 的历史驱动留在外部，不从此仓启动 Android 全量 build。
- `SKYWARD_PACKAGE_CHECK` 指向使用**同版真实 recovery reader**构建的可执行文件，不用 ZIP 结构校验替代解析器。以 `d8d5bda21352a5c16ef5c70c9beac8bda61c665d` 为起点，恢复原回放用 `skyward-systems` 工作树增量，固定受控恢复点（仅 SHA 不足以复原当时的未提交改动）。用临时 staging 避免把恢复源码或链接提交到归档：

  ```sh
  # 从本仓根；RECOVERY_SRC 是已核验的同版源码绝对路径。
  build=$(mktemp -d)
  cp -a tools/skyward-package-check/{Cargo.toml,Cargo.lock,src} "$build/"
  ln -s "$RECOVERY_SRC" "$build/recovery"
  cargo build --locked --manifest-path "$build/Cargo.toml"
  export SKYWARD_PACKAGE_CHECK="$build/target/debug/skyward-package-check"
  ```

`build-static-e2fsprogs.sh` 是 P5.3 原构建脚本。实际打包需受控的功能验证记录和二进制摘要，仓库不含这些 helper 的二进制。`verify-heritage.py` 比较 `.heritage-snapshot` 中的原始归档与当前树，并报告逐文件差异。
