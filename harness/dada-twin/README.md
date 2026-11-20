# Dada r18 离线身份门禁

[`dada-run01`](../../evidence/guest-r18/dada-run01/readback.json) 是负对照：原样 `system-init` 已交接 `/guest-entry`；`virt` 的 `linux,dummy-virt` 和缺失的触摸固件 DT 属性令 Dada 拒绝，即使命令行已有 `androidboot.device=dada`。BCB 为 `boot-recovery / --system_stopped`，宿主 QEMU 退出码 0 不等于 Android 开机。

本夹具只验证身份门禁。它从 OTA 清单固定的 `OS4.0.0.22.XOCCNXM` `vendor_boot.img` 和 `dtbo.img` 中取 [OEM 板级表](../../evidence/P4.6/bootsets/dada/oem/meta/board.tsv) 的 base 0 与 overlay 0，离线合成后，把根 `compatible`、`model` 和触摸固件属性逐字节投影到同一 QEMU `virt` 拓扑。base 0 是实验所选的 Sun base，不是对手机 bootloader 实际选择的声明。保留原 r18 内核、initramfs、启动参数和只读磁盘；写入只落到新建的 qcow2。不要把整棵原厂 DTB 交给 QEMU：其板级 MMIO 不是 `virt` 的硬件。

在工程根目录执行（复用 `dada-run01` 记录的本地 QEMU 和同目录 `fdtget`、`fdtput`、`fdtoverlay`；PATH 另需 Python 3.11+、`qemu-img`、GNU `timeout`；预留约 10 GiB 临时空间）：

```bash
qemu=$(awk 'NR == 3 {print $4}' evidence/guest-r18/dada-run01/command.log)
qemu_bin=$(dirname "$qemu")
qemu_libs=$(realpath "$qemu_bin/../lib/x86_64-linux-gnu")
out="evidence/dada-baseline/r18-identity-replay-$(date +%s)"
python3 tools/dada-twin/identity-replay.py \
  --qemu "$qemu" --qemu-libs "$qemu_libs" --fdt-bin "$qemu_bin" --out "$out"
```

退出 0 表示只观测到期望的门禁推进：串口不再报告 identity mismatch，而是在紧接其后的 UFS 门禁停机；BCB 的 `--system_stopped` 理由与串口一致，所有受保护范围不变，未见 Android 第二阶段或 boot-completed 标记。以 `dt-provenance.json`、`command.log`、`serial.log`、`readback.json`、`ranges.log` 为准，不以 QEMU 退出码单独判定。

当前 `misc` 与 `userdata` 仍在 `virtio-blk-pci` 上；要继续 Dada 正常启动场景，缺少能让这两个分区由内核真实枚举在 `/sys/devices/platform/soc/1d84000.ufshc` 下的离线 UFS 拓扑和驱动输入。本夹具不伪造该路径，不修改生产门禁，不放行 P6.3 正常开机，也不证明真机启动或存储行为。
