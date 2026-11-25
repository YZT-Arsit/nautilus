# 开源源码复现

五个浅克隆由模块目录的 `references/` 保留在 Mac 上，版本与源码入口见 `reproduction.csv`。Windows 训练使用同一份研究模块源码。官方股票工作流在 Mac 独立环境执行，研究产物位于 Nautilus 的 `outputs/crypto_alpha/v1/reproduction`。

- Qlib 的 Alpha158 默认定义包括 9 个 K 线公式、4 个当期价格公式和 145 个滚动公式。日频移植验证 K 线、价格、ROC、MA、STD、MAX、MIN 的 38 个核心公式，计算前要求 UTC 自然日完整。
- Alpha360 的六组原始序列各带 60 个偏移。复现脚本实际解析并计算 360 条公式，验证首尾归一化，主加密模型采用分钟因子库。
- Qlib 默认标签为下一日收盘至再下一日收盘。Qlib 的 CSRankNorm 使用 `3.46 × (rank_pct − 0.5)`，CSZScoreNorm 使用样本标准差。主加密模型的排序标准化使用实际截面均值及总体标准差。
- DataHandlerLP 在数据层管理学习与推理口径，DatasetH 执行分段读取，SignalRecord 分别保存预测和原始标签。RollingGen 支持按交易日滚动并截短训练尾部。加密模块以真实 Label 成熟时刻执行年度截断。
- Alphalens 的组合权重、组合收益、分位收益、换手和 Rank IC 数值对照执行浅克隆中的函数体。适配 pandas groupby 新增的一层索引，函数体来自原仓库。
- AlphaGen 的单因子、线性组合与因子间 IC 使用逐截面相关性的均值。加密筛选使用拟合区间的平均截面相关性排除重复因子。
- FreqAI 的 DataDrawer 和 DataKitchen 从历史分段管理推进到模型持久化。RD-Agent 的 QlibFactorRunner 使用分离的因子缓存、去重、实验配置和回测记录。
