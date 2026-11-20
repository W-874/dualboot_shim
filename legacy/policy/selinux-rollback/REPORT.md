# 两机 guest SELinux 撤销清单

范围：小米 13 (`fuxi`，SM8550)、小米 15 (`dada`，SM8750) 当前构建源码。恢复的是项目选用的 Android/Lineage/CAF 源码基线，不是把 HyperOS 的二进制策略复制进 Android 17。没有改手机、迁移现存卷、关掉 enforcing 或新增替代 allow。历史冻结快照和运行日志保留原样。

## 1. 高通原有访问：去掉 guest 专用的屏蔽条件

两机各 4 个文件；原始增量可直接审阅：[SM8550](../../lock/frozen-r14/patches/device/qcom/sepolicy_vndr/sm8550.patch)、[SM8750](../../lock/frozen-r14/patches/device/qcom/sepolicy_vndr/sm8750.patch)。对应 manifest 源版本分别为 `3246964ec405f5c7f9c6aa15e7331b4de1c04bbc`、`19dd1d9a3ce4ac8d993203edac1ec05749a8d60b`。

| 文件 / 域 | 当时为什么改，实际改动是什么 | 本次恢复 |
|---|---|---|
| `generic/vendor/common/tee.te` / `tee` | 为隔离共享持久安全状态及 UFS 原始命令，我在 guest 构建里排除了 RPMB 块/字符读写、RPMB 字符 `MMC_IOC_MULTI_CMD`，以及 SG 读写和 `setattr`、BSG 和 UFS LUN4 BSG 读写。没有先证明真实安全服务在缺这些能力时能工作。 | 恢复上游权限及 ioctl 范围。 |
| `qva/vendor/common/qseecomd.te` / `tee` | 防止经 QSEE 路径向共享 RPMB 发命令；排除块设备 `MMC_IOC_CMD` 和字符设备 `MMC_IOC_MULTI_CMD`。这两条并不是“任意 ioctl”。 | 恢复这两条上游 `allowxperm`。 |
| `generic/vendor/common/hal_bootctl.te` / `hal_bootctl` | 为防止 guest 影响主机启动槽，排除 Boot Control HAL 对 SG/BSG 字符设备的读写。 | 恢复上游规则。 |
| `generic/vendor/common/recovery.te` / `recovery` | 为防止 recovery 绕过分区节点操作存储，排除 SG/BSG 读写和 SG 显式 `ioctl`。 | 恢复原规则，仍限 `recovery_only`，没有给普通系统增加 recovery 权限。 |

## 2. 项目另加的策略、标签与构建开关

| 已撤销的条目 | 当时为什么加 |
|---|---|
| `skyward_physical.te`：host、firmware、modemst、misc 四类物理块标签；ueventd 建节点/重标规则；访问白名单及普通/扩展 ioctl 禁令 | 想禁止 guest 域读写 host 类节点；允许 init 只读必要固件、仅 `vendor_rmt_storage` 读写 modemst、仅 bootwatch 读写 misc。对 modemst 的例外本身就是共享写入，不是完整物理隔离。 |
| 同文件 raw 类型及属性、SG/BSG/RPMB 字符设备和 RPMB 块设备限制；`file_contexts` 对 `/dev/bsg`、`/dev/rpmb*`、`/dev/p61*` 的映射 | 为防止从别名或原始接口绕过块路径限制；同时限制了上游硬件服务。 |
| `system/core/init` 的物理节点上下文覆盖和分区分类；两机 `sepolicy/skyward/file_contexts` | 按内核分区身份给节点打上上述标签，而非相信 by-name 路径。两机最后留下的设备级文件只有说明注释。现恢复 Android 常规标签查找。 |
| guest `file_contexts` 对 `/dev/block/skyward/{metadata,userdata,persist}` 与目录的映射，及 init 对 loop 的 `relabelto` | 让 Android 的挂载服务按 metadata、userdata、loop 类型访问 guest 私有卷。 |
| `skyward_volume_backing.te` 与 Rust `guest-entry` 的专用 xattr | r17 在 QEMU 有确切的 loop 写入拒绝；r18 给镜像底层文件打专用标签，仅允许 kernel 对已打开的文件读写，禁止额外权限。它解决的是模拟器已复现的 I/O 故障，不是已证实的真机黑屏。现不再写该标签，不清理手机旧卷。 |
| `skyward_bootwatch.te`、其 exec、state/attempts 文件标签、针对这些路径的 `restorecon` | 让监控进程观察启动状态、改尝试计数、按需写 BCB，而非让普通进程接触 misc。 |
| `skyward_switch_app.te`、`seapp_contexts` | 让指定平台签名快捷开关运行在独立域，只能提出切换请求，不能直接重启或写块设备。 |
| vendor `property.te`、`property_contexts`：switch_requested、recovery_ready 及设置方限制 | 将提出切换与完成 BCB 写入分开，限制谁可发出请求和确认。删除后，watchdog、快捷开关和配套 rc 不再进产品；删除 init 对 recovery_ready 的特殊处理。不借用其他域来绕过。 |
| 平台 `skyward_guest_prop` 和 `ro.skyward.guest` 专用 property context | 让多个域都能读 guest 身份，同时限制写入端。仅取消专用类型，不取消已有非 SELinux 保护分支。普通属性类型的可见性不能再沿用旧结论。 |
| `private/compat/33.0/33.0.compat.cil` 的 `proc_sys_walt` 声明 | 曾为 fuxi API 33 vendor CIL 引用的 system_ext 类型做兼容；它不是硬件访问 allow，但同样是项目改动，已撤销。 |
| guest BoardConfig 的 m4 开关、公共及设备专用策略目录；dada `BOARD_GENFS_LABELS_VERSION := 202604` | 前者仅让两机 guest 使用以上规则；后者强制选择 Android 17 板级 genfs 标签版本。现取消覆盖，dada 沿用公共树原值 `202504`。 |

