# 日志与 pstore

本轮 guest-entry kmsg 与上轮内核 pstore 分开归档。只读复用已挂载 pstore 或临时只读挂载，只卸载自己创建的挂载。只读已有的 `console`、`pmsg`、`dmesg` 记录；最多 16 条，单条 2 MiB，合计 4 MiB。有界归档并原子发布、轮换 `pstore`/`pstore.previous`，不删 pstore 原始记录。缺后端或导出失败不是启动失败；不能靠 enforcing 后常驻日志进程绕过策略。

摘录 `WatchdogLogDesign.md`：应保留跨重启证据而不是把「pstore 可挂载」当作已经持久保留过日志。`evidence/r26c/REPORT.md` S5 同时有 BCB 日志匹配误报与 QEMU 缺 pstore 后端，二者不要混为一谈。手机注册 ramoops 也不是跨重启日志证据。历史导出接口见 `evidence/P3.3/REPORT.md`。
