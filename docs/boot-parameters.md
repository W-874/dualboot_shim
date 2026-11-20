# 启动参数透传

原生 DT 用于身份、硬件与 init 各自读取的板级数据；shim 不覆盖 DT。bootconfig 优先于 cmdline，按 Android 导入语义去重，保留带引号值及控制器数组的字节语义；普通内核参数与 Android 键分开。仅已观测到配置的控制器时产生可信的 boot_devices/bootdevice 拼写；历史非 UFS 分支保留原 AOSP 导入语义。槽位必须有 `_a` 或 `_b`，两来源矛盾拒绝，不从目录猜槽。

拒绝 `init_rc`、`android_dt_dir`、`fstab_suffix`、DSU/根设备重定向，以及 host boot timeout 等危险输入；fatal target 固定 recovery，cmdline 同时清除 Android 键残留以避免 init 早期子串误识别。`hwc`、charger 等板级输入仍可能必要。宿主的 AVB 状态只是观测，不是 guest 自己经过 AVB 验证的证明。

原 `BootParamAudit.md` 要点：对照 AOSP 各消费路径而非只比字符串；archive fixtures 保留 `.307` 实际 cmdline、`.309` 离线 vendor_boot 和 QEMU 串口，以及单引号、数组、重复键和危险 override 的回归覆盖。
