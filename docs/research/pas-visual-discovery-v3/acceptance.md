# Chrome GPT extension acceptance — V3

日期：2026-11-05。使用用户指定的Chrome GPT扩展；没有Edge、Windows Computer Use或替代浏览器自动化。临时仅绑定127.0.0.1的研究目录预览可达；本轮没有修改代理、防火墙、浏览器权限或生产服务。

## 已在真实Chrome确认

- [x] V3实际加载；初始仅BTC/ETH两个bundle，打开矩阵后才加载十市场。
- [x] 十个市场逐一切换，任意单市场可看；四资产比较，勾选第五个被拒绝且选择仍为四个。
- [x] 按市场分组、原价格单位/历史元数据精度、raw event/available及报价年龄可见。
- [x] A矩阵90个off-diagonal单元（45对称关系）显示n/窗口；点击BTC/HYPE格正确设X=BTC、Y=HYPE。
- [x] B的全45对门槛不足时90格全部MISSING；配对图明确INSUFFICIENT_DATA，不凭价格可画而放行。
- [x] USDJPY/EWJ任意方向及60m切换；最终新增30m尺度实际显示Slope30，没有静默退回15m。
- [x] 固定UTC缺口范围为10/26 02:10—03:00的51点；截图可见空白断线，未跨20分钟缺口连接。
- [x] 图上点击固定历史光标、左右箭头移动一分钟；侧栏UTC同步；画布双击恢复完整15,815点。
- [x] 实际费用/滑点默认为UNKNOWN，假设成本为空。显式输入BTC fee/slip=1/2、ETH=3/4 bps并点击Apply，双腿情景从touch22.469变为42.469 bps，gross均值从11.234变为21.234；随后恢复空预算。没有净收益字段。
- [x] 成本摘要随历史光标更新；仅应用输入后计算假设，避免失焦未生效。
- [x] 浏览器控制台未见error/warn；实测短操作未出现页面冻结。页面观察到4市场render68ms（单次UI读数，不是严格性能基准）。
- [x] 默认实际浏览器宽度下布局可用，窄于1100时特征栏移至主图下方；无需把所有图无限纵向铺开。

截图在release/chrome-overview.jpg、chrome-gap.jpg；静态科学图另列coverage-overview、matrix-asof、prices-overview.png。

## 仍需人工确认 / 未宣称通过

- [ ] 纯鼠标悬停、跨图拖动缩放的完整手势验收：当前Chrome扩展文档未提供独立hover/mouse-drag接口；已在真实Chrome测试点击/键盘/双击，drag处理逻辑另由mock验证，二者不混称。
- [ ] 更大样本、不同设备/宽度与长时间操作的系统性能基准。
- [ ] 整页/矩阵单独截图：Chrome CDP大区域capture在5秒超时；矩阵DOM/数值/点击已真实验收，保留成功的overview/gap截图，不把失败截图列成交付。
- [ ] Chrome直接file://模式端到端：工具策略只允许HTTP/HTTPS。本轮通过本机HTTP确认，无外部CDN；相对本地script lazy加载设计和mock通过，直接文件打开仍待人工。

这是一份PARTIAL_ACCEPTANCE，不把上述空项勾选。用户可直接在Chrome双击整个release目录中的dashboard.html继续人工审阅。目录/数据缺失会明确报错，不外部补齐。

## 本轮修复披露

1. 旧本地十资产EDA的book-only loader漏掉两条normalized book request-error，V3按完整管线各减少USDEUR/USDJPY一个quote点；收益不变，旧证据不改。
2. Chrome真实测试中预算失焦更新不可靠，新增明确Apply按钮，并让成本摘要随光标变动。
3. 增加真正Slope30使统一尺度选择无静默回退；矩阵说明同时保留已刷新as-of和当前光标时刻，防止把停留矩阵当当前值。

以上为数据/交互语义修复，没有改变收益阈值、进行策略搜索或新统计检验。
