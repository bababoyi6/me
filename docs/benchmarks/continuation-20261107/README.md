# 当前环境补充验收（2026-11-07）

本目录保存后续 CI 的脱敏原始 JSON；`manifest.json` 校验收到的文件字节，不代替工作流提交身份。此前 WSL/Ubuntu x86 批次仍在 `../stable-20261107/`，不拼接为一次测试。

| 批次 | 固定代码 | 结果与范围 |
|---|---|---|
| [ARM64 42352100821](https://github.com/bababoyi6/me/actions/runs/42352100821) | `bcc9a5c321b6f3928d9d9d8a3b5dcd8d06fce97e` | Ubuntu 22.04.5 原生 aarch64，正式包/二进制双摘要、首次安装、生产架构选择、20 条双 SNI 原生协议传输、API 401/403、配置保持、进程清理均通过 |
| [短时资源 42352935615](https://github.com/bababoyi6/me/actions/runs/42352935615) | `9d57ab936ca35987704138123949d59f3dedd077` | Ubuntu 22.04.5，182.0 秒、30 并发/波、900/900 条原生协议连接，FD 14→14，暖机后 RSS 增量 2,404,352 字节，cgroup 峰值 81,424,384 字节，OOM/OOM kill 增量 0 |
| [最终 ARM64 42370196967](https://github.com/bababoyi6/me/actions/runs/42370196967) | `94857f2847bacd199a9955c7cff660022ed75a8a` | 20 条传输、正式包安装和 API 边界再次通过 |
| [最终 x86 42370197039](https://github.com/bababoyi6/me/actions/runs/42370197039) | 同上 | 核心真实安装/升级/回退、systemd 崩溃恢复、健康代理、364/364 传输、双 WG 丢包/抖动/故障恢复和 HAProxy 重载均通过 |
| [三系统修复轮 42369032262](https://github.com/bababoyi6/me/actions/runs/42369032262) | `9dabd6a1b21c1e0b2082379a278dced38080439b` | 三角色安装、重复执行、真实维护命令、旧目录权限迁移/回退、真实 Telegram req_pq/resPQ 切换、正常重启与持久文件一致性均通过；584.9 秒，清理成功 |
| [最终三系统 42370196980](https://github.com/bababoyi6/me/actions/runs/42370196980) | `94857f2847bacd199a9955c7cff660022ed75a8a` | 完整复验再次通过；599.0 秒、三个 boot ID 变化、持久文件一致，宿主 OOM 增量 0，客体/磁盘/临时密钥清理成功 |

ARM 使用 `ubuntu-22.04-arm` 的原生 runner，没有用 x86 或 QEMU 用户态模拟代替。平台由 GitHub 提供：[官方 runner 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。公共仓库标准 runner；本次没有购买资源或改变仓库可见性。

资源测试的 cgroup 包含控制器、HAProxy、Telemt 和模拟 DC；其 CPU 累计增量约 4.17 秒，限制 1 CPU / 512MiB / 无 swap。该值不能与仅核心 CPU 的旧表直接比较。每条仅 4096 字节，目的是检查反复建连/关闭后的资源回收，不是吞吐基准。暖机后仍有约 2.29MiB RSS 增长，只能说明本次有界门槛通过，不能证明长期没有泄漏。未重启之前停止的耐久实验。

这两批使用真实核心、Fake TLS/obfuscated2/padded-intermediate 和模拟 DC，均不是官方客户端验收或真实 Telegram 文件测试。用户本轮明确不提供设备，官方客户端保持 NOT RUN。

复验入口为 `.github/workflows/arm-compat.yml`、`.github/workflows/resource-churn.yml`、`.github/workflows/candidate-system.yml`。三系统工具复用既有 KVM 控制器，版本下载固定到当前提交，不使用未校验 main。最终四组 CI 的提交、状态及原始作业元数据见 `ci-summary.json` 与 `ci-*.json`；逐文件摘要见 `manifest.json`。

最终 x86 负载条件仍为共享私有 loopback，netem 25ms / rate 100Mbps，控制器、模拟 DC、HAProxy 和核心共用 1 CPU / 1GiB / 无 swap。多段 TCP 都经过同一 qdisc，不能把下面数值理解为实际公网速度。API 401/403、旧核心恢复、配置/令牌不变、临时单元清理均已观测；崩溃实验单元 RestartSec=1，不是生产 5 秒策略的恢复承诺。

| 并发 | 方向 | 成功 | Mbps | 握手 p50 / p95 ms | 完成 p50 / p95 ms |
|---:|---|---:|---:|---:|---:|
| 30 | 上传 | 30/30 | 22.670 | 221.75 / 229.58 | 2748.24 / 2765.79 |
| 30 | 下载 | 30/30 | 21.141 | 215.28 / 221.88 | 2882.67 / 2928.69 |
| 50 | 上传 | 50/50 | 25.001 | 223.80 / 235.93 | 4160.35 / 4178.62 |
| 50 | 下载 | 50/50 | 24.087 | 217.91 / 223.80 | 4284.73 / 4325.73 |
| 100 | 上传 | 100/100 | 26.365 | 233.21 / 253.94 | 7912.06 / 7920.94 |
| 100 | 下载 | 100/100 | 25.158 | 225.54 / 241.17 | 8273.35 / 8308.38 |

该批核心最高 RSS 为 27,725,824 字节，核心 CPU 最高约占一个 CPU 的 1.26%，空闲后 FD 为 14。完整数字见 `stable-42370197039/protocol-load.json`。此批未再次测试旧核心，不与其他主机/批次直接相减声称提速；此前同主机新旧基线仍在工程报告中。

WSL root 最终 249 项全通过，日志为 `final-root-tests.log`，没有使用非 root 跳过项代替权限验收。首轮与诊断轮失败证据分别保留在 `system-42352574411/`、`system-42353312993/`；新核心的 Permission denied 根因和修复见工程报告。
