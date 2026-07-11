# D1 契约 — 真实历史数据层 + 历史回测模式（V3 §三）

前置：V3_DATA_PLAN.md 选型已定（yfinance / AKShare 东财 / sample）。
基线：**pytest 1368 / jest 489（62 suites）/ E2E us 35 + cn 14 一字
不改全部通过**；`next build` 干净；金样本与 CN 字节钉死照旧。

核心不变量（对抗验证重点核查）：
- **测试与 CI 零外网**：pytest/jest/E2E 任何路径不得真实访问
  Yahoo/东财——provider 测试全部走注入的 fake fetcher/固件 JSON；
  E2E 只用 sample 源。真实网络仅存在于用户显式触发的 sync。
- **不再分发真实行情数据**：仓库/镜像只含 sample 源（确定性生成的
  非真实序列，生成脚本入库）；yfinance/akshare 拉取的数据只落用户
  自己的 SQLite 卷。
- **模拟器主路径零接触**：实时行情/SSE/交易/规则/策略引擎不读
  daily_bars；新依赖导入失败或断网只影响 sync 与 history 回测
  （报错清晰），启动与既有功能不受影响（yfinance/akshare 用
  **延迟导入**，导入失败降级为"源不可用"）。
- **旧回测路径字节不变**：`source` 缺省 = 现行合成路径，金样本
  照过；`runs`>1 仅合成路径支持（history 确定性，无 MC）。
- 既有测试一字不改全绿；执行/费用/T+1/整手语义在 history 模式复用
  同一套 mechanics（不 fork 数学）。

---

## 1. 数据层（backend/app/market/history.py + db）

- **daily_bars 表**（schema.sql，新表无迁移）：`market TEXT NOT NULL,
  ticker TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL, high
  REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL
  NOT NULL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
  PRIMARY KEY (market, ticker, date)`；索引 `(market, ticker, date)`
  已由 PK 覆盖。复权口径：CN 存前复权（qfq）、US 存 adj close 折算
  的 OHLC（幅度按 adj/close 比例缩放）；source ∈
  sample|yfinance|akshare。
- **HistoryProvider 协议**：`fetch_daily(ticker, start, end) ->
  list[DailyBar]`；实现三个：
  - `SampleProvider`：读 `backend/app/market/sample_bars/`（生成脚本
    `scripts/gen_sample_bars.py` 确定性输出 ~3 年日线：us 10 票 +
    cn 14 票，趋势/回撤/震荡三形态混合，写明"合成样本非真实
    数据"）；永远可用。
  - `YFinanceProvider`（us）：yfinance 延迟导入，auto_adjust；
    异常/空结果 → 明确错误消息（不抛裸异常）。
  - `AkshareProvider`（cn）：stock_zh_a_hist(symbol, period="daily",
    adjust="qfq")，仅东财接口；重试 2 次退避。
- 依赖：pyproject 主依赖加 `yfinance`、`akshare`（uv lock 更新，
  Docker 构建验证）；导入放函数内。

## 2. 同步与查询 API（backend/app/routes/history.py，cookie 鉴权）

- `POST /api/market/history/sync` `{source?: "auto"|"sample"|
  "yfinance"|"akshare", tickers?: [str], years?: int 1..10 默认 3}`：
  auto = 按市场选真实源、失败回落 sample 并在响应标注。同步执行于
  asyncio.to_thread（≤30 票规模秒级~分钟级），逐票 upsert（INSERT OR
  REPLACE），返回 `{"results": [{ticker, source, bars, error?}],
  "total_bars": int}`。Bearer 调用 403（对齐 key 管理红线）；每次
  调用间隔 <10s → 429（防手滑连打真实源）。
- `GET /api/market/history/daily?ticker=&limit=` limit 默认 260 夹
  1..2600 → `{"ticker", "bars": [{date, open, high, low, close,
  volume}], "source", "coverage": {from, to, count}}` 升序。
- `GET /api/market/history/coverage` → 每票 {ticker, from, to, count,
  source}（前端展示数据就绪状态）。

## 3. 历史回测模式（backtest.py 增量）

- 请求增可选 `source: "synthetic"(默认) | "history"`；history 时
  days 语义 = 交易日数（夹 20..750），从 daily_bars 取该票最近 N 根
  日线；bars 不足 20 → 400 "Insufficient history — run a data sync
  first"（含 coverage 提示）。
