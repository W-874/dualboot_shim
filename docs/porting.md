# 新机移植与当前边界

1. 以 `profiles/` 为输入模板，检查 DT identity 的互斥证据、misc/userdata 控制器拓扑、槽位与分区几何、只读固件 fstab、ABI，显式选用有匹配平台补丁的格式协议。默认新组合只用 token-v2；`legacy-v1` 仅登记历史组合。`/skyward-guest.toml` 是每次安装实例参数，不是授权来源；`system.toml` 是包及宿主绑定。不能靠配置扩大宿主物理写权限上限。
2. `profile.rs` 现集中两机的可信身份规则，以及读取只读 DT 的入口。嵌入版 `device.toml` 驱动触控属性路径、控制器路径/拼写和强制性、只读固件/charger fstab、协议，产品生成器另读取其 zygote。保留原两机判定，包括 fuxi 特殊 boot claim/Twin 路径；这是**兼容档**，不是新机信任规则。当前要添加新板仍须修改 Rust 的 profile 注册及身份判定，不能只下发 TOML；`virt` 仅测试解析和拓扑，不参与 release 设备选择。
3. 新移植要补齐一个编译期固定的描述选择/摘要绑定、精确 DT 字节匹配与互斥规则、可审核的父设备/角色/容量约束，同时禁止仅凭 boot claim、历史 Twin 例外或可写参数选择新机。核心仍固定 A/B、私有三卷与 ROM 镜像 ABI；新布局应显式升级契约，而非允许任意分区名。构建工具与 Rust 尚分别验证自己消费的配置，生成的 SHA 尚未联绑 ELF、fstab、包和独立宿主证据。
4. 使用 [guest-only 产品片段](../product-template/README.md) 和 [准确平台基线](../platform-patches/README.md)，移去原 fstab/charger 重复安装边，确认 ABI、匹配宿主 bootset/DLKM 和 SELinux/VINTF 回读。历史 vold 仍有 fuxi 门限；另做完整 guest 身份/能力闸控后，才能扩大支持范围。不安装共享 Boot HAL、Weaver、通用恢复/OTA 写入口。没有实体后端隔离证据，不启用共享 Gatekeeper。
5. 先跑主机正反例与 ELF 检查，再使用合成盘 offline QEMU，最后用受控测试手机独立验证两套系统的实际解锁、CE 与绑定 key 保持。单系统产品图不继承任何 guest 片段。参见 [验收分层](validation-limits.md) 与 [TEE 事故教训](lessons-shared-tee.md)。
