# P4.5 — guest SELinux 编前验收

## 策略与边界

`uwu_dada`、`uwu_fuxi` 通过 ProductGraph 的 guest BoardConfig 纳入 `device/xiaomi/skyward/sepolicy/vendor` 和本机 `sepolicy/skyward`。非 guest 不接入这些策略；`ueventd` 仅在只读 `ro.skyward.guest=true` 时使用 guest 标签，属性于 second-stage init 载入系统属性后、启动 `ueventd` 前可读。

| 目标 | 块/字符数据权限 | 说明 |
|---|---|---|
| `skyward_host_block_device` | 无 | 包括宿主原始 userdata、metadata、persist、FRP、fsg/fsc、ssd 等；guest dm/loop 仍走原来的标签路径。 |
| `skyward_firmware_block_device` | `init`：`getattr/open/read` | dada：modem、modemfirmware、DSP、BT；fuxi：modem、DSP、BT。只匹配确认的 UFS 控制器与 A/B 分区身份。 |
| `skyward_modemst_block_device` | 仅 `vendor_rmt_storage`：`getattr/open/read/write` | 只匹配 modemst1/2，不继承通用 EFS/SSD 权限；不授权 ioctl。 |
| `skyward_misc_block_device` | 仅 `skyward_bootwatch`：`getattr/open/read/write` | stock recovery、工厂组件、更新器不授权；独立请求端不直接开放 misc，后续受审请求可由专用域处理。BCB 偏移与请求种类不是 SELinux 可表达的限制。 |
| `skyward_raw_device` 及 Qualcomm stock SG/BSG/RPMB 字符标签 | 无 | guest `skyward_guest_policy` 编译门禁排除两 SoC 旧的 `hal_bootctl`、`recovery`、TEE raw allow；`neverallow` 同时覆盖新旧字符标签。`ueventd` 不公布整盘、bsg/sg、直接 RPMB 和已知 eSE/NFC 节点。 |

`ueventd` 的节点生命周期权限与读取/写入分区内容分开。新物理块类型不属于 `dev_type`，避免继承 `init` 在该属性上的块读取与 ioctl；策略同时拒绝基本与扩展 ioctl。标签选择使用内核分区身份，作用于 inode；普通 `/dev/block/by-name` 和 controller symlink 不能替换标签。guest-entry 还须在 Android 接管前执行块设备只读与节点抑制；本项没有接触手机，也不证明固件挂载、受限 modem 或 BCB 的运行时行为。

依据：[dada 处置表](../../host-baseline/dada/shared-state.md)、[fuxi 处置表](../../host-baseline/fuxi/shared-state.md) 和 P4.4 guest fstab。

## 验收方法与状态

使用 HwGraph 的同一 `uwu_<device>-cp2a-user` 冻结快照与 OUT：只构建 preflight 所需的 `precompiled_sepolicy` 和 `vendor_file_contexts`，无 ROM 目标。Soong 的 `secilc` 正向编译必须包含 platform、system_ext、product 的版本映射及目标 vendor policy，不使用 `-N`。

```sh
bash evidence/P4.5/verify.sh <OUT_DIR> dada >evidence/P4.5/dada.log 2>&1
bash evidence/P4.5/verify.sh <OUT_DIR> fuxi >evidence/P4.5/fuxi.log 2>&1
```

脚本记录完整映射编译命令及策略 SHA-256，使用 `sesearch --allow` / `--allowxperm` 比对白名单；再以相同映射输入注入禁止规则，要求 `neverallow` 拒绝编译。外部 `sesearch` 路径可由 `SESEARCH` 覆盖。旧产物或任何非本次构建的策略不作为验收证据。

| 设备 | 映射 `secilc` | `sesearch` 白名单 | `neverallow` 反例 | 证据 |
|---|---|---|---|---|
| dada | PASS：platform、system_ext、product `202604` 映射，无 `-N` | PASS | PASS：六条禁止授权均被拒绝 | [构建日志](../P0.3/frozen.9DWx1vII/dada.log)、[审计日志](dada.log) |
| fuxi | PASS：同一冻结轮的 platform、system_ext、product 映射，无 `-N` | PASS | PASS：六条禁止授权均被拒绝 | [构建日志](../P0.3/frozen.9DWx1vII/fuxi.log)、[审计日志](fuxi.log) |

双机 P4.5 编前验收 **PASS**。两机 raw 字符设备的通用规则只涉及节点元数据与生命周期，均无打开、读写或 ioctl 授权。只执行规范要求的策略编前验证；未构建 ROM，未操作真机。
