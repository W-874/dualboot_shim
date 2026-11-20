# 与遗产快照逐文件对比

原文件：`.heritage-snapshot/guest-entry-20261120-194822.tar.gz`，SHA-256 `72eaecaf8a01756b21ea371969c70e0a816e73547b249b44039423bfed0ab691`。首次提交 `daa649b99e537aa13632665343767c91683baf4a` 原样保留共 18 个文件（逐文件字节及可执行位比对）；没有用 recovery HEAD 冒充未跟踪实现。

| 当前变化 | 原因 |
|---|---|
| `Cargo.toml` | 两条相对 path 指向本仓共享 crate 镜像。 |
| `src/main.rs` | 注册 `profile`。 |
| `src/policy.rs` | 将身份规则移至 `profile.rs`，软硬件控制器值和固件 fstab 读嵌入 TOML；保留私有卷和启动参数通用规则与原断言。 |
| `src/boot.rs` | 调用 profile DT/控制器适配。 |
| `src/storage.rs` | 从 profile 选择格式协议；原协议授权和持久时序不变。 |
| 新增 `src/profile.rs` | 严格解析嵌入描述、保留历史身份策略、增加负例和合成板拓扑测试。 |

其余 13 个文件不变（含全部 11 个 fixtures 及 `state.rs`、`sys.rs`），无删除。复核本仓提交内容：

```sh
python3 tools/verify-heritage.py ../.heritage-snapshot/guest-entry-20261120-194822.tar.gz
```

`dualboot-src-20261120-194822.tar.gz`（SHA-256 `ccf87fb4a5ecd4d275f535a178d674e2ce71d8d983b1b302e418db14dce48866`）也原位保留，记录 system-init、产品原树和 C 原型；不把更晚的 live 源码冒充 r26e 当次构件。
