# P2 契约 — 策略中心（V2 §二）

前置：P1 已落地（44c788c）。基线：**pytest 857 / jest 275（39 suites）/
E2E us 18 + cn 6 —— 一字不改全部通过**；`next build` 干净。
范围：策略实体+引擎+指标触发+回测持久化(Run Library)+AI 动作+模板 +
/strategies、/strategy?id=X、/runs、/run?id=X 四个页面。P3 量化接口、
P4 锦上添花不做。

核心不变量（对抗验证重点核查）：
- **金样本**：旧 `POST /api/backtest` 对相同 (config, seed, end_time)
  的完整响应**逐字节不变**。实现顺序强制：先在未改动代码上采集金样本
  （us 两组 seed + cn 一组，含 tp/sl/runs>1 变体，pin end_time），写入
  `tests/test_backtest_golden.py`，**再**重构引擎；金样本测试永久留档。
  响应里 config 回显对旧请求保持旧形状（不得混入 entry/exits 新键）。
- **冻结签名**：`execute_trade_on_conn` 公共包装（8 参）不动；
  `_execute_trade_on_conn` 仅追加 keyword-only `strategy_id: str|None
  = None`（写入 trades.strategy_id）。rules/chat/orders 的既有绑定与
  行为零变化。
- **chat 兼容**：默认 LLM_MOCK 输出字节不变；`strategies` 出参键仅在
  非空时出现（沿用既有规则）；结构化 schema 字段名全英文。
- **安全底线**：策略条件是声明式 JSON + 白名单注册表校验；**任何路径
  都不执行 AI 生成的代码**；未知 field/op/params → 400。
- **指标单源**：一个指标模块（纯函数，输入分钟 bar 序列），实盘（1s
  环形缓冲聚合 1m）与回测（合成 1m bar）调用同一套函数——口径天然
  一致。bar 数不足暖机 → 条件求值 False（绝不抛异常）。
- BacktestPanel 抽组件是**纯重构**：DOM/testid 不变，其现有 jest 逐字
  不改通过。

---

## 1. 数据模型（schema.sql + connection.py 迁移，全部幂等）

1. **strategies 表**（新）：`id TEXT PK, user_id TEXT NOT NULL DEFAULT
   'default', name TEXT NOT NULL, ticker TEXT NOT NULL, status TEXT NOT
   NULL DEFAULT 'draft'`（draft|live|paused|archived）`, entry TEXT NOT
   NULL, exits TEXT NOT NULL, sizing TEXT NOT NULL, template TEXT,
   created_at TEXT NOT NULL, deployed_at TEXT, open_qty REAL NOT NULL
   DEFAULT 0, open_price REAL, opened_at TEXT, high_water REAL,
   cooldown_until REAL, entered_count INTEGER NOT NULL DEFAULT 0,
   exited_count INTEGER NOT NULL DEFAULT 0, last_fired_at TEXT`；索引
   `(user_id, status)`、`(status)`。
2. **backtest_runs 表**（新）：`id TEXT PK, user_id TEXT NOT NULL,
   strategy_id TEXT, label TEXT, created_at TEXT NOT NULL, config TEXT
   NOT NULL, stats TEXT NOT NULL, equity_curve TEXT NOT NULL,
   baseline_curve TEXT NOT NULL, trades TEXT NOT NULL, runs_summary
   TEXT`；索引 `(user_id, created_at DESC)`、`(strategy_id)`。曲线沿用
   引擎 ≤400 点降采样；trades JSON 截前 200 条。
3. **trades.strategy_id TEXT**：schema.sql 增列 + `_TRADES_NEW_COLUMNS`
   追加（现成 `_add_missing_columns` 模式）。
4. **chat kind 'strategy'**：GET /api/chat 的 kind 合法集、LLM 历史排除
   名单（brief/rule/review/strategy）两处 + 前端 KIND_BORDER/
   `chat.kind.strategy`。

## 2. 条件与指标模块（新 `app/indicators.py`，纯函数无 IO）

