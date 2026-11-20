# 进展看门狗与 BCB

- `system-init` 前段看门狗负责启动器自身；`guest-entry` 使用 `CLOCK_BOOTTIME` 和阶段/实际 I/O 进展，连续 **300 s 无进展**才请求回退。历史固定 600 s 曾误杀在慢速全量哈希/全零扫描上仍有进展的启动（r26b）；无进展不等于总耗时。Android 交接后的 `bootwatch` 是另一个历史组件，r26e 产品不安装。
- 两个 PID 1 阶段各自预置后还原。guest-entry 核验继承的 misc；BCB 空时保存原字段，预置 `boot-recovery`、`--system_stopped` 及 reason，同步后才继续，exec 前恢复并同步。只改 2048 字节 BCB 的 command/recovery 区段，分别核对其余 BCB 和周边范围；已有可识别 recovery 请求不覆盖。其它 occupied BCB 目前跳过预置，后续失败仍可能写回退；移植须单独审核、收紧未知占用与失败路径。
- fsync 错误不能当作回退已持久；exec 返回重新走主线程失败路径，不能由 watchdog 与交接分别宣称成功。僵死 I/O/内核故障不保证可以写 BCB 或重启。

`WatchdogLogDesign.md`（历史提纲）和 `evidence/{r26b,r26c}/REPORT.md`：r26c S4 的证据链是串口停滞 → BCB reason → recovery 请求；宿主 timeout 退出码不能代替这一证据。旧 r25 harness 600 s 判据不用于 r26c。
