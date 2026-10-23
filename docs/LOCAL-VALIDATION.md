# 本地补充验证：完整代理传输与维护安全

最新范围已按用户要求改为1.5.0首次安装，默认使用audit2的x86_64核心，见[新安装验收](FRESH-INSTALL-1.5.0.md)。本文保留早期管理器验证，不代表最新核心启用状态。

本文记录10月21日的WSL补充验证。10月22日新增的原生Ubuntu后端编译补丁、高RTT吞吐对照和完整WG双后端模拟见[后端核心验证报告](NATIVE-BACKEND-VALIDATION.md)，应优先结合最新证据解读本文的“尚未显示吞吐收益”等历史结论。

日期：2026-10-21。本轮以 `c4e2111` 为起点在 `audit/local-e2e` 工作区继续开发；依用户要求不推送、不合并。本文及相邻新增测试属于本地候选，运行 `a update` 不会取得这些尚未发布的修改。

后续关于splice采用判断、5分钟splice长传、Telemt固定TCP缓冲的源码/运行值和后端优先方案，见 [入口与后端复核](BACKEND-AND-SPLICE.md)。本地只读诊断已补上相应缓冲/窗口受限聚合指标。

## 1. 本轮新增的生产代码

仅加固管理器的维护锁：避免用输出重定向截断已有文件，拒绝符号链接、FIFO、多硬链接和可被其他用户写入的锁文件。首次创建使用私有临时文件和不覆盖目标的原子链接；打开后核对路径与文件描述符指向同一 inode。保留旧锁文件 inode，因此能与旧管理器互斥；兼容旧版 root 所有的 0644/0600 锁和 Ubuntu 默认的 sticky `/run/lock`。

7 项回归验证原版截断行为、新版拒绝异常对象、现有内容/inode 保持、创建权限及旧管理器持锁时阻止新版。使用专用临时文件，不修改 `/run/lock`。这是维护路径的防御加固；Ubuntu 的内核文件保护会影响普通用户能否利用旧行为，不能据此声称默认系统必然存在可利用的提权漏洞。

生产配置模板、节点协议、Secret、端口、WireGuard、主备、Telemt 固定版本以及更新语义保持不变。锁检查失败时管理器停止本次维护，不重启数据服务；不自动删除异常锁文件，以免破坏他人文件或失去互斥。

## 2. 新的真实二进制传输验证

`tests/e2e_lab.py` 增加以下受控路径：

```text
合成客户端 → 实际 HAProxy → 官方 Telemt → 仿真 DC
                SNI / PROXYv2 / FakeTLS HMAC / obfuscated2 AES-CTR / padded intermediate
```

同时覆盖绕过 HAProxy 直连测试 Telemt 的对照路径、两个合成 Secret、分段 ClientHello、双向文件和 4 并发。仿真 DC 逐连接解码 Telemt 产生的真实 DC nonce，双向核对文件 SHA256。与只验证 FakeTLS 握手相比，现在实际经过 Telemt 的解密、Direct 转发和加密数据路径。

