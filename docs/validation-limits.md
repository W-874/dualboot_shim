# 验收分层与 QEMU 局限

`QemuRunbook.md` 的边界：`virt` 不是手机，缺实际 Qualcomm TEE/QSEE/RPMB 和 GPU/显示链。不能证明 KeyMint/Gatekeeper/Weaver、凭据保持、GPU 或性能已验收。Softkm 只覆盖软件路径，不进发布组合；也并非所有 userspace 故障都可归因于 TEE/GPU。

- 主机单测覆盖策略、状态、授权注入、BCB 字段与仲裁、pstore，不能证明 Android 内核/设备安全。Android 静态 ELF 构建也不是运行验收。
- 离线虚拟机可以测实际早期交接、合成 GPT 的保护范围、BCB、故障注入和同盘二启：readonly raw backing，各场景独立 qcow2，无 host 真实块设备。r25 判定保留在 [harness](../harness/README.md)，不是 r26c 的断言。
- 已有证据（非本次执行）：r26c S1–S3 进入 Android，未到 boot complete，未证明同盘数据保持；S4 有回退证据；S5 有 BCB 对照但没有 pstore 证据。r26d 修复 64 位 zygote 后，官方短 S1 仍未完成。证据原位置 `evidence/{r26c,r26d,qemu-softkm}/REPORT.md`。
- 分开报告 handoff、vold/挂载、`sys.boot_completed`、正常关机后二启数据保持；物理设备的凭据与硬件功能须在独立受控测试机另行放行。归档不发布刷机包。
