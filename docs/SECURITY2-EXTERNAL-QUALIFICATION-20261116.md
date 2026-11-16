# security2.2 补充验证（2026-11-16）

固定候选 `telemt 3.5.5+tdh.balanced2.security2.2` 的 ELF SHA256 为 `0b901711296707c23da7e8edda5509f108bd351f6e4b9c72f0f69a1b1fe71388`。以下手动 CI 均重建并核对同一 SHA；未修改正式安装器或默认队列。

- [双隧道功能 CI 43791169379](https://github.com/bababoyi6/me/actions/runs/43791169379)：100 ms 私有三节点拓扑，真实 HAProxy、两条 WireGuard PSK 隧道和两个候选后端。两个域各走一条隧道，7 次双向合成传输 SHA256 正确，两个隧道的字节计数完整，故障切换和恢复通过。有效载荷约 1.06 MB，不用于吞吐结论。
- [公网 TLS 源 CI 43794144964](https://github.com/bababoyi6/me/actions/runs/43794144964)：候选从 `apple.com`、`gs.apple.com` 真实公网源取得并以 0600 权限持久化非默认 `merged` 特征；两个域的上下行合成传输 SHA256 正确，进程清理及 OOM 检查通过。客户端与 DC 仍是本地合成夹具。
- [共享容量 CI 43795365486](https://github.com/bababoyi6/me/actions/runs/43795365486)：两条私有隧道共享 100 Mbps 入口链路，配置 100 ms RTT。与正式 balanced2 按 ABBA 顺序进行四轮，每轮双向各两条并发传输，候选/基线下载中位比为 `1.00023`、上传为 `0.99929`，超过事先登记的 90% 保留门槛。四轮各传 34,078,720 字节且完整性检查通过。

前两项工具首轮分别因 systemd 单元命名和 Python 导入路径在实验前退出：[公网源首轮](https://github.com/bababoyi6/me/actions/runs/43791434087)、[容量首轮](https://github.com/bababoyi6/me/actions/runs/43793300474)。修正后另行重跑，失败回执保留。完整 JSON 回执在本地 `lab-control/evidence/2026-11-16-security2-external/`。

这些结果证明候选在上述有界功能和 100 Mbps 共享容量初筛中与已发布核心性能相当，也证明两个公网 TLS 源可获取。真实 Telegram 客户端、生产公网三机链路、千兆容量、长期稳定性与抗封锁效果仍没有验证。正式管理器 v1.7.2 保持 balanced2 默认核心；是否发布候选核心资产及改默认值是独立步骤。
