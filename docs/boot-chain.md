# 启动链与边界

1. Skyward recovery 通过真实 `skyward-systems::package::read` 验包，核对独立的宿主凭据、boot set、几何与容量，预留宿主 userdata 中的 extents，制备 `Start` 与定制 ramdisk。ZIP 能被解析并不证明可安装，镜像文件长度也不是目标分区容量。不要用 guest boot 的散列替代宿主绑定。
2. bootloader 仍使用匹配的宿主 boot 链。ramdisk PID 1 `system-init` 读 `Start`、加载匹配模块、校验并映射已预留 extents 为 `skyward-root` dm-linear。它持有自己的 BCB 回退，成功后还原并交棒给 `/guest-entry`；不把宿主 `/data` 当 guest 文件系统。
3. [guest-entry](guest-entry-state.md) 校验挂载、块设备及私有卷契约，验证并只读挂载五张 Android 图加两张匹配的宿主 DLKM 图，使用 guest 私有 metadata/userdata/persist，以 Android first-stage 等价环境切根，`exec /system/bin/init selinux_setup`。宿主 metadata、guest metadata、boot 绑定和七张嵌套图是不同对象。

恢复源：`SkyWard-uwu-dualboot/crates/{skyward-system-init,skyward-systems}`（`boot::Start`、`package/install/switch/store`），`evidence/{P2.6,P3.2,P5.4,fuxi-qemu-chain}/REPORT.md`。恢复/UI/安装器不进最小 Cargo workspace；同版源码和构件用于外部链路回放。
