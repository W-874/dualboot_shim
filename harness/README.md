# fuxi / r26：离线 QEMU 孪生场景

本流水线只用于 `virt` / TCG。不连接手机、ADB、宿主块设备或网络；不写 Android OUT、不写 `deliver/`。`softkm` 变体、S3 测试密钥、S4 桩均不可用于真机。

从项目根执行一条命令（省略 `--scenarios` 同样运行全部）：

```bash
python3 -u evidence/r26-qemu/harness/run.py /绝对路径/guest-entry softkm \
  --out qemu-r26-01 --scenarios S1 S2 S3 S4 S5
```

- 第一个参数是静态 AArch64 ELF；第二个参数为 `official` 或 `softkm`。`--out` 是 `evidence/r26-qemu/` 下的新目录；同名目录不覆盖。选择 S2/S3 自动先跑 S1，选择 S5 自动先跑 S1/S2。只跑 S4 不需要 S1。执行失败或断言不成立时保留证据并继续独立场景；空间不足立即停止。
- `official` 使用现存 fuxi OUT 和 r25 五图批准 SHA。默认 `softkm` 要求 `evidence/qemu-softkm/REPORT.md`、`vendor.img`、`SHA256SUMS`，只替换 vendor：其余镜像和策略仍核对原套件。完整不同的相配镜像集用 `--android-out <绝对路径> --five-images <同一构建的五图SHA清单>`。不允许用现算的哈希掩盖漂移。
- `--qemu` 或环境变量 `QEMU` 可选；默认 PATH 上的 `qemu-system-aarch64` 或私有 Fedora QEMU。需要 Python ≥3.11、仓库离线 Rust 依赖、`qemu-img`、`qemu-io`、GNU coreutils / timeout、7z 和宿主 e2fsprogs；S4 用 `--clang` 指定 NDK 的 `aarch64-linux-android30-clang`（默认 r29）。`--system-init` 与 `--system-init-sha256` 可共同覆盖固定的 release `system-init`。默认 4 vCPU / 4096 MiB。打包、VM 启动前分别把 `df -B1` 记到 `df.log`，低于 100 GiB 停止；大型临时文件用 evidence 同盘，不落 `/tmp`。
- 输出 `package/*-test.zip{,.sha256}`，含 rootfs，打包和 prepare 均调用真实的 Skyward `package::read`。`work/twin` 按 P6.1 写入 GPT、area、状态及保护范围；基盘只读。用 `Start` 和 release `system-init` 拼接同盘 `init_boot`，抽取 Skyward ramdisk 并前置已校验的 USB ramdisk prefix。QEMU 用 ZIP 内原样 AArch64 `Image` 和 xHCI USB 磁盘，**不是**历史 virtio GKI。各 SHA 见 `package/fuxi/` 和 `work/{twin,kernel}/SHA256SUMS`。
- S1–S3 宿主截止 official 750 s / softkm 1200 s，S4 900 s（必须大于 guest 600 s）；TCG 慢时用 `--limit` 和 `--watchdog-limit` 延长。已经准备好的目录可用 `run.py --prepared evidence/r26-qemu/<目录> --scenarios ...` 运行未做的场景；已有完整判定的场景不覆盖。中断但未完成的场景不尝试续用半成品盘。

## 判定

| 场景 | 盘与判定 |
|---|---|
| S1 | 新 overlay，零预置卷；要求 Android handoff、second stage、vold 先消费 token、格式化成功、随后真正挂载 `/data` 和 `sys.boot_completed=1` 的 init action。关机后核对 `delegated-v2`、无 token 和非零私有 userdata。 |
| S2 | 从 **停止后的 S1 overlay** 做同盘分支；不重建卷。要求 S1 成功且有正常关机证据、S2 启动完成且挂载 `/data`，没有再次格式化/消费、没有新 token。无测试串口 shell 时允许跑宿主截止后的同盘对照，但这种结果不是正常二启 PASS。 |
| S3 | 从 S1 已 provision 的卷复制。离线设 `delegated-v2`，删除 token 和 `in_progress`，punch 掉私有 userdata 的所有 extents（保持 4 GiB，验证全零），保留同变体 S1 的真实 metadata key。要求 handoff 显示 `format_grant=reissued`，完整场景还需消费/格式化/挂载/boot completed。没有真实 key 时可加 `--structural-s3`：允许造同形但**不可解封**的占位 key，仅检查重新发放 `v2`、仍为全零和 key 目录保持，不能算加密路径或完整 S3 验收。 |
| S4 | 从空盘派生，仅覆盖测试 `/bin/resize2fs` 为静态长 sleep 桩。要求串口依次有 system-init 交接、桩、600 s grow-root 看门狗、recovery reboot；`-no-reboot` 捕获退出后读到 `boot-recovery` / `--system_stopped` / grow-root reason。宿主 124 不是看门狗证据。 |
| S5 | S1/S2 的恢复阶段日志须早于各交接；检查运行前后的完整 2048 字节 BCB 相同。`guest-entry` 必须有完成 restore 后进 `exec init` 阶段的日志；r25 无该阶段日志，只能证明 system-init 侧。读取 `/volumes/logs/{pstore,pstore.previous}` 并记录串口 backend/错误；缺失不算归档成功，也不阻断 BCB 判定。 |

如果镜像**本来就有**测试专用 UART shell，追加 `--console`，用脚本打印的 `socat` 命令接入，待启动完成后运行 `getprop sys.boot_completed`、核对 `/proc/mounts` 的 `/data`，在测试 guest 内 `sync; reboot -p` 正常关机。串口 socket 不会打开原本不存在的 shell，也不会改变镜像的 SELinux 策略。默认无交互；仍以 init 日志判断启动完成，不把单纯 QEMU 退出作为证明。

`S*/command.log`、`serial.log`、`exit.json`、`before-bcb.bin`、`bcb.bin`、`ranges.log`、`private-volume-proof.log` 和 `readback.json` 保全命令、串口、逐区 SHA 和卷/BCB 状态。`S*/verdict.json` 把证据和判定放在一起，`matrix.json` 汇总。只列 metadata key 文件名及属性，不输出 key 内容。可独立再次判定：

```bash
python3 evidence/r26-qemu/harness/verdict.py S1 evidence/r26-qemu/qemu-r26-01/S1
```

r25 的对照在 `r25-official/` 与 `r25-softkm/`；它们不是 r26 验收。看 `r25-*-prepare.log` 与 S1 的 `verdict.json`，区分 KeyMint/keystore2 注册、vold 等待及 Android 启动完成。

对照结论及证据链接见 [R25-RESULTS.md](R25-RESULTS.md)。
