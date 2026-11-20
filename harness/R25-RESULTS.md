# r25 对照：fuxi QEMU S1

这两次运行只验证场景流水线及测试镜像变体，**不是 r26 验收**。共同输入：r25 `guest-entry` SHA-256 `7d9ccb302b3a246a6d74537ee1d69812a0971099104d42bbe44661e006b4821a`；QEMU 11.1.3 / TCG；从各自测试包的 `boot.img` 抽取同一个 5.15.194 AArch64 内核，xHCI USB 孪生盘；两组均从独立空盘首启。打包和 prepare 的 `SkyWard package::read OK` 见 [official](r25-official-prepare.log) 与 [softkm](r25-softkm-prepare.log)。

## Official

- [串口](r25-official/S1/serial.log#L339)：system-init 先恢复 misc 并交给 `/guest-entry`，Android 在 320.656 s 进入 second stage。串口从 [495.691 s](r25-official/S1/serial.log#L838) 开始反复丢弃对 `aidl/android.system.keystore2.IKeystoreService/default` 的启动请求，复现了服务不可用的等待；本轮没有直接捕获 KeyMint HAL 的 `NAME_NOT_FOUND` 或 keystore2 注册成功。
- [退出](r25-official/S1/exit.json)：宿主 750 s 截止（124）。[卷回读](r25-official/S1/readback.json)：4 GiB 私有 userdata `delegated-v2` 且仍全零；`v2` token 未消费，无完整 metadata key。BCB 原字节不变，254 个受保护区间 [SHA 均一致](r25-official/S1/ranges.log)。未观察到 `/data` 成功格式化、挂载或 `sys.boot_completed=1`；[S1 判定未通过](r25-official/S1/verdict.json)。

## Softkm

- [串口](r25-softkm/S1/serial.log#L389)：Android 在 342.526 s 进入 second stage，fs_mgr 在 [417.930 s](r25-softkm/S1/serial.log#L836) 发现私有 `/data` 可格式化。
- **确认 keystore2 发布过 `IKeystoreService/default` 且可调用。** [432.838 s 和 453.622 s](r25-softkm/S1/serial.log#L837) 的 `vold: keystore2 Keystore generateKey returned service specific error: -67` 是 Binder 服务返回，不是仅看到进程或 init rc。`system/vold/Keystore.cpp` 先查找 `IKeystoreService/default`、获取 `TRUSTED_ENVIRONMENT` security level，再调用 `generateKey`；`-67` 为 KeyMint `ROLLBACK_RESISTANCE_UNAVAILABLE`，`system/vold/KeyStorage.cpp` 对 rollback-resistant 失败会回退。未捕获 keystore2 自己的注册成功行；同次启动 453.303 s 也有一条 lookup 启动请求失败，该行不否定两次已返回的 Binder 调用。
- 到 [1030.968 s](r25-softkm/S1/serial.log#L844) 仍有同类 `generateKey` 返回。未观察到 Gatekeeper HAL 等待日志，不能据此断定其是否阻断后续启动。
- [退出](r25-softkm/S1/exit.json)：宿主 1200 s 截止（124）。[卷回读](r25-softkm/S1/readback.json)：真实 metadata key 四文件存在，token 已不在，`in_progress` 不在，4 GiB 私有 userdata 为 `delegated-v2` 且实际读到非零字节。**没有**看到串口 `Skyward mkfs: consumed`、`Format ... succeeded`、`/data` 挂载成功或 `sys.boot_completed=1`；[S1 判定未通过](r25-softkm/S1/verdict.json)，不把盘状态替代为完整启动证据。
- BCB 原字节不变，254 个受保护区间 [SHA 均一致](r25-softkm/S1/ranges.log)。两次 S1 都未见 pstore backend 注册或卷中 pstore 归档；未跑 S2，不能判定跨重启归档。

[Official](r25-official/S1/df.log) 与 [softkm](r25-softkm/S1/df.log) 两次启动前可用空间分别为 172877193216 B 与 168581410816 B，均高于 100 GiB。

另外用历史盘的派生副本做了两次**离线**夹具检查：r21 首启副本可构造结构版 S3（零 userdata、保留容量、`delegated-v2`、缺 token、模拟 key 目录和 ext4 检查通过）；r24 空孪生盘可编译并安装 S4 AArch64 桩，离线改动逐字节回读匹配。没有将这两次离线检查计为 S3/S4 VM 通过。
