# r26e：fuxi guest Gatekeeper UID 隔离与正式镜像测试包

**步骤：** r26e（fuxi）  
**状态：** PASS（定向构建、离线回读和正式镜像测试 ZIP；不放行真机硬件锁屏）  
**执行者：** GuestGkOffsetImpl / newapi/gpt-6.2-sol  
**开始/结束：** 2026-11-20 18:44:24 / 19:02:32（UTC+08:00）

## 输入

- r26d 的[冻结锁](../../lock/frozen-r26d/)、[五图 SHA](../r26d/build/five-images.sha256)、[打包七图 SHA](../r26d/pack/fuxi/images.sha256)及[原 OUT](../fuxi-r25-a-storage/build-arm64/fuxi/OUT_DIR)：`out-p41-fuxi-user.2xQIuWz4`。产品为 `uwu_fuxi-cp2a-user`，复用同一个 [`build.lock`](../P4.6/android-five-images/build.lock)。编前在原 OUT 校验五图均匹配 r26d。
- r26c 的 [`guest-entry`](../r26c/build/guest-entry) SHA-256 `137a23dc87de81e13a99388ba3b01f49c084d782cbda0152644f320c2fafed76`；使用经过固定的 fuxi boot、两张 DLKM、静态 helper、包检查器，以及 fuxi 原厂 kernel release/IKCONFIG。[输入 SHA](inputs.sha256)和[校验](build/inputs-check.log)。

## 源码变更

[相对 r26d 的完整源码 diff](build/source.diff) · [改动文件 SHA](build/source.sha256) · [实际 fuxi Soong 编译参数](build/gatekeeper-cflags.log)。只改 `gatekeeperd.cpp`、`gatekeeperd/Android.bp` 和两机共用的 `skyward-guest/product.mk`。guest 产品在 Soong 中设置 `skyward.guest_gatekeeper`，只给 daemon 添加 `-DSKYWARD_GUEST_GATEKEEPER`，不依赖运行时 `ro.skyward.guest`。没有改 LockSettings、keystore2、recovery 或 guest-entry。

- `adjust_userId` 在 AIDL/HIDL 边界使用 `0 <= uid < 1,000,000`，guest 优先映射到 `2,000,000..2,999,999`，与 GSI 位无关；非 guest 原值、非 guest DSU `+1,000,000`。`enroll` 的立即 `verify` 仍传原逻辑 ID 给 proxy，各次转发只偏移一次。SID 文件键及授权服务收到的 HAT SID 不参与偏移。
- P4.3 的两处删除抑制改用同一编译身份：guest 保留 `.coldboot` 和授权后的本地 SID 清理，**不发送** `deleteAllUsers`（该 API 无 UID）或 `deleteUser`。非 guest 延续已有权限、DSU 与 `deleteUser` 映射行为。
- **r26e 对共享 Gatekeeper 默认拒绝。** 没有已验证并绑定的目标 HAL/TEE 组合；guest 构造时不查询 AIDL/HIDL Gatekeeper，真实凭据操作不送到共享 HAL，也不回退原 UID。偏移是对已获准后端的映射规则，本轮只通过主机注入替身验证该路径；不能据此宣称硬件锁屏可用或 TEE 已隔离。

## 命令

在工作区根执行。新锁与新 ZIP 需要未使用的输出路径。

```bash
set -euo pipefail
e=evidence/r26e
sha256sum -c "$e/inputs.sha256" | tee "$e/build/inputs-check.log"
python3 "$e/build/gatekeeper-test.py" | tee "$e/build/gatekeeper-test.log"
/usr/bin/bash "$e/build/build-system.sh" > "$e/build/system.log" 2>&1
/usr/bin/bash "$e/build/relock.sh" > "$e/build/relock.log" 2>&1
flock -x evidence/P4.6/android-five-images/build.lock bash -ec '
  e=$1
  grep -Fx "build=0 guard=0" "$e/build/build.exit"
  sha256sum -c "$e/build/source.sha256"
  python3 -u "$e/checks/verify.py"
' bash "$e" > "$e/checks/verify.log" 2>&1
/usr/bin/bash "$e/pack/pack.sh" > "$e/pack/driver.log" 2>&1
```

编译脚本实际执行 `lunch uwu_fuxi-cp2a-user` 和 `m -j8 out-p41-fuxi-user.2xQIuWz4/target/product/fuxi/system.img`。打包脚本用 r26d 的 `skyward-candidate.py` `pack`/`readback`、`offline=True`，版本参数改为 `r26-e`；增加内嵌 `gatekeeperd` 回读和 r26d 逐镜像摘要对照。

## 产物与 SHA-256

| 镜像 | r26d | r26e |
|---|---|---|
| `system.img` | `9492acbff4b33c5cdf54b3f9a6e8008ccde9149e10a2e20aebfc4b66a176515a` | `024b6df60c602b3d16c15ca8eca1c302169411ddd439fc7cd6408a57c737b203` |
| `system_ext.img` | `e58680f74fb38035fd118b36d66802a8d50b1b4c142d40ae6e478ad8b94c13e1` | 相同 |
| `product.img` | `53ba5b2d68c5b6cf202b437e7840fdc1ea30b53895f9bd3b8f51db046ef7bd7d` | 相同 |
| `vendor.img` | `3246565835aacb0e5c38a538166f7dff3482d0c730ece03ed96bf76482317e10` | 相同 |
| `odm.img` | `55c6f15ee7b1a5a4e31d0b2f11f12b4674d111d5d9e76ad32ce3c4db450ea82c` | 相同 |

[构建后五图](build/five-images.sha256) · [SHA diff](build/image-sha.diff) · [编前文件 stat](build/before.tsv) / [编后 stat](build/after.tsv)。

