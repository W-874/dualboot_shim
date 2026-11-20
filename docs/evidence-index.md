# 证据索引

[provenance.tsv](provenance.tsv) 记录源工作区的位置和导入层；Android repo 基线及摘要见 [series](../platform-patches/r26e/series.tsv)。本仓只有源码、文本夹具及报告，不复制镜像、专有固件、用户态数据、已撤回发布物或恢复仓库中的其它脏修改。原 `lock/frozen-r26e/`、`.heritage-snapshot/`、donor、Android Git 祖先与 recovery 分支/未提交增量仍在外部保全；未另造全工作区 bundle 或伪造非 Git 树历史。历史报告里的外部路径当作定位，不要求 clone 此仓后直接可打开。

设计原 `ShimArchiveDesign.md` 和 `DualbootBoundary.md` 的关键结论见 [移植](porting.md)、[快照对照](snapshot.md) 与 [平台边界](../platform-patches/README.md)。下列会话调研结论已摘录入仓，不依赖会话存档：

| 调研源 | 入仓摘录位置 |
|---|---|
| `HostUnlockDeps.md`、`DecryptFlowMap.md`、`GuestGkOffsetFix.md` | [共享 TEE 教训](lessons-shared-tee.md) |
| `R26CodeReview.md` | [状态机](guest-entry-state.md) |
| `FormatWedgeRCA.md` | [格式授权](format-authorization.md) |
| `WatchdogLogDesign.md` | [BCB/watchdog](recovery-watchdogs-bcb.md)、[pstore](logs-pstore.md) |
| `BootParamAudit.md` | [启动参数](boot-parameters.md) |
| `QemuRunbook.md` | [QEMU 局限](validation-limits.md)、[回放](../harness/ARCHIVE.md) |
