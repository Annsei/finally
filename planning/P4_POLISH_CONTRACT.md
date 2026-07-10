# P4 契约 — 锦上添花：情绪指数 · 相关性热力图 · 成交日历 · 选手主页（V2 §四）

前置：P1-P3 已落地（19b489b）。基线：**pytest 1253 / jest 414（53
suites）/ E2E us 30 + cn 12 —— 一字不改全部通过**；`next build` 干净。
范围：四项增强，全部挂在既有页面（/market、/journal、/arena→新
/player 页）。V2 至此收官。

核心不变量（对抗验证重点核查）：
- 无新导航项；既有页面既有区块 DOM/testid 不动，四项全部**增量追加**。
- **相关性配色不走涨跌方向色**（相关性无涨跌语义，CN 翻转不适用）：
  正相关 blue `#209dd7`、负相关 purple `#753991`、强度=|r|——契约
  钉死，防验证员误报"硬编码 hex"。
- 情绪指数进入 **chat/briefs 的上下文组装**（context），**严禁碰
  SYSTEM_PROMPT 常量**（CN-3 字节钉死）；已核实 context 无字节钉死
  测试。LLM_MOCK 输出不受 context 影响，字节不变。
- Leaderboard 名字包 `<Link>` 保留原文本节点（getByText 兼容；
  SymbolLink 先例证明 next/link 在 jest 下可渲染）；若任一基线 jest
  仍失败 → 回退为行 onClick 导航，不改测试。
- 选手主页只暴露**概要**：权益曲线 + 持仓权重%，绝不暴露
  数量/成本/现金明细；隐私开关关闭 → 仅返回 `{user, public:false}`。
- 既有测试一字不改全绿。

---

## 1. 市场情绪指数（后端 + /market 页 + AI 引用）

- **GET `/api/market/sentiment`**（新，market.py，无鉴权）：从
  PriceCache 全量快照 + 1s 环形缓冲计算，三轴各 0..100：
  - `breadth` 涨跌家数比：day_change_percent>0 的标的占比×100
    （恰为 0 计平盘，不入分子；无标的 → 50）；
  - `volatility` 平均波动：全场 mean((day_high−day_low)/prev_close)
    振幅，2% 振幅映射 100（线性夹取，prev_close≤0 跳过）；
  - `volume` 量能：近 10 分钟 1m bar 成交量合计 / 前 10 分钟合计，
    比值 1.0→50，≥2.0→100，≤0.5→0（线性；前段为 0 → 50 中性）。
    复用 indicators.aggregate_minute_bars。
  - `score` = round(0.5·breadth + 0.25·volatility + 0.25·volume)；
  - `label` 五档（阈值 0/20/40/60/80）：frozen|cool|neutral|active|
    hot（键名，i18n 前端渲染：冰点/低迷/中性/活跃/沸腾）。
  → `{"score":int,"label":str,"axes":{"breadth":n,"volatility":n,
  "volume":n},"sample_size":int}`。样本 <2 个标的 → 全 50 + neutral。
- **/market 页仪表盘** `market-sentiment`（放网格上方/侧栏，DOM 实现
  非 canvas）：横向 5 段色带 + 分数标记 + 大号 label（i18n）+ 三轴
  迷你条（breadth 用 up/down 方向色**合法**——它是涨跌家数语义；
  volatility/volume 用 accent/blue 中性色）；SWR 10s。
- **AI 引用**：`_assemble_portfolio_context`（chat.py）与 briefs 的
  事件上下文追加一行
  `Market sentiment: {score}/100 ({label}) — breadth {b}, volatility
  {v}, volume {vol}`（zh locale 中文行）；仅当 sample_size≥2 时追加。
  SYSTEM_PROMPT 常量零改动；mock 分支输出字节不变。

## 2. 相关性热力图（后端 + /market 页）

- **GET `/api/market/correlation?minutes=`**（新，market.py，无鉴权）：
  minutes 默认 30 夹 5..120。对每标的取近 minutes 分钟 1m 收盘
  （aggregate_minute_bars），bar 数 ≥10 的标的入选；1m 对数收益
  Pearson 相关，输出按 **sector 分组排序**（板块相关块教学价值）：
  → `{"tickers":[str],"sectors":{t:s},"matrix":[[float 2dp]],
  "minutes":int}`。样本不足 2 个标的 → tickers=[]。自相关恒 1.0；
  分母 0（恒价）→ 0.0。
- **/market 页** `market-correlation`：NxN CSS grid，格
  `market-corr-${A}-${B}`，底色 `color-mix(blue|purple, |r|)`（正 blue
  负 purple，对角 muted），hover title "A×B r=0.83"；行/列头为代码
  （可点 SymbolLink 不必须，头部空间紧张可省）；sector 分界线或分组
  标签；空态 i18n（开市初期 bar 不足）。SWR 30s。

## 3. 成交日历（/journal 页，纯前端）

- 数据复用页面已取的 trades（500 条）按本地日聚合 realized_pnl。
- **月历** `journal-calendar`：7 列网格 + 周头（profile.locale 星期
  缩写），格 `journal-cal-day-${YYYY-MM-DD}`：日号 + 当日已实现盈亏
  （formatMoney 紧凑），底色 up/down 方向色 color-mix 强度=|pnl|
  相对当月最大|pnl| 夹取（0 成交透明）；今日描边 accent。
  `journal-cal-prev`/`journal-cal-next` 月导航（默认当月，月标题
  profile.locale 格式化）；点击有成交的格 → 设置该日过滤（与既有
  journal-filter/day 分节联动：滚到/仅显示该日，实现自选，测试可断言
  过滤生效）。导出纯 helper（月网格生成/强度映射）供 jest 直测。