[`frozen-r26e`](../../lock/frozen-r26e/) 的 `manifest.xml` SHA-256 `9055416541673587efb8c04975a781325137fa689d6214360cb44937bf178f76` 与 r26d 及构建 manifest 字节一致；`SHA256SUMS` SHA-256 `763f131e7b61fb044d7e6fa129f27b0b55c5cdef33f6f413f1746ed6a93ac1ee`。[锁差异](build/relock-vs-r26d.diff)只涉及 `system/core` 与 `device/xiaomi/skyward-guest` 的补丁和记录。

[正式镜像测试 ZIP](pack/uwu-fuxi-r26-e-test.zip)，版本 `17.0.182-r26-e`，3,728,577,067 B，[SHA-256](pack/uwu-fuxi-r26-e-test.zip.sha256) `10fc9af398ec104345f55c0e0dcfe17e4b6d9e017b451dc8c4491b4c85486350`。[顶层三成员 SHA](pack/fuxi/members.sha256) · [七图 SHA](pack/fuxi/images.sha256) · [r26d 七图对照](pack/image-sha.diff) · [rootfs 13 项内容 SHA](pack/fuxi/rootfs.sha256) · [打包后 df](pack/df-after.log)。

## 验收逐项

| 项目 | 观察结果 |
|---|---|
| 主机夹具 | [8 组均通过](build/gatekeeper-test.log)：guest 两种 GSI 位下，AIDL/HIDL 替身实际收到 `0 → 2000000`、`100000 → 2100000`；覆盖 `100042`、`999999`、非法 `-1`/`1000000`、enroll 后立即 verify、挑战与 reenroll、失败不回退、SID 键与 HAT SID、权限和删除拦截。未注入 HAL 的 guest 构造器未查询两类服务，enroll/verify 均失败。夹具编入原 proxy 方法，替身不含真实 Binder/TEE；它取代 P4.3 旧 Gatekeeper 运行时属性断言，旧 P4.3 记录保留。 |
| 定向增量及磁盘 | [编译日志](build/system.log)记录最终 Ninja 16 步、构建 `02:35` 成功，[退出](build/build.exit) `build=0 guard=0`。实际只装入新 `system.img`；另四图 SHA、inode、mtime 均未变。[编前](build/df-before.log) `112436125696 B`、[采样](build/space.tsv)最低 `111085277184 B`（103.456 GiB）、[编后](build/df-after.log) `111725645824 B`，未触及 100 GiB 停线。scope `MemoryHigh=36G`、`MemoryMax=42G`、`MemorySwapMax=512M`。 |
| P0.2 冻结及重放 | [校验](build/relock-sha-check.log)通过；[重锁日志](build/relock.log)记录随机抽验 `device/xiaomi/dada`、`vendor/xiaomi/fuxi` 均无差异，另定向重放完整 guest 非 Git 树及 `system/core` 的两个改动文件，均匹配实际源码。 |
| 实际镜像只读回读 | [日志](checks/image-readback.log)：新镜像 `/system/bin/gatekeeperd` 与 OUT 安装件逐字节相同，SHA-256 `9e44f5224120a7f6218d7b9058bb801914f578e9bcb3c63423d6185988599f5d`；r26d 读出件为 `f9ffc8897f7901c1b7b5984ee52cac0d062ec827bf51073b948ed0f23229a170`。RC 与 OUT 相同，回读 `ro.skyward.guest=true`，安装清单有 `/system/bin/gatekeeperd`。 |
| fsck / checkvintf | 新 system `e2fsck -fn`、保留的 r26d vendor `fsck.erofs` 均退出 0（[fsck](checks/fsck.log)）。对实际五图只读解包并激活 APEX，使用 r26d 的空 SKU 组合、首发 API 33、fuxi 原厂 kernel release + IKCONFIG。沿用 r26d 的 `out-dada` 宿主工具、实际 dirmap 全为 fuxi 镜像。[checkvintf](checks/vintf.log#L213) 输出 `COMPATIBLE`，[检查退出](checks/verify.exit) `0`。 |
| 正式镜像测试 ZIP | [打包](pack/pack.log)及[只读回读](pack/readback.log)的 SkyWard `package::read` 与 rootfs `e2fsck -fn` 均通过；整包 SHA、恰好三个顶层成员（`boot.img`、`rootfs.img`、`system.toml`）、三成员 SHA、七图 SHA、13 项 rootfs 内容、策略映射和 `unzip -t` 均通过。ZIP 的嵌套 `system.img` 取出的 `gatekeeperd` 与 OUT 读出件逐字节相同。boot 及其余六张图（含 vendor 和两张 DLKM）与 r26d 一致。[ZIP 检查](pack/zip-check.log) |

## 偏差

- [首次编译入口](build/system-initial.log)中，`lunch` 显示 `cp2a`，但 `get_build_var TARGET_RELEASE` 返回空；预检未进入 `m`。改用 `lunch` 导出的 `$TARGET_RELEASE` 后通过，实际只运行一次镜像构建。
- 这是 fuxi 定向增量，不是 P4.6 全编；镜像未触发 dada 构建。未运行 QEMU、未连接手机、未验证真机部分。未写 `deliver/`，不解除 [RELEASE-HOLD](../selinux-rollback/RELEASE-HOLD)。

## 下一步

P4.3 / S7：在可恢复的测试机上确认并绑定目标 HAL/TEE 身份，验证无别名及宿主凭据、CE、认证绑定密钥保持后，再评估放行硬件 Gatekeeper。当前候选不能安装到主力机验证共享硬件锁屏。
