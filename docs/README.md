# Telemt Dual-Hop 文档

| 需要做什么 | 文档 |
| --- | --- |
| 安装管理器 | [固定版本安装入口](../README.md) |
| 更新现有节点 | [管理器更新](MANAGER-UPDATE.md)、[v1.7 升级与回退](UPGRADE-1.7.md) |
| 选择或回退后端核心 | [v1.7.3 默认核心、架构差异和已有后端](CORE-DEFAULT-SECURITY2-20261116.md) |
| 排查运行问题 | [故障排查](TROUBLESHOOTING-1.7.md)、[后端与 splice](BACKEND-AND-SPLICE.md) |
| 了解架构和验证范围 | [v1.7 架构](ARCHITECTURE-1.7.md)、[安全核心扩展验证](SECURITY2-EXTERNAL-QUALIFICATION-20261116.md) |
| 查性能与可靠性的优化优先级 | [优化审查与验收计划](OPTIMIZATION-REVIEW-20261126.md) |
| 查历史发布和原始证据 | [GitHub Releases](https://github.com/bababoyi6/me/releases)、[性能与验收记录](benchmarks/) |

## 历史 GitHub Actions

以下四项 CI 只由已经合并的实验分支推送触发。配置可在整理前的固定提交中查阅，运行记录仍按原编号查找。

| 工作流 | 历史配置 |
| --- | --- |
| 候选核心安装 | [candidate-install.yml](https://github.com/bababoyi6/me/blob/2891386fa8cb0bae7a6b2068c9f609fe3ba34630/.github/workflows/candidate-install.yml) |
| 候选隧道路径 | [candidate-path.yml](https://github.com/bababoyi6/me/blob/2891386fa8cb0bae7a6b2068c9f609fe3ba34630/.github/workflows/candidate-path.yml) |
| balanced2 安装交付 | [core-delivery.yml](https://github.com/bababoyi6/me/blob/2891386fa8cb0bae7a6b2068c9f609fe3ba34630/.github/workflows/core-delivery.yml) |
| 早期中继候选构建 | [relay-candidate.yml](https://github.com/bababoyi6/me/blob/2891386fa8cb0bae7a6b2068c9f609fe3ba34630/.github/workflows/relay-candidate.yml) |