数据是合成负载，不是已登录 Telegram 账号的消息或文件。没有实现账号授权、消息 AES-IGE 或 CDN 下载。仿真 DC 使用官方 `dc_overrides`，仅作用于临时配置；mock 模式将所有 DC 索引指向回环地址。Telemt 自身仍可能访问 TLS 伪装站点取得握手资料。后台启动 TCP 探测不发送 nonce，单独计数；部分 nonce、错误协议、文件校验失败仍使实验失败。该行为已对照 [3.5.5 upstream 源码的 ping_single_dc](https://github.com/telemt/telemt/blob/3.5.5/src/transport/upstream.rs) 核实。

另有显式 `--real-dc` 模式，经过相同代理层向真实 DC2 发送 3 次公开 `req_pq_multi`，核对 `resPQ` 和请求 nonce。它不登录账号、不创建授权密钥，不做文件测速，也没有完成服务端 RSA 身份验证，因此不能把这个响应称为完整 MTProto 身份认证。

生产不会运行这些工具。实验只绑定回环地址，子进程在 root 启动时降为 nobody，退出时终止本次子进程、关闭测试监听并删除专用目录。入口参数只接受两份已核对官方归档校验值的 x86_64 GNU 二进制，并在复制后再次核对文件摘要。ARM 传输行为没有在本轮验证。

## 3. 环境与解读边界

本轮运行于 WSL2 内核 `6.18.40.1-microsoft-standard-WSL2`、HAProxy `3.2.9-1ubuntu2.2`。它不同于临时 VPS 的 Ubuntu 22.04 / 5.15 / HAProxy 2.4.30。不能用这批本地结果替代此前的 [VPS 原生验证](VPS-VALIDATION.md)，也不能代替生产三机、WireGuard 公网隧道或真实 Telegram 文件测试。

Telemt 3.5.5、3.5.14 各完成 12 次真实加密合成传输；3.5.5 关闭 fast_mode 的实验对照也完成 12 次。两个版本各 3 次真实 DC2 公开协议响应通过。所有测试配置保持原字节内容。原始记录放在本文所链接的本地数据文件中，不包含服务器地址、凭据或账号。

记录：[3.5.5](benchmarks/local-e2e-355.json)、[3.5.14](benchmarks/local-e2e-3514.json)、[关闭 fast_mode 的 3.5.5](benchmarks/local-e2e-355-no-fast.json)、[3.5.5 真实 DC 响应](benchmarks/local-e2e-public-355.json)、[3.5.14 真实 DC 响应](benchmarks/local-e2e-public-3514.json)。公开响应各仅 3 个样本，而且测试没有交错；不据此比较两个版本的网络时延。

另外完成 **48 组完整加密转发 A/B**，对应 120 次 SHA256 通过的传输、合计 3.6 GB 合成数据。[逐轮记录与汇总](benchmarks/local-e2e-benchmark.json)保留全部样本。固定 fast_mode=true 的结果如下；表中范围是 3 轮最小值至最大值，单位 Mbps，限速上限为 200 Mbps：

| 项目 | 3.5.5 中位数（范围） | 3.5.14 中位数（范围） |
|---|---:|---:|
| 单连接下载 | 195.48（195.32–195.87） | 195.96（195.83–196.13） |
| 单连接上传 | 195.95（195.93–196.17） | 195.93（195.92–195.93） |
| 4 连接下载 | 195.44（195.24–195.49） | 195.76（195.75–195.98） |
| 4 连接上传 | 196.16（196.16–196.25） | 196.16（195.89–196.43） |

该限速下没有大幅吞吐收益。下载的 Telemt 进程 CPU/GiB 中位数从单连接 2.577 降至 2.147、4连接从 2.147 降至 2.004 秒/GiB；上传均约 3.150 秒/GiB。一次传输只有 75 MB、CPU tick 为 10 ms，单 tick 就相当于约 0.143 秒/GiB，且部分样本波动重叠。因此这只是需要进一步验证的 CPU 成本信号，不能声称生产速度提高 17% 或升级必然有益。关闭 fast_mode 的 8 项分组汇总也没有呈现一致吞吐优势，生产继续保持 true。

本轮最终检查：Bash 语法、ShellCheck、内置自检、Python 编译、**46 项自动回归**和实际 HAProxy SNI/agent 主备/重载测试全部通过。其中 7 项覆盖维护锁、9 项覆盖实验传输的已知向量、分段、上限、截断及未知二进制拒绝；旧版更新兼容、链接/状态/密钥保持和失败回退测试仍通过。实验结束后核对本轮代理子进程为 0、`/tmp/tdh-e2e-*` 目录为 0。没有连接生产 VPS。

## 4. 复现与依赖

仅在空闲 Linux 实验环境运行；`cryptography` 只用于实验和 CI，不是管理器依赖。实际传输工具按 [Telegram 官方传输文档](https://core.telegram.org/mtproto/mtproto-transports) 构造 obfuscated2/padded intermediate；额外用 [NIST SP 800-38A F.5.5](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-38a.pdf) 已知向量验证 CTR 与分段更新，避免只用自写客户端和自写服务端互相证明。

`T355`、`T3514` 为已经校验的官方解压二进制路径，`OUT` 是私有输出目录。以下命令不属于生产升级步骤：

```bash
timeout 60 python3 tests/e2e_lab.py --telemt "$T355" --output "$OUT/e2e-355.json"
timeout 60 python3 tests/e2e_lab.py --telemt "$T3514" --output "$OUT/e2e-3514.json"
timeout 60 python3 tests/e2e_lab.py --telemt "$T355" --no-fast --output "$OUT/e2e-355-no-fast.json"
timeout 60 python3 tests/e2e_lab.py --telemt "$T355" --real-dc --output "$OUT/public-355.json"
timeout 300 python3 tests/e2e_benchmark.py --baseline "$T355" --candidate "$T3514" \
  --rate-mbps 200 --seconds 3 --rounds 3 --output "$OUT/e2e-benchmark.json"
```

基准工具交替版本和 fast_mode 顺序，区分单连接/4连接、上传/下载，最大聚合应用限速 200 Mbps、每项最多 3 秒、最多 3 轮。CPU 数据仅包含对应进程的用户态与系统态时间，不包含所有内核工作线程、仿真客户端和 DC；3 轮 P95 就是最大样本，不能视为稳定的尾延迟估计。限速实验接近上限只能证明该上限下正常工作，不能证明程序最大能力。

## 5. 更新与回退的交付边界

本轮没有提交、推送或合并远端变更。候选发布到 main 之后，入口、backend1、backend2 各执行一次 `a update` 即可更新管理器，无需重装或额外应用参数。它不会重启服务，不会自动升级已运行的 HAProxy、Telemt 或 WireGuard，也不会修改用户配置；本轮没有需要迁移的数据面性能参数。

从带备份功能的新管理器后续更新，可用 `a update-rollback` 回退管理器。原版 1.3.1 首次更新本身没有备份动作，不能承诺这一步已有一键回退；固定提交、校验和及首次手动恢复步骤见 [审计手册第6节](PERFORMANCE.md#6-只运行-a-update-的兼容升级边界)。两种回退都不需要改 Secret、链接或代理运行配置。

已验证的软件对照没有显示大幅吞吐收益；是否能靠线路、地区或主机资源获得明显提升，仍需要生产使用时的三段证据。当前也不能判定已经达到物理上限。安全优先的可交付结果是维护加固、只读诊断及可复现实验，不是未经证实的加速参数。
