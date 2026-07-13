# D3 契约 — 历史行情回放（Market Replay）

前置：D1/D2 已落地（11b6c58）。基线：**pytest 1658 / jest 585（72
suites）/ E2E us 41 + cn 18 一字不改全部通过**；金样本、零外网、
session 快照精确形状测试照旧。

愿景：把 daily_bars（sample/yfinance/akshare 任一来源）"重播"为全
平台实时行情——1 交易日压缩为 N 秒，昨收/涨跌停/日内高低随回放日
**真实滚动**；交易/规则/策略引擎/竞赛/排行/AI 全部零改动照常工作。
配合 D2 私房赛 = "历史时段回放赛"。

核心不变量（对抗验证重点核查）：
- **缺省行为零变化**：FINALLY_LIVE_SOURCE ≠ replay 时一切逐字节如旧
  （factory auto 分支、session 配置读取、endpoints）。
- **结算机器零改动**：settlement.py / cache.settle_close / roll_session
  一行不动。回放的正确性靠**路径构造保证**：每日路径最后一个写入
  tick == 该日真实收盘 → 既有 settle 盖章即真实收盘、roll 后
  prev_close 即真实昨收、CN 涨跌停带自动按真实昨收重算。
- **确定性**：同一窗口+同一数据同一回放（每 (ticker, date) 种子
  RNG）；测试可逐 tick 断言。
- **零外网**：回放启动注入只用 SampleProvider（同步、无导入依赖）；
  真实数据回放要求用户先 sync（启动校验给明确指引，不静默拉网）。
- session 快照**精确形状不动**；回放状态走独立端点（附加键仅
  在回放激活时出现的先例模式不采用——直接独立端点最干净）。
- 既有测试一字不改全绿。

---

## 1. ReplayDataSource（新 backend/app/market/replay_source.py）

- 实现 MarketDataSource，形态镜像 SimulatorDataSource（构造
  `(price_cache, *, db_path, market, session_clock, universe,
  update_interval=0.5, config: ReplayConfig)`；start/stop/add/remove/
  get_tickers）。
- **数据装载**（start 时一次）：从 daily_bars 读窗口内各票日线
  （直接 SQL 或新助手 load_daily_bars_window(conn, market, ticker,
  from, to)）；窗口前一交易日的收盘做**首帧种子 tick**（cache 首写
  即定 prev_close —— 与模拟器 seed-write 模式一致）。无覆盖的票
  **静默忽略 + 一次性日志**（不进 cache → SSE 自然缺席；
  sync_market_source 已兜异常）。crypto 无日线，同样缺席（文档注明
  回放模式只含有历史数据的权益标的）。
- **日内路径合成**（纯函数 `build_day_path(bar, n_points, rng) ->
  list[float]`，独立可测）：阳线（close≥open）走 O→L→H→C、阴线走
  O→H→L→C 的分段线性骨架 + 每 (ticker, date) 种子微噪声（幅度
  ≤0.1% price，逐点夹在 [low, high] 内）；**保证恰好触及 high 与
  low 各一次、末点精确 == close**。路径长度 = 开市窗口 tick 数的
  90%（尾部 10% 持收盘价零噪声——吸收 sleep 抖动，确保收盘前
  路径已完成）。
- **volume**：日总量均匀分布到路径点（种子抖动 ±30%），以每 tick
  增量写 cache.update（语义对齐）；尾部持价段 volume 0。bid/ask
  复用模拟器的 compute_quote 确定性点差。
- **会话对齐**（零新钩子，镜像模拟器的轮询模式）：循环每 interval
  检查 session_clock——closed → 不写（冻结）；session_id 变化 →
  切到下一回放日重建路径（到窗口尾按 config.loop：true 回到第一日
  ——**loop 重置日与首日之间也经历 settle/roll**，prev_close 用
  窗口首日前收，涨跌停带正确；false → 持最后收盘价冻结，新鲜度
  闸门自然拦截交易，回放状态端点标 finished）。CN 四相 midday 由
  is_open 轮询天然处理（路径按"已写 tick 数"推进而非墙钟，am+pm
  拼接无缝）。
- 异常：单票路径构建失败剔除该票并告警；循环异常 log + 存活。

## 2. 激活与启动数据（factory.py + main.py）

- LIVE_SOURCE_CHOICES 增 'replay'（**不入 REAL_DATA_SOURCES**——
  回放需要会话时钟）；resolve_live_source 透传。factory 签名增
  `db_path`（唯一调用点 main.py:234 在作用域内）。
- 新 env（全部启动时读一次）：`FINALLY_REPLAY_FROM` / `FINALLY_REPLAY_TO`
  （ISO 日期；缺省 = 公共覆盖的最近 20 个交易日）、
  `FINALLY_REPLAY_SECONDS_PER_DAY`（默认 120，夹 30..600）、
  `FINALLY_REPLAY_BREAK_SECONDS`（默认 5，夹 2..60）、
  `FINALLY_REPLAY_LOOP`（默认 true）。
