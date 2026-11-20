# P4.3 平台源码：guest 专用拒绝策略

## 产品契约

与 ProductGraph 约定：只有 fuxi / dada 的 Skyward guest 产品设置 `PRODUCT_SYSTEM_PROPERTIES += ro.skyward.guest=true`；非 guest 产品不设。平台各处只将精确的 `true` 识别为 guest，未设置或 `false` 保持已有流程。`system/sepolicy/private/skyward_guest_prop.te` 和 `property_contexts` 为该只读属性声明 system 所有、仅 `init` 可设置的 `true`/`false` 枚举。框架在调用进程里读取属性，以便在执行任何本地准备之前拒绝；服务端独立判断，不信任调用进程。属性也允许 early-init 的 ueventd 读取，供 P4.5 使用。

## 拒绝路径

| 源码 | Guest 行为 |
| --- | --- |
| `system/core/gatekeeperd/gatekeeperd.cpp` | 冷启动保留本地 marker 但跳过 `deleteAllUsers`；收到授权的 `clearSecureUserId` 时保留本地 SID 清理及成功状态，不向 AIDL 或 HIDL HAL 转发 `deleteUser`。原有 GSI 条件不变。 |
| `system/security/keystore2/src/maintenance.rs` | `deleteAllKeys` 保留 SELinux 权限检查，guest 返回 `PERMISSION_DENIED`，不获取或调用任何级别的 KeyMint。 |
| `system/security/keystore2/src/km_compat.rs`、`src/km_compat/km_compat.cpp` | Rust backlevel KeyMint wrapper 的 `deleteAllKeys` 和 `destroyAttestationIds` 返回 `UNIMPLEMENTED`，不转发。C++ Keymaster 兼容层的 `deleteAllKeys` 同样拒绝；其 `destroyAttestationIds` 原本即返回 `UNIMPLEMENTED`，无转发。原生 vendor HAL 本身不属于这些平台入口，未修改。 |
| `frameworks/base/core/java/android/os/RecoverySystem.java` | 擦除/重格式化、eUICC 清理、安装/调度/取消 OTA、uncrypt 包准备、无人值守更新与 resume-on-reboot 入口在本地副作用前拒绝，返回各 API 的 `IOException`、`false` 或非零错误码。只读包验证不变。 |
| `frameworks/base/services/core/java/com/android/server/recoverysystem/RecoverySystemService.java` | 直接 Binder 调用也在副作用前拒绝：不启动 `uncrypt`/`setup-bcb`/`clear-bcb`，不准备/清理/应用 LSKF，不分配 OTA 空间，不删除 KeyMint/Secretkeeper 密钥，不重启。通用 BCB 入口不解析命令，一律禁用，避免覆盖共享 `misc`；void reboot 入口返回 Binder `UnsupportedOperationException`。专用 `--switch_system=installed` / `--system_stopped` 路径不走通用 RecoverySystem。 |

## 主机验证

```sh
python3 evidence/P4.3/platform-test.py
```

结果见 [platform-test.log](platform-test.log)。测试将 C++、Rust 的实际入口方法，以及 Java 到首个原有副作用边界的实际入口代码，编译进仅主机运行的测试夹具。三组均通过：guest 调用不触达替身 HAL 或原有执行路径，未设置及 `false` 则保留原路径；额外验证 Gatekeeper 权限、原有 GSI 抑制和冷启动 marker、Keystore 维护权限拒绝、Recovery Binder 权限与 K1 命令。没有调用真实 Binder 服务、TEE、手机或块设备。这是源码级行为验收，不是 Android 平台模块集成编译、Treble/SELinux 策略编译或真机验证。

包裁剪及产品图见 ProductGraph 的 [REPORT.md](REPORT.md)。最终镜像的 `installed-files*.txt`、分区 VINTF/rc 和最终 build.prop 检查须在 P4.6 产物形成后进行，本次未执行。
