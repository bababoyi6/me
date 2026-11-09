# 后端队列接入证据

完整解释见[工程报告](../../MANAGED-QUEUE-20261109.md)。目录按原始CI运行号保存，不筛选失败。[qualification.json](qualification.json)保留四对原门槛的失败结果与18项恢复检查的成功结果；整体production_approval=false。

- 42589798524唯一一次完整性能：原生17固定核心、仅后端整形、100ms/200Mbps/30逻辑混合。上传和延迟相对改善，但新连接绝对预算失败，不能晋级。
- 42592010385最终真实WG/systemd生命周期：18项、私有命名空间、应用/撤销SIGKILL恢复及12秒钩子截止。measure明确SKIPPED，不计性能成功。
- 42592016609最终代码完整root316项回归，无跳过。后续仅文档/证据提交由普通基础CI复核，不复跑矩阵。
- 两项首轮失败与中间source结果全部保留。不同source不可混称为同一次完整验收；自动ARM结果不是新队列ARM证明。

`manifest.json`覆盖原始文件和其他记录的字节大小/SHA256；大型原始metrics只无损gzip压缩，`raw-compression.json`保存压缩前字节摘要，可解压逐字节复核。`accepted=true`只表示执行/数据完整，性能以analysis/qualification里的联合门槛为准。

这不是Telegram官方客户端、公网跨地区三VPS、24小时稳定、ARM队列、1Gbps或抗封锁验收。