- `aggregate_minute_bars(bars_1s) -> [{time,open,high,low,close,volume}]`
  按 60s 桶聚合（同 time 取整分钟），**只返回已完结分钟**（丢弃当前
  未完结分钟，避免抖动）；回测的合成 1m bar 直接透传。
- 指标：`sma(closes, n)`、`ema(closes, n)`（标准 2/(n+1) 递推，首值
  SMA 种子）、`rsi(closes, n=14)`（Wilder 平滑）、`window_high/low
  (bars, minutes)`。全部返回 float 或 None（数据不足）。
- **条件注册表** `FIELD_SPECS`：field → (params schema, evaluator)。
  条件组：`{"all":[COND..]} | {"any":[COND..]}`（恰一键，1..5 条）；
  `COND = {"field", "op": "above"|"below", "value"?: number,
  "params"?: object}`。字段清单（值/参数严格校验，多余键 400）：
  | field | params | value | 语义（op=above 时） |
  |---|---|---|---|
  | price | — | 必填>0 | 现价 ≥ value（below ≤，含边界，对齐 rules） |
  | day_change_pct | — | 必填 | 日涨跌%（4dp，对齐 PriceUpdate）≥ value |
  | ma | period 2..120 | 必填省略→0 | 现价相对 SMA(period)±value% |
  | ma_cross | fast<slow，各2..120 | 无 | 本分钟金叉（above）/死叉（below）：前一 bar fast≤slow 且当前 fast>slow |
  | ema_cross | 同上 | 无 | 同 ma_cross 用 EMA |
  | rsi | period 2..50 默认14 | 必填 0..100 | RSI ≥ value |
  | window_high | minutes 5..240 | 无 | 现价 ≥ 滚动 minutes 分钟最高（突破） |
  | window_low | minutes 5..240 | 无 | below 语义：现价 ≤ 滚动最低（破位）；above 视为非法 400 |
  | pullback_from_high_pct | minutes 5..240 | 必填>0 | 自滚动高点回撤% ≥ value |
- `validate_condition_group(entry) -> None | 错误消息`；
  `evaluate_condition_group(entry, bars_1m, quote_like) -> bool`——
  quote_like 提供 price/day_change_percent（实盘用 PriceUpdate，回测
  用 bar close + 合成 day_change）。cross 类字段用完结 bar 序列，
  price/day_change 用实时值。
- **exits**：`{"take_profit_pct"?, "stop_loss_pct"?, "trailing_stop_pct"?,
  "max_holding_days"?}`——全部可空；**deploy（转 live）时至少一项非空**。
  数值 >0，max_holding_days int 1..120。
- **sizing**：`{"mode":"fixed_qty","qty">0}` 或
  `{"mode":"cash_pct","pct" 1..100}`。

## 3. 策略引擎（新 `app/strategy_engine.py` + 循环）

- `process_strategies_once(db_path, price_cache, commission_bps=0.0,
  profile=None) -> {"entered","exited","skipped","trade_failed"}`：
  单查 `WHERE status='live'`（全用户，对齐 rules 模式），每策略异常
  隔离（rollback+log+continue）。同一 pass 内按 ticker 记忆化
  get_history→1m 聚合。`strategies_eval_loop` 1s 注册进 lifespan
  （传 `trading_profile`），列入 background_tasks。
- **持有态（open_qty>0）出场判定**，优先级严格：stop_loss →
  trailing_stop（自 high_water 回撤%，high_water 每 pass 以现价抬升
  并写回）→ take_profit → max_holding_days（UTC 日历日差，与回测的
  合成"日"有语义差异——契约备案，不视为缺陷）。触发 → 市价卖
  open_qty（`_execute_trade_on_conn` 带 strategy_id）。**T+1**：
  opened_at 同一交易日内跳过全部出场（对齐回测）。卖出失败因份额
  不足（用户手动卖过）→ 清空 open 状态 + 记 chat 备注（防死循环）；
  市场闭市失败 → skip 下一 pass 重试。
- **空仓态入场**：`evaluate_condition_group(entry,...)` 为真 →
  sizing 定量：fixed_qty 原样；cash_pct = floor(cash×pct% / ask)，
  CN 向下取整手，不足一手/一股 → skip + 置 cooldown。买入成功 →
  写 open_qty/open_price/opened_at/high_water=entry price、
  entered_count+1、last_fired_at。
