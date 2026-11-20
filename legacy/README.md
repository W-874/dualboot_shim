# 仅作追溯

- `skyward-guest/`：原 `device/xiaomi/skyward-guest` 完整非 Git 源目录，来自 live tree 与 `dualboot-src` 快照。`init.secure-hardware.rc` 是 **standalone** 输入，不能并入 guest；bootwatch/tile 源仍在，但冻结 guest 已不安装。不要直接在 Android 扫描路径内放这份目录。
- `policy/`：`device/xiaomi/skyward/sepolicy/vendor` 在 r26e 冻结清单只有目录、没有策略文件。保留空树清单、旧锁的 core / SELinux 增量和 P4.5/回滚报告作为历史资料；旧锁不是最终安装件，也不可整套应用。
- `c-prototype/`：`uwuAOSP-dada/skyward-guest` 的 4 个 C 文件来自 `dualboot-src` 快照；配套 shell 为 live 原件。仅追溯，不运行在当前宿主盘或手机上；脚本中的路径、输入及行为属于历史实验。
