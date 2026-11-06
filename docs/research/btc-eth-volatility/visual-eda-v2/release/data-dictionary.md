# V2 数据字典

固定样本 2026-10-19 13:43—2026-11-03 10:00 UTC；PAS testnet异步REST。A新有效事件零收益保留，B严格活跃报价。所有时序字段仅历史/当前，未来标签只在事后事件页面现场求和。CSV缺失为空；HTML为null。

|字段|单位|窗口|公式与资格|A有效|B有效|
|---|---|---|---|---:|---:|
|btc_r|bps|1|10000 log(Mid_t/Mid_t-1)|21332|17539|
|btc_mid|USDP|0|valid as-of Mid|21352|21352|
|btc_spread|bps|0|10000(ask-bid)/Mid|21352|21352|
|btc_age|seconds|0|t - raw event time (includes expired selected book)|21377|21377|
|btc_receipt_age|seconds|0|t - raw receipt time|21377|21377|
|btc_ret5|bps|5|10000 sum signed log returns; complete window|21260|11440|
|btc_rv5|bps|5|10000 sqrt(sum(r²)); complete, not annualized|21260|11440|
|btc_ret15|bps|15|10000 sum signed log returns; complete window|21080|6483|
|btc_rv15|bps|15|10000 sqrt(sum(r²)); complete, not annualized|21080|6483|
|btc_ret30|bps|30|10000 sum signed log returns; complete window|20823|4051|
|btc_rv30|bps|30|10000 sqrt(sum(r²)); complete, not annualized|20823|4051|
|btc_ret60|bps|60|10000 sum signed log returns; complete window|20338|2107|
|btc_rv60|bps|60|10000 sqrt(sum(r²)); complete, not annualized|20338|2107|
|btc_slope5|bps/min|5|OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid|21260|11440|
|btc_slope15|bps/min|15|OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid|21080|6483|
|btc_slope60|bps/min|60|OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid|20338|2107|
|eth_r|bps|1|10000 log(Mid_t/Mid_t-1)|21332|18675|
|eth_mid|USDP|0|valid as-of Mid|21352|21352|
|eth_spread|bps|0|10000(ask-bid)/Mid|21352|21352|
|eth_age|seconds|0|t - raw event time (includes expired selected book)|21377|21377|
|eth_receipt_age|seconds|0|t - raw receipt time|21377|21377|
|eth_ret5|bps|5|10000 sum signed log returns; complete window|21260|13553|
|eth_rv5|bps|5|10000 sqrt(sum(r²)); complete, not annualized|21260|13553|
|eth_ret15|bps|15|10000 sum signed log returns; complete window|21080|8338|
|eth_rv15|bps|15|10000 sqrt(sum(r²)); complete, not annualized|21080|8338|
|eth_ret30|bps|30|10000 sum signed log returns; complete window|20823|5120|
|eth_rv30|bps|30|10000 sqrt(sum(r²)); complete, not annualized|20823|5120|
|eth_ret60|bps|60|10000 sum signed log returns; complete window|20338|2601|
|eth_rv60|bps|60|10000 sqrt(sum(r²)); complete, not annualized|20338|2601|
|eth_slope5|bps/min|5|OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid|21260|13553|
|eth_slope15|bps/min|15|OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid|21080|8338|
|eth_slope60|bps/min|60|OLS log Mid vs UTC minute 0…w, w+1 prices; all selected-mode returns valid|20338|2601|
|corr60|unitless|60|Pearson on jointly valid clock-minute pairs; >=90% pairs; both centered SS>1e-18|21250|5762|
|beta60|ETH/BTC|60|Cov(ETH,BTC)/Var(BTC); >=90% pairs; both centered SS>1e-18|21250|5762|
|pair_n60|pairs|60|joint valid pair count in nominal clock window|21319|21319|
|corr240|unitless|240|Pearson on jointly valid clock-minute pairs; >=90% pairs; both centered SS>1e-18|21139|3642|
|beta240|ETH/BTC|240|Cov(ETH,BTC)/Var(BTC); >=90% pairs; both centered SS>1e-18|21139|3642|
|pair_n240|pairs|240|joint valid pair count in nominal clock window|21139|21139|
|corr1440|unitless|1440|Pearson on jointly valid clock-minute pairs; >=90% pairs; both centered SS>1e-18|19939|1178|
|beta1440|ETH/BTC|1440|Cov(ETH,BTC)/Var(BTC); >=90% pairs; both centered SS>1e-18|19939|1178|
|pair_n1440|pairs|1440|joint valid pair count in nominal clock window|19939|19939|
|h03_beta|ETH/BTC|1440 excluding signal60|PR38 returns [i-1500,i-60), >=1296 pairs; past-only|19878|1178|
|h03_n|pairs|1440|PR38 beta sample count|21378|21378|
|residual60|bps|60|ETH past60 - H03 beta × BTC past60; no intercept; not P&L|18899|87|
|residual_z|historical SD|1440|(e_t - mean(e_[i-1440,i)))/sampleSD, >=1296 valid, sd>1e-6 bps; excludes current|15850|0|
|z_n|residuals|1440|prior residual count; overlapping, dependent|21378|21378|
|rv_ratio15|ETH/BTC|15|complete RV_ETH / RV_BTC; denominator>1e-12 bps|21071|4126|
|rv_ratio60|ETH/BTC|60|complete RV_ETH / RV_BTC; denominator>1e-12 bps|20338|1093|
|btc_cost_rt|bps|0|current full spread + 2×9 fee + 2×5 slip BUDGET|21352|21352|
|eth_cost_rt|bps|0|current full spread + 2×9 fee + 2×5 slip BUDGET|21352|21352|
|two_leg_unweighted|bps per-leg notional|0|spreadBTC+spreadETH+56; two equal-notional legs, NOT gross normalized|21352|21352|
|gross_cost_rt|bps total gross|0|H03 weighted quoted spread +28 fee/slip BUDGET; no executable net|19853|1176|
|residual_magnitude_gross|bps total gross|60|abs(historical residual)/(1+abs(beta)); magnitude only, not payoff|18899|87|

缺失原因详见 metric-catalog.json：warmup、coverage、small_variance、invalid_return_window、current_residual_missing、historical_coverage、small_sd。未插值或补零；协方差窗口允许显式配对删缺并展示n/w，累计收益与RV始终要求全窗完整。

Price index 为共同13:44基点=100；统计数值CSV完整精度，HTML显示payload约8位有效数字。历史标准差不是置信区间；窗口高度重叠。