- **cooldown_until**（REAL epoch）：交易失败（资金不足等）或不足一手
  → now+60s，期间跳过求值；成功清空。防每秒重试刷屏。
- 每次入/出场：同一提交内 `_record_snapshot` + 插入 kind='strategy'
  的 assistant chat 行，`actions={"trades":[outcome],
  "strategy_id": id}`（对齐 rules 的 fire 消息模式）。
- pause = 完全冻结（不入不出，UI 文案明示"暂停后停止管理持仓"）；
  archive 清空 open 状态（份额留在组合，由用户手动处理）。

## 4. 回测引擎扩展（backtest.py，金样本护航）

- 内部统一为"条件组+exits"评估：旧 trigger_type/threshold 在入口
  适配为等价单条件组；**bar 生成、RNG 抽取顺序、费用/半点差、
  日重武装、SL 先于 TP、T+1 推迟、降采样逻辑逐行不动**。
- 新增能力（仅新配置路径触达）：条件组含指标字段（在合成 1m bar 上
  评估，暖机不足 → False）；trailing_stop_pct（盘中先于 TP、后于 SL
  判定：优先级 SL → trailing → TP，与实盘一致）；max_holding_days
  （按合成日）；sizing cash_pct（入场时按现金计算、CN 取整手）。
- `normalize_strategy_backtest_config(price_cache, *, strategy_row |
  (ticker, entry, exits, sizing), days, runs, seed, universe, profile)`
  → 扩展 config `{ticker, entry, exits, sizing, days, runs, seed,
  anchor_price, source:"strategy"}`。响应形状与旧版一致
  （config/stats/equity_curve/baseline_curve/trades/runs_summary），
  stats 键集不变。

## 5. Run Library API（新 `app/routes/backtest_runs.py`，鉴权）

- `POST /api/backtest/runs`：body 二选一——`{strategy_id, days?,
  runs?, seed?, label?}`（从策略配置构造）或旧字段全量
  `{ticker, trigger_type, threshold, quantity, ..., label?}`（保存
  Backtest 页签结果：**服务端以同 config+seed 重跑落库**，杜绝伪造
  统计；end_time=now，不要求与页签渲染一致，seed 一致即口径可信）。
  → 201 `{"run": {id, strategy_id, label, created_at, config, stats,
  equity_curve, baseline_curve, trades, runs_summary}}`；策略不存在/
  非本人 404；校验失败 400。
- `GET /api/backtest/runs?strategy_id=&ticker=&limit=`（limit 默认 50
  夹 1..200，created_at DESC）→ `{"runs":[{id, strategy_id, label,
  created_at, ticker, days, runs, seed, stats}]}`（**无曲线**）。
- `GET /api/backtest/runs/{id}` → 全量；404 非本人。
- `DELETE /api/backtest/runs/{id}` → `{"status":"ok"}`；404。

## 6. 策略 CRUD + 绩效 + 模板（新 `app/routes/strategies.py`，鉴权）

- `POST /api/strategies` `{name 1..40, ticker, entry, exits, sizing,
  template?}` → draft。ticker 必须在 cache/universe；全量校验。
- `GET /api/strategies?status=`（draft|live|paused|archived|all 默认
  all 不含 archived？——**定死：默认不含 archived**，status=all 含）
  → `{"strategies":[{...config, status, counters, open_qty, open_price,
  opened_at, runs_count, realized_pnl}]}`（realized_pnl = 该
  strategy_id 卖出 realized_pnl 求和）。
- `GET /api/strategies/{id}` → 单条同形状。
- `PATCH /api/strategies/{id}`：`{status}` 状态机 draft→live（写
  deployed_at；**校验至少一项 exit**）、live↔paused、任意→archived
  （清 open 状态）；archived 终态（→400）。或配置编辑 `{name?,
  entry?, exits?, sizing?}`——**live 时编辑 400 "pause first"**。
