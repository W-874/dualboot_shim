# 格式化授权与掉电重入

`FormatWedgeRCA.md` 要点：vold 可能已消费 token、已留下 metadata key，随后未完成加密格式化；下次无 token 但 raw userdata 仍全零。这不是删除或重建 key 的理由。v2 只对经确认的私有 guest raw 卷进行全零证明，校验私有 fd、设备、权限和标签，先持久化 `trusted.skyward.mkfs: new-v2 → delegated-v2`，再在 guest `/metadata/vold` 原子发布 `skyward-mkfs-v2`。写入、文件/目录 fsync 的顺序用于保证重启后 token 和 marker 可以区分，而不是把 fstab 当授权。

| 原状态 | 只读观察 | 动作 |
|---|---|---|
| `new-v2` | 全零、无 token | 持久委托并发布 token |
| `delegated-v2` | 全零、合法 token | 保留授权（`delegated`） |
| `delegated-v2` | 全零、无 token | `reissued`，保留已有 key |
| `delegated-v2` | 非零、无 token | 拒绝格式化 |
| 其它冲突（含新卷非零或 token/marker 不一致） | — | 出错退出 |

`guest-entry` 只给符合协议的 `/dev/block/skyward/userdata` 保留 `formattable`；已有 metadata 由前段处理。vold 负责查验并消费 token，fs_mgr 禁止失败后回退明文 mkfs。显式 `legacy-v1` 仅为 dada 历史组合，不能作为 v2 失败兜底。测试使用 `user.*` xattr，不能证明 Android 的 `trusted.*` 权限和 SELinux 策略。

来源：`evidence/r26c/REPORT.md` S3 只证明到 `reissued` 及停盘上有 key 文件；没有证明后续格式化/挂载成功或实际使用旧 key。报告应分开记录委托、消费、mkfs、挂载和 key 复用。