当时设计的分区逐项说明及原收紧验证：[P4.5](../P4.5/REPORT.md)。这些检查验证了“拒绝是否生效”，没有验证“依赖这项访问的真实服务是否可用”。

## 3. 没有借回滚之名改动的内容

- `/proc/bootconfig`、`/proc/cmdline` 的替代文件沿用 Android 原有上下文；替换 fstab 时继承原 inode 标签；固件和蓝牙挂载沿用原厂上下文。这些是挂载时保留原标签，不是自定义策略条目。
- 非 SELinux 的分区节点过滤、guest fstab、格式化授权和其他宿主保护没有随策略回滚拆掉。因此“上游规则已恢复”仍不等于“硬件服务一定能打开节点”。
- 当前交付目录内的四个旧候选 ZIP 及其校验文件已移到 `withdrawn/`；没有发布新 ZIP。旧手机上的安装和旧卷不受源码回退影响。两个常规组包入口均暂停发布，不能用旧 OUT 或已删标签的旧 entry 冒充回退产物。

## 4. 回退验证结果

- 小米 13：`uwu_fuxi-cp2a-user` 的 `precompiled_sepolicy vendor_file_contexts` 编译成功并安装到构建输出，退出 0（[日志](fuxi-policy.log)）。没有构建或启动回退后的整包。
- 小米 15：恢复公共树 `202504` 后，在产品配置阶段就被 Android 17 构建系统拒绝：`BOARD_GENFS_LABELS_VERSION (202504) must be greater than or equal to BOARD_API_LEVEL (202604)`（[首个错误](dada-policy.log#L15)）。不是规则编译错误；没有重新加回 override。
- 共用 AArch64 [guest-entry 编译成功](guest-build.log)。两个组包入口的发布暂停检查均触发，未写入新 ZIP。

## 5. 节点过滤撤销及 Android 17 兼容项复核（取代上节对应的当前状态）

- 两机共用 guest-entry 不再按设备号删除 `/dev` 节点；Android ueventd 恢复上游节点创建和事件跟踪。分区只读标志是独立操作，未撤销。见 [guest-entry 交叉编译](unfiltered-guest-entry.log) 和 [fuxi init 编译](fuxi-unfiltered-init.log)。
- dada：仅 `uwu_dada` 恢复 `BOARD_GENFS_LABELS_VERSION := 202604`，针对上节已复现的 Android 17 产品配置错误；不是访问授权。见[恢复后的构建日志](dada-genfs-unfiltered.log)。
- fuxi：旧 `33.0.compat.cil` 声明针对 API 33 vendor。当前 guest 的 `vendor/etc/selinux/plat_sepolicy_vers.txt` 为 `202604`，其 vendor/ODM CIL 没有 `proc_sys_walt` 引用，且上节的预编译策略已通过，因此当前目标不恢复该旧声明。
- 两机用于接入自定义过滤策略的 m4 开关和目录保持移除。上述验证不构成整机启动验证。
