# 入口共享预算与原子发布修复证据

见[完整报告](../../SHARED-ENTRY-20261109.md)。日期覆盖北京时间2026-11-09至11-10，目录沿首次登记日期保持稳定。

`qualification.json`保留42651841645四对联合门槛全失败、七项双WG预算/撤销证明全通过的结果。虽延迟改善，上传每对回退超过5%，model_gate_pass和production_approval均false，所有活跃IFB原型与接入参数已撤回，源码历史ec8de62可审查。前三次前置失败为零性能批次，不伪装一次成功。

十九项真实WG恢复中的publication_crash_resumable验证新队列记录在首次发布后被强杀仍保持单链接并可重试；新增核心配置权限迁移的同类强杀检查计入后续318项完整root回归，准确source由每个运行目录和交付回执单列。

`manifest.json`保存逐文件大小/SHA256，`raw-compression.json`保存原始metrics解压后摘要；gzip只压缩，不删除失败样本或重写分析结果。`accepted=true`是执行/完整性条件，不等于性能放行。

没有官方客户端、公网三VPS、24小时、抗封锁、1Gbps或ARM新调用的生产验收结论。