- 评估口径（**新路径，不碰合成路径的 RNG/bar 生成**）：
  - 指标/条件在**日线序列**上求值（复用 indicators.py 同一套函数，
    分钟参数字段直接按"根"解释）；day_change_pct 用相邻收盘。
  - 入场：条件在 T 日收盘成立 → **T+1 日开盘价成交**（消除前视
    偏差；教学要点写进响应 config.echo）。
  - 出场：持仓期间每日先 SL 后 trailing 后 TP（对照当日 low/high
    触碰，价取触发价），max_holding_days 按交易日；CN T+1 = 入场
    次日起可出（天然满足）。
  - 费用/整手/点差沿用现有 `_fee`/lot 逻辑；starting_cash 按
    profile。equity 每日收盘 mark；baseline = 首根开盘买入持有。
  - 完全确定性：`runs` 必须为 1（>1 → 400）；seed 忽略且回显 null。
- 响应形状不变（config/stats/equity_curve/baseline_curve/trades/
  runs_summary=null），config 增 `source`、`date_range: {from, to}`。
- Run Library / 策略回测（POST /api/backtest/runs、chat backtests
  动作）透传 `source`；strategy 详情跑历史回测同参。

## 4. 指标扩充（indicators.py + FIELD_SPECS）

- 新增纯函数：`macd(closes, fast=12, slow=26, signal=9) ->
  (macd_line, signal_line, hist)`、`bollinger(closes, period=20,
  k=2.0) -> (mid, upper, lower)`（母体标准差）。
- FIELD_SPECS 新字段（值/参数严格校验，op above/below）：
  | field | params | 语义（above） |
  |---|---|---|
  | macd_cross | fast<slow, signal（默认 12/26/9） | MACD 线本根上穿信号线（前根≤，当根>；below=下穿） |
  | boll_break | period 5..120 默认 20, k 0.5..4 默认 2 | 现价 ≥ 上轨（below：≤ 下轨） |
- 开发期用 pandas-ta-classic 对同序列的输出做金向量校验（仅测试
  fixture 内嵌预算值，**不引入运行时依赖**）。暖机不足 → False。
- 六模板不改；模板库后续 D2 再扩。

## 5. 前端

- **Backtest 页签**：数据源分段开关 `backtest-source`（模拟 |
  历史，i18n），历史态下 runs 选择禁用、days 标签变"交易日"、
  提交带 source；结果统计区显示来源徽章 `backtest-source-badge`
  与 date_range。表单其余不动（既有 jest 不改）。
- **策略详情**：跑回测弹层加同款 source 开关（`strategy-bt-source`）。
- **/market 页数据状态卡** `history-coverage`：coverage 端点渲染
  每票数据区间 + `history-sync-button`（source=auto，进行中禁用+
  spinner，结果 toast 成功/失败计数；Guest 可用）。
- **Run 详情/Run Library**：行与详情显示 source 徽章（sample/
  yfinance/akshare/synthetic，i18n）。
- i18n `history.*` + backtest 增键 en/zh；金额 formatMoney。

## 6. 测试

- 既有全套一字不改全绿（含金样本——source 缺省合成路径字节不变）。
- 新 pytest（约 +55）：sample 生成器确定性（同 seed 同输出）与
  形状；provider 单元（fake fetcher 注入：正常/空/异常/重试）；
  sync upsert 幂等 + auto 回落 + 429 节流 + Bearer 403；daily/
  coverage 端点形状与夹取；history 回测（固件 bars：T+1 开盘成交、
  SL 先于 TP 触碰判定、trailing 高水位、max_holding、CN 整手/费用、
  bars 不足 400、runs>1 400、确定性重跑一致）；MACD/BOLL 金向量
  （pandas-ta 预算值内嵌）+ cross 边界 + FIELD_SPECS 校验矩阵；
  合成路径金样本回归照跑。
- 新 jest（约 +25）：source 开关切换与提交载荷、历史态 runs 禁用、
  coverage 卡与 sync 按钮态、source 徽章、i18n 键对齐。
- 新 E2E `history.spec.ts`（US ~3 条，全走 sample 源）：sync(sample)
  → coverage 卡出现区间；Backtest 页签切历史源跑通并显示徽章；
  strategy 详情历史回测入 Run Library。`history-cn.spec.ts`
  （~1-2 条：中文文案 + cn sample 同步）；cn compose 追加。
- 双市场回归 = 全套件 + US/CN compose 全绿 + Docker 构建含新依赖
  成功。

## 7. workflow 分工（实现→对抗验证→修复）

- **B 后端 agent**（只碰 backend/ + scripts/gen_sample_bars.py）：
  §1-§4 + pytest。
- **F 前端 agent**（并行，只碰 frontend/）：§5 + jest。
- **E2E agent**（并行，只碰 test/）：§6 两 spec + cn compose 增量。
- **对抗验证**：套件门槛（含金样本+零外网核查：测试代码 grep 真实
  域名/未打桩网络调用）+ 三路（后端契约与回测口径、前端、CN+E2E）
  → 修复循环 ≤3 轮。
