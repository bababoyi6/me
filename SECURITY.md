# Security Policy

## Supported version

- Telemt Dual-Hop：`1.2.1`
- 配置 schema：`5`
- Telemt：`3.5.5`

旧版 `1.0.x`/`1.1.x` 与本版的部署码、服务布局及运行模式不兼容，必须完整卸载后再部署。

## Sensitive values

Join Code、回执码、MTProxy 链接、Telemt Secret、WireGuard 私钥和 PSK 都应视为敏感信息，不得提交到 GitHub Issue、截图或公开聊天。

Join Code 中的 HMAC 可检测复制损坏，但登记令牌也包含在同一代码内，因此它不替代可信传输；请只从入口 SSH 会话直接复制到对应后端。回执不包含登记令牌，由入口保存的令牌验证，成功录入后令牌会立即轮换。

状态文件为 `root:root 0600`。WireGuard 私钥只保存在对应服务器。Telemt 配置和 API Token 为 `root:telemt 0640`；HAProxy 配置不含 Telemt Secret。

Telemt API 仅监听 `127.0.0.1:19091`，使用随机 256-bit Bearer Token 并启用只读模式。Telemt 只监听 WireGuard 地址 `:24431`，只信任入口隧道 IP 发来的 PROXY Protocol v2。健康代理只监听对应 WireGuard 地址 `:19101`。

## Exposure boundary

入口公网仅开放 TCP 443；后端公网只需要从入口公网 IP 到 UDP 51821/51822。TCP 24431、19091、19101 不得进入云安全组公网规则。

HAProxy 只读取 ClientHello 中本来就是明文的 SNI，不终止 TLS。只有到 Telemt 的 WireGuard 路径携带 PROXY Protocol v2；到真实 `apple.com:443` 或 `gs.apple.com:443` 的回落连接绝不携带该头。`gs.apple.com` 的 Apple 私有证书链仅使用 Apple 官方发布且固定 SHA-256 指纹的 Apple Inc. Root 做局部验证，不修改系统全局信任库。

未知/无 SNI 探测会回落到真实 Apple TLS，但 VPS IP 的 ASN、路由和长期流量行为无法伪装成 Apple。Fake-TLS 与回落能减少明显暴露，不能承诺规避所有未来的主动探测、统计分类或 IP 封锁。

两条链接共享入口 IP、TCP 443 和 HAProxy，是共同故障域。DDoS、入口宕机、运营商故障或入口 IP 被封会令两条线路同时失效。

## Runtime hardening

- 后端单 Telemt 进程同时承载两个用户，避免同机双实例上游已知不稳定风险；
- Direct-DC 模式不使用 Middle Proxy/ME writer；因此不支持 sponsor/ad channel；
- systemd 服务使用最小权限、只读系统目录、私有临时目录、限制能力集和无限重启策略；
- BBR+fq 必须实际加载并验证，否则安装失败；
- 健康代理同时要求 Telemt ready API 与 5 个 Telegram IPv4 DC 目标全部可达；
- HAProxy 正常会话不记录访问日志，避免 Secret 相关元数据和高流量磁盘 I/O；
- 安装失败执行回滚，卸载仅删除脚本自行创建的对象。

## Reporting

公开 GitHub 仓库前请启用 Private vulnerability reporting。私有报告中也应对真实公网 IP、Secret 和部署码做脱敏。
