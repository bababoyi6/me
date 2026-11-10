# 3.5.17 兼容验收原始证据

本目录逐运行保存CI metadata和原始报告，包括失败/取消；不是同一个源码的一次测试。准确commit见每个ci.json；回归checkout/head树一致证明见42501933849/root-result.json。qualification.json是本轮索引，manifest.json逐文件记录字节数和SHA256。

当前结论、命令、结果与未验证边界见[11月9日报告](../../FINAL-ACCEPTANCE-20261109.md)。previous-3.5.16-lock.json保留旧版准确锁；20,160条历史矩阵不属于3.5.17。运行42495175073为原生实验，不能当作官方发行资产或安装器支持。
