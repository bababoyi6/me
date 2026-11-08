# 排队、同负载与资源实验原始证据

- 42495712435：四策略探索pilot，缺探测子进程CPU，保留原analysis及严格analysis-rechecked；不能推荐策略。
- 42498243533：20Mbps/流同负载四轮AB/BA，仅用于机制检查。
- 42499438481：首个TCP容量对照超时，尚无计量负载；失败和清理记录保留。
- 42499962853：30/50/100并发4MiB资源通过，未计量逐流延迟，原analysis及严格复核保留。
- 42501930660：增加逐流分位数和RSS采样后的定向复验，720/720、OOM0。

每个ci.json记录各自source commit；manifest.json验证原始文件；sustained-timing-summary.json从最后一轮metrics直接提取。native-only布局历史official_release默认字段不参与核心选择，实际版本/二进制SHA与native_manifest一致为3.5.17。无生产调参、无官方客户端或24小时结论。完整条件/预登记/限制见[计划](../../QUEUE-STUDY-20261108.md)和[结果](../../FINAL-ACCEPTANCE-20261109.md)。