- **会话时钟**：replay 模式下 _create_session_clock 用
  seconds_per_day/break_seconds 构造（覆盖常规 session env；CN
  midday 照 profile 保留，am+pm 各半）。
- **启动数据校验与注入**（main.py replay 分支，创建源之前）：
  检查宇宙各票在窗口的覆盖；不足 → **同步注入 sample**（
  sync_daily_bars source="sample"，零网络零可选导入）补缺；注入后
  仍不足（窗口超出样本范围等）→ 启动 ValueError，消息含覆盖现状
  与"先 sync 或改窗口"指引。公共交易日 <2 → 同样失败。
- 回放模式下 24/7 强制不适用（不在 REAL_DATA_SOURCES）；
  briefs/规则/策略/竞赛照常。

## 3. 回放状态端点 + 前端指示

- **GET /api/market/replay**（新，market.py 或独立小工厂，无鉴权）：
  非回放模式 → `{"active": false}`；回放 → `{"active": true,
  "from": str, "to": str, "current_date": str, "day_index": int,
  "total_days": int, "seconds_per_day": num, "loop": bool,
  "finished": bool, "source_hint": "sample"|"mixed"|...}`（读源的
  线程安全快照）。session 快照端点**不动**。
- 前端：
  - **StatusBar 回放徽章** `replay-badge`（SWR /api/market/replay
    10s；active 才渲染——StatusBar 既有 DOM/testid 不动，纯追加）：
    "回放 2020-03-16 · 3/20" 样式，amber 语义色。
  - **市场页横幅** `replay-banner`（纯追加）：窗口、当前日、进度条、
    loop/finished 状态；finished 提示"回放已结束（价格冻结）"。
  - i18n `replay.*` en/zh。
- 不做前端开关（env 决定，教学部署由教师控制）——文档钉死。

## 4. 文档（本期实现 lane 直接负责，目录授权已含）

- `.env.example`：FINALLY_REPLAY_* 四变量 + 与 FINALLY_LIVE_SOURCE=
  replay 的组合示例（"2020 熔断周回放赛" us 示例窗口按 sample
  覆盖写可用日期）。
- planning/OPERATIONS.md：回放模式运行手册（怎么起一场回放赛：
  设 env → 起容器 → 建限时私房赛发码）；planning/CURRENT.md 增
  数据源一节。
- README：教学场景一段。

## 5. 测试

- 既有全套一字不改全绿（含 session 精确形状、settlement、simulator
  源、factory 矩阵——replay 分支为 additive case）。
- 新 pytest（约 +55）：build_day_path（确定性同种子同路径、末点==
  close、恰触 high/low、噪声夹取、n_points 边界 2/3 点、零振幅
  bar）；volume 分布（总量守恒±取整、尾段 0）；ReplayDataSource
  单步（FakeTime/假 clock：首帧=前收、开市写 tick、closed 冻结、
  session_id 变化换日、末日 loop 回绕经 settle/roll 后 prev_close
  正确、no-loop 冻结 finished、无覆盖票忽略、单票失败剔除）；
  **结算集成**（真 SessionClock 短窗：两日回放走完 settle→roll，
  断言 prev_close==真实昨收、CN 涨跌停带==真实昨收±pct、
  day_change_percent 真实）；factory/env 矩阵（choices 增 replay、
  缺省行为字节回归、seconds 夹取、日期解析 400/ValueError、
  REAL_DATA_SOURCES 不含 replay）；启动注入（缺覆盖→sample 补齐、
  仍不足→ValueError 消息、公共日 <2 失败）；replay 状态端点两态
  形状；CN 路径（midday 四相下路径无缝、整手交易在回放价上照常）。
- 新 jest（约 +18）：replay-badge active/inactive 两态与文案、
  市场页横幅进度/finished 态、i18n 键集 en/zh、StatusBar 既有
  断言不破（inactive 缺省零渲染）。
- E2E：不新增 spec（env 门控特性）；验证阶段做**真实 app 冒烟**：
  以 replay env + sample 起 uvicorn，秒级 seconds_per_day，观测
  两次日界（curl session/replay/quotes 断言 prev_close 滚动与
  日期推进），us+cn 双 profile 各一遍。既有 US/CN compose 照跑
  （回放不启用，验证缺省零变化）。
- 双市场回归 = 全套件 + 双 compose + 双 profile 回放冒烟。

## 6. workflow 分工（实现→对抗验证→修复）

- **B 后端 agent**（backend/ + .env.example + planning/ 文档 +
  README 段落）：§1-§2 + §3 端点 + §4 + pytest。
- **F 前端 agent**（并行，只碰 frontend/）：§3 前端 + jest。
- **对抗验证**：套件门槛（缺省字节回归+金样本+session 形状）+
  三路（路径数学与会话对齐——独立重放两日断言结算正确性；
  前端与 CN；env/factory 矩阵与文档一致性）+ 双 profile 回放
  冒烟 → 修复循环 ≤3 轮。