- `DELETE /api/strategies/{id}`：live → 400；其余删除（trades 保留
  strategy_id 归因）。
- `GET /api/strategies/{id}/performance` → `{"stats":{realized_pnl,
  round_trips, win_rate, profit_factor, max_drawdown_pct, fires},
  "equity_curve":[{time,value}], "trades":[...该策略成交]}`。曲线 =
  累计已实现盈亏序列（按卖出时点）+ 持仓时末点加浮盈（**0 基线
  P&L 曲线**，前端 BaselineSeries base 0）；统计口径复用 M5/analytics
  数学（win_rate 4dp、_max_drawdown_pct 同源）。
- `GET /api/strategies/templates`（无鉴权，静态注册表）→
  `{"templates":[{key, ticker_hint:null, entry, exits, sizing}]}`。
  **六模板定死**（名称/描述由前端 i18n 按 key 渲染）：
  1. `dip_buyer` 抄底：entry {all:[{day_change_pct below -3}]}；
     exits {tp 4, sl 3}；sizing cash_pct 20
  2. `momentum_breakout` 动量突破：{all:[{window_high above,
     params{minutes:60}}]}；exits {trailing 2.5, sl 3}；cash_pct 20
  3. `ma_golden_cross` 均线金叉：{all:[{ma_cross above,
     params{fast:5,slow:20}}]}；exits {tp 5, sl 3}；cash_pct 25
  4. `grid_lite` 网格（简化版，如实标注）：{all:[
     {pullback_from_high_pct above 2, params{minutes:60}}]}；
     exits {tp 2, sl 6}；cash_pct 15
  5. `rsi_rebound` RSI 超卖反弹：{all:[{rsi below 30,
     params{period:14}}]}；exits {tp 4, sl 3}；cash_pct 20
  6. `trend_rider` 趋势跟随：{all:[{ma above, value 0,
     params{period:30}},{day_change_pct above 0.5}]}；
     exits {trailing 3}；cash_pct 25

## 7. chat `strategies` 动作（chat.py）

- Pydantic schema 增 `strategies: [{action: "create"|"backtest"|
  "deploy"|"pause", name?, ticker?, template?, entry?, exits?,
  sizing?, strategy?: str(按 id 或不区分大小写名称解析), days?,
  runs?}]`。执行位于 Step 6d 后（Step 6e）：create（template 优先，
  显式 entry 覆盖）→ draft；backtest → 解析策略 → 引擎 + **落库
  Run Library（带 strategy_id）**，outcome 含紧凑 stats + run_id；
  deploy/pause → 状态机（deploy 缺 exit → failed outcome）。逐项
  failed 不中断批次（对齐现有动作语义）。
- 系统提示词 en/zh 增 strategies 数组说明 + 模板 key 清单 + "先回测
  再部署"引导 + 声明式字段清单；schema 键保持英文。
- LLM_MOCK：消息含 "strategy"/"策略" → 确定性分支：create
  ma_golden_cross（NVDA / cn 首只宇宙票）+ backtest(days 20, seed
  4242) + message；en/zh 变体动作数组字节一致。**默认与 backtest
  关键词分支输出字节不变**。

## 8. 前端

- **组件抽取（纯重构）**：EquityChart/StatCard + 内联的统计卡网格、
  MC 摘要条、成交清单抽为 `src/components/backtest/{EquityChart,
  StatCard,StatsGrid,RunsSummaryStrip,TradesBlotter}.tsx` 并导出；
  BacktestPanel 改为组装引用，**DOM 结构与全部 testid 不变**（其
  jest 逐字不改通过）。方向色/G28R28 常量随抽取收敛为单处。
- **导航**：NAV_ITEMS 增 策略 `/strategies`（nav-strategies）、回测库
  `/runs`（nav-runs），i18n `nav.*`。