- 放 reviews 与按日成交之间或右栏；既有区块 testid 不动。

## 4. 竞技场选手公开主页（后端 + 新 /player 页 + 排行榜链接）

- **users_profile 增列** `public_profile INTEGER NOT NULL DEFAULT 1`
  （schema.sql + `_USERS_PROFILE_NEW_COLUMNS` 迁移模式）。
- **GET `/api/players/{user_id}`**（新 routes/players.py，无鉴权）：
  用户不存在 → 404；`public_profile=0` 且非本人（cookie 判定）→
  `{"user":{"id","name"},"public":false}`；否则 →
  `{"user":{"id","name","created_at"},"public":true,
  "total_value":f,"return_pct":f,"rank":int|null,
  "equity_curve":[{time,value}],（portfolio_snapshots 升序，>500 点
  均匀降采样保末点）,
  "positions_summary":[{"ticker","weight_pct" 1dp}]（按现价权重
  降序，**无数量/成本/现金**）}。total/return/rank 复用
  leaderboard.compute_standings 口径。
- **PATCH `/api/players/me`**（cookie 身份）`{public: bool}` →
  `{"public":bool}`；Bearer 调用 403（对齐 key 管理红线）。
- **/player 页**（`/player?u=<id>`，query 模式同 /symbol，空态
  `player-empty`）：头部（名字、since、rank/总值/回报，formatMoney/
  方向色）；权益曲线 `player-equity`（lightweight-charts BaselineSeries
  base=profile.seed_cash，directionColors——照抄 PnLChart 配方）；
  持仓权重 `player-weights`（横条列表：代码 SymbolLink + 权重%）；
  非公开 → `player-private` 空态文案；**本人访问自己的主页**时显示
  隐私开关 `player-privacy-toggle`（PATCH me，乐观更新+重验）。
- **排行榜跳转**：Leaderboard 名字单元格包
  `<Link href={{pathname:'/player', query:{u:user_id}}}>`（testid
  `player-link-${user_id}`），文本节点保留原名字。arena 页照常。

## 5. i18n 与双市场

- 新键：`market.sentiment*`（含五档 label）、`market.corr*`、
  `journal.cal*`、`player.*`，en/zh 双字典。
- CN：情绪三轴与热力图市场无关（breadth 迷你条走方向色变量自动
  翻转——红=多数上涨，语义正确）；日历权益/金额 formatMoney ¥；
  /player 曲线 base=seed_cash（cn ¥100k）自适应 profile。
- cn 验收（8801）：市场页中文情绪档位/热力图渲染、日历中文月份
  星期、选手页 ¥ 与中文文案。

## 6. 测试

- 既有全套一字不改全绿。
- 新 pytest（约 +35）：sentiment 三轴数学（构造 cache 状态：全涨/
  全跌/半平盘、振幅映射夹取、量能比值三档、样本<2 → 中性）、
  correlation（已知序列 r≈±1/0、bar<10 过滤、sector 排序、恒价
  分母 0、minutes 夹取）、players（公开/私密/本人/404、降采样
  保末点、权重和≈100、无数量成本字段泄漏、PATCH me cookie/Bearer
  矩阵）、迁移幂等、context 情绪行（追加与 sample<2 不追加、
  SYSTEM_PROMPT 常量字节不变断言、默认 mock 字节回归）。
- 新 jest（约 +30）：sentiment 档位映射与轴条渲染、correlation 格
  色彩（blue/purple 非方向色）与空态、日历纯 helper（月网格/闰月/
  周起始/强度映射）与导航/点击过滤、player 页四态（公开/私密/
  本人开关/空态）、Leaderboard 链接存在且名字文本保留。
- 新 E2E `p4.spec.ts`（US ~4 条）：market 页情绪仪表盘与热力图
  渲染（有分数、有格子）；下单后 journal 日历当日格出现盈亏色；
  排行榜点名字 → /player 曲线渲染；隐私开关关闭 → API 返回
  public:false（request context 断言）。`p4-cn.spec.ts`（~2 条：
  中文档位/月份、选手页 ¥）；cn compose 命令追加。
- 双市场回归 = 全套件 + US/CN compose 全绿。

## 7. workflow 分工（实现→对抗验证→修复）

- **B 后端 agent**（只碰 backend/）：§1-§2 端点 + §4 后端 + §1 AI
  引用 + pytest。
- **F 前端 agent**（并行，只碰 frontend/）：§1-§4 前端 + §5 i18n +
  jest。
- **E2E agent**（并行，只碰 test/）：§6 两 spec + cn compose 增量。
- **对抗验证**：套件门槛 + 三路（后端数学与隐私面、前端不变量
  ——重点 Leaderboard 基线与相关性配色红线、CN 一致性+E2E 强度
  合并一路）→ 修复循环 ≤3 轮。

---

## 附：实施偏离备案（已认可，2026-07-10）

1. **/market 与 /journal 既有 section 的 flex 权重调整**（flex-[3]→
   flex-1 等 + 包裹容器）：为容纳新增面板的布局适配；区块内部 DOM
   与全部既有 testid 逐字节未动，基线 jest/E2E 全绿。"纯追加"按
   字面被突破，按布局必要性认可。
2. **本人查看自己主页恒可见但 `public` 返回真实标志**（契约字面
   写"否则 public:true"）：修正为 flag 如实（开关重载后不误显示），
   本人仍拿完整概要。
3. **GET 本人判定 cookie-only 加严**：契约要求 cookie 判定，实现曾
   经 get_current_user_id（Bearer 优先）——已修正为 cookie-only
   变体并有中间件级集成测试（纯 Bearer 持本人 key 亦只见
   public:false）。
