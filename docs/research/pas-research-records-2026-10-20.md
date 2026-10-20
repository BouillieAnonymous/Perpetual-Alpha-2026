# PAS 真实盘口研究记录（2026-10-19–20）

本PR保存三轮已完成的离线研究及失败结果，不接入生产策略或交易权限。

|记录|输入覆盖|主要结果|
|---|---|---|
|[参考价偏离](pas-mean-reversion-2026-10-19/report.zh-CN.md)|首批约9小时53分钟、十资产|保留成本过滤无交易；20bps去过滤48笔，-11.29 USDP情景单位|
|[短周期候选比较](pas-short-cycle-2026-10-19/report.zh-CN.md)|同一批数据|新增动量、本地回归、BTC→ETH、盘口不平衡分钟基准均亏损；做市只做可行性检查|
|[全资产5/10分钟动量、反转与外部领先](all-asset-momentum-2026-10-20/report.zh-CN.md)|约19小时9分钟；旧段拟合，新增约9小时16分钟单列|新增段40个原始方向/持有期/资产组合成本后全部亏损；过滤版零成交；外部领先只有事件时间诊断|

这些是单日、相互关联、已见样本上的研究结果。外部一分钟K线为事后下载，缺少历史接收时间，不能据此声称C策略已完成可执行回测；SP500相关线索的条件后续幅度仍不足以覆盖成本。未模拟已批准账户资本、保证金、清算及全资产估值乘数。

## 归档与复现

- 每轮报告、固定方案、输入指纹、CSV情景/逐笔记录及验证结果一起保留。
- `trades.csv.gz` 为完整逐笔CSV归档，汇总CSV保留为可直接审阅文本。
- `results.json.gz` 是生成的完整 `results.json` 的确定性gzip归档（mtime=0），包含标签、逐笔模拟与来源指纹；Python标准库即可读取。
- 原始公开行情和外部下载文件仍在本地忽略目录，未随PR提交。换一轮导出会增加尾部，不能凭同一路径认定输入相同；先核对报告中的SHA-256。
- 报告中的回放命令在获得同一原始输入后生成未压缩JSON。无网络、钱包、生产服务依赖；五个研究脚本只依赖Python标准库。
- 本轮把旧分支的成本与方向函数抽为 `scripts/pas_research_costs.py`，把ETH试验中的通用计算抽为 `scripts/pas_momentum_core.py`。所有三轮结果在干净main上重跑，除代码指纹外各字段与原研究输出逐项完全相等；没有把未合并的策略producer加入生产代码。
- 源码哈希按文件字节计算。Windows的LF/CRLF转换会改变哈希；复现源码指纹时需保留Git中的LF字节。
- 小时级研究代码和Trend V2等状态说明是研究当时的本地盘点，不代表本PR把那些实现一起提交。

```python
import gzip, json
with gzip.open('docs/research/all-asset-momentum-2026-10-20/results.json.gz', 'rt', encoding='utf-8') as stream:
    result = json.load(stream)
```

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_pas_research_record.py -v
```

研究记录入口为本页；各轮复现命令在相应报告中。完整数字相等证明见 [PR整理核验](pas-research-pr-verification.json)。