- **/strategies**：模板卡 `template-card-${key}`（i18n 名称/描述，
  点击 → 表单预填）；创建表单 `strategy-form`（name/ticker(datalist)/
  模板下拉/**条件行构建器**：all|any 切换 + 行[field 下拉/op/value/
  params 动态输入] 增删 ≤5 行/exits 四输入/sizing 模式切换；提交
  POST）；列表 `strategy-row-${id}`：名称、SymbolLink、状态 chip
  `strategy-status-${id}`、realized_pnl（方向色）、runs_count、
  deploy/pause `strategy-toggle-${id}`、详情链接（→ /strategy?id=）。
- **/strategy?id=X**（query 同 /symbol 的 hydration 空态模式，
  `strategy-empty`）：头部（名称/SymbolLink/状态 + 控制：
  `strategy-deploy`（**软门槛**：runs_count===0 → 二次点击确认 +
  警示文案；后端不拦）、`strategy-pause`、`strategy-archive` 二次
  确认）；配置摘要（conditionText 式 i18n 人话渲染 entry/exits/
  sizing，`strategy-config`）；绩效区 `strategy-performance`（StatsGrid
  复用 + 0 基线权益图）；回测区：`strategy-run-backtest` 按钮
  （days/runs 输入 → POST /api/backtest/runs {strategy_id}）+ 本策略
  runs 列表 `run-row-${id}`（→ /run?id=X）+ **对比**：勾选两条 →
  `runs-compare` 并排统计列。
- **/runs**：全库表格（SWR /api/backtest/runs），filter ticker 输入 +
  strategy 下拉；行 `run-row-${id}`（时间/SymbolLink/策略名链接/
  label/收益%/胜率/MaxDD，方向色）→ /run?id=X；删除
  `run-delete-${id}` 二次确认。
- **/run?id=X**：`run-detail` 全量渲染 = StatsGrid + EquityChart +
  RunsSummaryStrip（有则）+ TradesBlotter 组装；返回策略/回测库
  链接。
- **Backtest 页签**：结果存在时显示 `backtest-save` 按钮（label 可选
  输入）→ POST /api/backtest/runs（旧字段全量含 seed）→ 成功 toast +
  链接到 /runs。既有表单/渲染不动。
- **ChatPanel**：StrategyBadge（`strategy-badge-created|deployed|
  paused|failed`，backtest 动作徽章带紧凑 stats + run 链接文案）、
  KIND_BORDER 增 strategy 色（紫 #753991 系）、onNewTrade 触发条件
  增 strategies 键、TRADE_REVALIDATE_KEYS 增 '/api/strategies'。
- 新页面全部 AppShell + useT + 导出纯 helper（conditionText、
  compare 数据整形）供 jest 直测（沿用 P1 页面模式）。

## 9. i18n 与双市场

- 新命名空间 `strategy.*`、`runs.*`（含 `strategy.template.{key}.name/
  .desc` 六模板、`strategy.cond.*` 字段人话、状态 chip、软门槛警示、
  暂停语义提示），en/zh 双字典同步。
- CN：sizing cash_pct 整手向下取整（mechanics 复用）；引擎经
  `_execute_trade_on_conn(profile)` 继承费用/T+1/涨跌停；zh mock
  分支；金额 formatMoney、数量 formatShares；模板对 CN 宇宙可用。
- cn 验收（8801）：中文导航/模板卡/条件人话、¥ 与手、策略引擎在
  T+1 下当日不出场（pytest 覆盖）、zh chat 建策略 mock 徽章。

## 10. 测试

- 既有全套一字不改全绿（含 BacktestPanel jest 在纯重构后）。
- **金样本 pytest**：HEAD 采样（见不变量）→ 重构后逐字节比对。
- 新 pytest（约 +75）：指标已知向量（SMA/EMA/RSI/窗口/金叉死叉/
  暖机 False）；条件校验 400 矩阵（未知 field/op/多余键/越界
  params/嵌套超限）；引擎单步（入场→trade+chat(kind=strategy)+
  snapshot 同提交、SL→trailing→TP 优先级、trailing 高水位抬升、
  max_holding、T+1 当日不出、cash_pct 整手、不足一手 skip+cooldown、
  卖出份额不足→清态、闭市 skip、pause/archive 不评估、异常隔离、
  cooldown 过期恢复）；CRUD（状态机全转移 + 非法 400、live 编辑
  400、deploy 无 exit 400、DELETE live 400、跨用户 404）；
  performance 口径（realized_pnl/win_rate/曲线末点浮盈）；runs
  （POST 两形状、同 seed 重跑等于无状态端点 stats、list 过滤/
  limit 夹取、detail/delete 404、strategy 级联查询）；chat
  strategies 四动作 + 解析 by name/id + mock 新分支 + **默认与
  backtest 分支字节回归**；迁移幂等（strategy_id 列）。
