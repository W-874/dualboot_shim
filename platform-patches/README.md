# Android 平台补丁档案

[r26e/series.tsv](r26e/series.tsv) 每行都是独立 Android Git 项目，不能在 AOSP 根一次 apply。先在**隔离 checkout** 确认该项目 base 和补丁 SHA256，再在干净 base `git apply --check`；保留准确的冻结行为，不把它误当成可发布的新机移植版。

| 项 | 行为与组合边界 |
|---|---|
| fs_mgr + vold | 私有 userdata 失败不得回退明文 mkfs；vold 查验和消费 v2 token。历史 vold 门控仍含 `ro.product.device=fuxi`，且最后一个失败传播 hunk 影响非 guest；不能直接在新机启用。与 [格式授权](../docs/format-authorization.md)、guest-entry 和 fstab 一起审。 |
| gatekeeperd | BP/C++ 成对；`skyward.guest_gatekeeper=true` 编译 guest 专用路径，偏移 +2,000,000；没有可信后端时不发现共享 HAL，不回退原 UID。普通 DSU 仍 +1,000,000；冻结代码提前读取 GSI 属性，故不是普通变体零影响。旧 P4.3 属性拦截只作历史对照。 |
| keystore2 | maintenance、Rust/C++ compat 三处防止 guest 破坏共享状态；Rust 属性读取失败也会改变普通异常路径。 |
| RecoverySystem | API + Binder 两层拒绝 guest 恢复/OTA/LSKF 写入口；不能只保留一层。 |
| build/make、build/soong、vendor/uwu | guest 包裁剪、历史 zygote 接线、root-dir bridge（残留，非现有产品必需）与 vendor guest 过滤；后两项目按文件提取 S 类，不含通用 depfile/UAPI/kernel 修复或独立 DSU 实验。新产品不要盲套旧接线。 |

来源：[P4.3 平台入口审计](evidence/P4.3/PLATFORM.md)、[r26e 报告](evidence/r26e/REPORT.md)、原 `evidence/r26e/build/source.diff` 与 `lock/frozen-r26e/{git-projects.json,patches/}`。原证据中的路径按工作区解释。SELinux/ueventd、bootwatch/tile 历史见 [legacy](../legacy/README.md)，不要叠到现役补丁；Softkm 不在发布 series。新组合必须检查 host/guest 实际安装件、普通非 guest 及 DSU 对照；未执行 Android 平台构建或实机验证。
