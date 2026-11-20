# r25 历史回放入参

[README.md](README.md) 和 [R25-RESULTS.md](R25-RESULTS.md) 为原始历史记录，命令/路径是当时证据，不是本仓默认值。脚本保持 r25 S1–S5 判定，不拿 600 s 的旧检查判定后来 300 s 无进展版本。

设置 `SKYWARD_REPLAY_OUT` 到空的受控回放输出根；`SKYWARD_EVIDENCE_ROOT` 指原有 `evidence/` 和 `host-baseline/` 布局所在根。`SKYWARD_PACKAGE_CHECK` 使用 [tools](../tools/README.md) 的真实 reader，`SKYWARD_TWIN` 指同版 `fuxi-twin` helper（`fuxi-twin/Cargo.toml` 原来依赖的是 `SkyWard/` 的 crate，不是 dualboot checkout；在外部隔离 staging 绑定原版构建）。另同 candidate 设置 host-A、previous candidate 和静态 e2fs 输入。

```sh
python3 harness/run.py "$GUEST_ELF" official --out old-r25   --android-out "$ANDROID_OUT" --five-images "$FIVE_SHA"   --system-init "$SYSTEM_INIT" --usb-prefix "$USB_INITRD" --build-lock "$BUILD_LOCK"   --qemu "$QEMU" --clang "$CLANG"
```

`softkm` 必须给单独批准的静态测试 OUT 及配套摘要，不会隐式替换 vendor。恢复现有准备结果用 `--prepared`。USB prefix 和历史 system-init 的 SHA 默认仍是原样本。`dada-twin`、`fuxi-twin-scenarios` 是独立历史辅助入口，原 Cargo 路径指向不同 recovery 源并需外部适配。没有镜像、主机块设备或设备访问；新 `virt` TOML 仅用于静态/host 测试，没有声称已经成为可运行的第三部设备或独立 QEMU 基线。