- 新 jest（约 +45）：抽取组件独立渲染；四页面（表单构建器增删行/
  校验、模板预填、软门槛二次确认、对比整形、runs 过滤、run 详情
  组装、hydration 空态）；StrategyBadge 各态；conditionText；nav
  增项；i18n 键集 en/zh 对齐（脚本断言）。
- 新 E2E `strategies.spec.ts`（US，约 6 条）：模板实例化→列表出现
  draft；详情页跑回测→ /runs 出现记录→ run 详情渲染图+统计；
  软门槛部署（二次确认）→ 状态 live；Backtest 页签保存→回测库
  可见；chat mock "strategy" → StrategyBadge + draft 入列；导航
  往返连接点保持 connected。
- 新 E2E `strategies-cn.spec.ts`（文件名命中 CN testMatch、被 US
  testIgnore 排除）：中文模板卡/创建/¥ 显示（约 2-3 条）；cn
  compose 命令追加该文件（基建改动）。
- 双市场回归 = 金样本 + jest + pytest + build + US/CN compose 全绿。

## 11. workflow 分工（实现→对抗验证→修复）

- **B1 后端引擎 agent**（只碰 backend/）：先采金样本 → §1 schema/
  迁移 + §2 指标条件模块 + §4 引擎扩展 + 对应 pytest。
- **B2 后端服务 agent**（B1 后串行，只碰 backend/）：§3 引擎循环 +
  §5 runs API + §6 CRUD/绩效/模板 + §7 chat 动作 + 对应 pytest。
- **F1 前端基建 agent**（与 B1 并行，只碰 frontend/）：§8 组件抽取
  （纯重构验证）+ 导航 + StrategyBadge/KIND_BORDER + i18n 骨架 +
  对应 jest。
- **F2 前端页面 agent**（F1 后串行）：四页面 + Backtest 保存按钮 +
  余下 i18n + 对应 jest。
- **W-E2E agent**（可并行，契约 testid 为准，只碰 test/）：两个新
  spec + cn compose 命令。
- **对抗验证**：套件门槛（含金样本）+ 五路核查（后端契约、引擎
  语义/边界、前端不变量与纯重构、CN 一致性、E2E 强度）→ 修复
  循环 ≤3 轮。

---

## 附：实施偏离备案（已认可，2026-07-08）

1. **strategy_id 归因经 `_execute_trade_impl` sibling** 而非在
   `_execute_trade_on_conn` 上加参：基线 test_cn2_parity 以
   inspect.signature 精确钉死后者 9 参签名（keyword-only 亦计入），
   契约字面写法必破硬门槛。沿用 CN-2 sibling 模式，三层包装行为
   零变化，归因写入有专测。
2. **`/api/strategies` 重验经独立 `STRATEGIES_REVALIDATE_KEY` +
   swr 模块级 mutate**，而非扩 TRADE_REVALIDATE_KEYS：基线
   AppShell.test.tsx 以 toHaveBeenCalledTimes(5) 冻结键集；app 无
   自定义 SWRConfig，模块级 mutate 命中同一默认缓存，生产等价。
3. **paused→live（Resume）不设软门槛**：软门槛语义绑定"首次部署"，
   paused 策略已过门；resume 单击直达 live。
4. **抽取件 StatsGrid/TradesBlotter 的 `$`/en-US 默认值保留**（纯
   重构不变量强制）；经可选 currencySymbol/locale props 在新页面
   （/run）传入 profile 实现 CN ¥ 本地化；BacktestPanel 默认路径
   逐字节不变。彻底收敛（连同 Backtest 页签）留待 P2 后续。
