# 健康恢复与混合体验原始证据

详见[工程报告](../../STABILITY-MIXED-20261109.md)。CI绿色不等于联合性能放行；qualification.json保留全部条件结果，production_approved=false。

metrics.json.gz是原CI metrics.json的无损gzip，raw-compression.json记录解压后原字节SHA和大小；原CI analysis.json和更严格的analysis-strict-directions.json分开保存，后者provenance注明Windows纯数据分析及分析器SHA。失败、取消、初始错误与最终通过按运行号单列。manifest.json覆盖本目录，不包含自己。没有临时二进制、凭据、磁盘或未脱敏终端会话。
