# Guest-only 产品片段

```sh
python3 tools/fstab-lint.py --profile profiles/<board>/device.toml --render /path/to/aosp/device/<owner>/<guest>/generated
```

在独立的 guest 产品里显式将 `SKYWARD_GENERATED_PROFILE` 指向 AOSP 树内这个生成目录，包含 `product.mk`；相应板级最终包含 `BoardConfigGuest.mk`。普通产品不包含任何片段。生成目录覆盖五列 fstab、charger fstab、ABI 属性和原 TOML 摘要；原始 fstab 的 `formattable` 仍必须由 PID 1 运行时清洗并闸控，不能供普通 init 启动。删除原 fstab/charger 的重复安装边。设置 `skyward_vendor_mk` 后每次包含 `vendor-inputs.mk` 可读取并过滤一份 vendor 片段，不要求 Android 树位于本仓附近。

此模板以明确的 Make 安装路径替代旧按机型枚举的 Soong fstab 模块；`gatekeeperd` 仍用 Soong 的 `skyward.guest_gatekeeper` 选项。生成文件不包含 boot/内核/固件等输入；核对 guest ABI、宿主 bootset/DLKM、普通/charger fstab 唯一来源和 VINTF/SELinux。历史 bootwatch/tile 和单系统安全硬件 RC 在 [legacy](../legacy/README.md)，默认不安装。拦截共享 Weaver/Boot HAL/OTA 必须在实际镜像确认，无自动安全放行。本片段未在 AOSP 中构建，仅验证生成和 lint。
