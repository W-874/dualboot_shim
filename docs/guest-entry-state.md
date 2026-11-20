# guest-entry 状态机

```text
root/PID 1，继承 misc，预置回退
    → 验证继承环境、设备与参数
    → 条件扩 root → 持久化私有卷 → 尝试归档日志
    → 保护宿主分区 → 首次全量校验 / 后续抽查，挂载 ROM
    → first-stage → 挂载私有卷 → 安装清洗后的参数
    → 查格式授权 → 绑定 guarded fstab → durable marker / 清理 / 日志
    → 切根 → 同步还原 BCB → exec Android init
```

`volumes-ready` 是私有卷 provision 完成的持久标记；不代表 `/data` 已加密或可用。`probe-ok` 记录一次完整 ROM 校验，不代表 Android boot complete；此后只抽查，错误不能用删除标记掩盖。marker 采用原子替换和同步；掉电可能落在两者之间，重入仍验证既有文件、卷和必要的授权状态。`/volumes/state/.handoff` 只清理固定 scratch 名称，不触碰宿主数据。

内存阶段/进度与持久 marker 不同。错误、panic 和无进展看门狗共用一个 recovery 状态仲裁；exec 前 CAS 排除并发失败请求，exec 返回只走主线程失败出口。详见 [BCB](recovery-watchdogs-bcb.md)、[格式授权](format-authorization.md)。

摘录原 `R26CodeReview.md` 的审查边界：静态审查和故障注入能确认预期分支、持久顺序和复位竞争，不是实机开机完成证明；详见 `guest-entry/src/{boot,state,storage}.rs` 和 r26c S1–S5。
