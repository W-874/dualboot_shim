# 首要教训：共享 TEE 上同 UID 的 enroll 是宿主凭据写风险

**不要允许 guest 用宿主的 Gatekeeper UID 向共享 TEE 登记。** 事故中，用户确认第一次仅切换时宿主正常；guest 设置与宿主一样的密码后，HyperOS 和 Skyward recovery 均不再接受原密码。隔离 `/data`、`/metadata`，挡 `deleteUser`/`deleteAllUsers`，或设置相同 PIN，都不会拦下正常 `enroll`。非 DSU guest 的 user 0 使用与宿主相同的 LSKF 逻辑 UID `100000`，初始化 SP 时还可能使用 `0`。独立的盐和 SP 意味着相同明文不产生相同派生凭据/句柄/SID。**必须按可能覆盖宿主凭据处理**；本次实际写入哪个 QTI TA 记录、是否是 SID/限速状态和实际失败返回码均未捕获。

## 调研结论摘录

- **`HostUnlockDeps.md` §§2–5**：在检查过的 fuxi `.13` 移植包中，静态证据是 QTI HIDL Gatekeeper、QTI AIDL KeyMint、共享 QSEE/RPMB，未发现 Weaver 服务声明。锁屏验证返回旧 SID 的 HAT，授权 KeyMint 打开 SP 外层；HAT 不参与软件 protector secret 派生。[推断] TA 状态修改致旧 handle 无法得到 KeyMint 接受的旧 HAT 是最有支持的机制；不能断言磁盘密文损坏。
- **`DecryptFlowMap.md` §§1–2**：recovery 读原 `.pwd` 句柄和盐，先走 `verify(100000 + user, …)`；若 HAL 返回 `INCORRECT (-1)`，直接拒绝，不查外层 key、不装载 CE。`Retry` 和 KeyMint 拒绝是不同阶段。成功有 HAT，才能用旧 SID 绑定 key 解 SP 外层，再以软件 secret 解内层，最后由 SP 解 CE key；wrapped storage key 仍可能调用 KeyMint。真实失败点仍需原始日志佐证，不能只凭 UI 文案判定。
- **`GuestGkOffsetFix.md` §§1–2**：在 daemon/HAL 边界将合法 guest 逻辑 UID 映射到 `2000000..2999999`，宿主不变、正常 DSU `+1000000`。这不是改 SID、HAT 或本地用户索引。r26e guest 构建默认不获取共享 HAL；AIDL/HIDL 替身结果只证明传参不重号，不证明 TA 无截断、别名、全局限速或 RPMB 副作用。未验证后端一律拒绝，不许回退原 UID；保留删除拦截，查直连绕行。

**Weaver 是独立风险。** 共享 slot 的写入、删除或限速可令已有 protector 失效；Gatekeeper UID 偏移不隔离 Weaver。`.13` 的静态阴性不外推其它版本和机型。默认不在 guest 安装或调用共享 Weaver。放行只能在受控测试机上用同一 HAL/TA/固件组合证明隔离、宿主重启后凭据/CE 与认证绑定 key 均保持。事故主力机优先保全原数据与日志，不用重设密码、清 TEE、反复尝试或偏移补丁来验证或声称恢复。

归档证据：[r26e](../platform-patches/evidence/r26e/REPORT.md)、[P4.3](../platform-patches/evidence/P4.3/PLATFORM.md)，[QEMU 局限](validation-limits.md)。
