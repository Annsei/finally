# CN-2 — A 股交易机制契约（T+1 / 整手 / 涨跌停 / 费用 / 午休）

前置：CN-1 已落地（profiles.py / universe.py / seed_prices_cn.py /
routes/profile.py，commit eb005ae）。硬门槛不变：现有 699 pytest 在默认
us 下一字不改全部通过；新机制逻辑放新文件或 profile 字段驱动的通用
检查（us 的字段值使检查自然为 no-op）。

## 0. 设计原则：单一挂钩参数 + 字段驱动

`execute_trade_on_conn` / `place_order_on_conn` / `create_rule_on_conn`
各增一个可选参数 `profile: MarketProfile | None = None`：
- `None` → 完全走现有路径（逐字节不变）。
- 提供时，检查全部由 profile **字段值**驱动（lot_size=1 → 整手检查
  天然通过；t_plus=0 → 不锁仓；min_commission=0 且 stamp=0 → 费用
  退化为现有 bps 数学）——US_PROFILE 传入 ≡ None，写测试证明这一点。
- 错误消息按 `profile.locale` 选择：zh-CN → 中文，其余 → 现有英文。

main.py 把 profile 传入所有相关工厂与后台循环（chat 路由、orders
fill loop、rules eval loop 同样透传——AI 与后台成交同受 A 股规则约束）。

## 1. 费用模型（字段驱动，扩展现有 commission 管道）

```
commission = max(profile.min_commission, notional × commission_bps / 10_000)
stamp      = notional × profile.stamp_tax_bps_sell / 10_000   # 仅卖出
fee        = commission + stamp
```
- `commission_bps` 解析（main.py，读一次）：FINALLY_COMMISSION_BPS 显式
  设置时优先，否则用 `profile.default_commission_bps`（cn=2.5）。
- fee 总额写入现有 `trades.commission` 列；买入折入成本、卖出净出
  realized_pnl —— 现有语义不变。None/us（min=0,stamp=0）→ 与现状
  逐分不差。

## 2. T+1 锁仓

- 增量迁移（现有 _migrate_schema 模式）：`positions` 增列
  `t1_locked REAL NOT NULL DEFAULT 0`。
- 买入且 `profile.t_plus > 0`：该持仓行 `t1_locked += qty`。
- 卖出校验：`sellable = quantity − t1_locked`；`qty > sellable` →
  失败 outcome/400，zh:「T+1：今日买入股份下一交易日方可卖出（当前
  可卖 {sellable:g} 股）」。
- 解锁：`settlement.roll_session_open(price_cache, db_path: str | None
  = None)` 增量参数——提供时执行
  `UPDATE positions SET t1_locked = 0 WHERE t1_locked > 0`（自持连接与
  提交，隔离失败不阻塞现有 prev_close 滚动）。main.py 的 on_open
  lambda 传入 db_path。
- **24/7 模式（无时钟或 always_open）下 T+1 整体禁用**（没有"交易日"
  概念，锁了永不解）——execute 侧以 session_clock 判断；契约明示。

## 3. 整手交易（lot_size）

- 买入（trade/order/rule 创建/回测 config）：`quantity % lot_size != 0`
  → 拒绝，zh:「A股买入须为 {lot_size} 股的整数倍」。
- 卖出：任意正数量（零股一次性卖出合法）。
- lot_size=1（us/None）→ 检查恒过。

## 4. 涨跌停（价格钳制在 PriceCache 漏斗，一处管全部）

- `PriceCache(limit_pct_fn: Callable[[str], float | None] | None = None)`
  增量构造参数。`update()` 内：若 fn 给出 pct 且该票已有 prev_close：
  `limit_up = round(prev_close×(1+pct/100), 2)`，`limit_down` 对称；
  **写入前把 price 钳入 [limit_down, limit_up]**——模拟器 tick、随机
  事件、板块 burst 全部经此漏斗，一处钳制。
- `PriceUpdate` 增字段 `limit_up/limit_down: float | None = None`；
  `to_dict()` **仅在非 None 时**输出这两个键（us 的 SSE payload
  逐字节不变——有现有测试断言 dict 形状，务必核对）。
- 模拟器内部状态防漂移：`GBMSimulator.set_price(ticker, price)` 新
  setter；`SimulatorDataSource._write_tick` 发现 cache 返回的
  PriceUpdate.price ≠ 提交价（被钳）时回写内部价 → 封板后随机游走
  自然开板。
- 委托校验（place_order_on_conn，profile 提供且该票有板价时）：
  limit_price 或 stop_price 落在当日 [limit_down, limit_up] 之外 →
  400 zh:「委托价超出当日涨跌停区间」。市价单不拦（按钳后价成交）。
- prev_close 随现有开盘滚动 → 板价自动按新基准重算，无需额外代码。
- 不新增"涨停事件"：前端用 quote 的 price>=limit_up 直接渲染徽章
  （CN-3）。

## 5. 午间休市（SessionClock 增量）

- `SessionClock(open_seconds, break_seconds,
  midday_break_seconds: float = 0.0)`。>0 时一个交易日 =
  上半场 open/2 → 午休 midday_break_seconds → 下半场 open/2 → 收盘
  break_seconds；=0 → 现有两态循环逐秒不变。
- 新属性 `phase -> str`：`"open"`（24/7 或未启用午休的开市）| `"am"` |
  `"midday"` | `"pm"` | `"closed"`。`is_open` = phase ∈ {open, am, pm}。
  session_id 仅在 closed→(am|open) 递增。next_transition_at 语义保持。
- `session_clock_loop`：**只有进入 closed 才触发 on_close（结算/DAY
  过期），只有从 closed 进入 am/open 才触发 on_open**——午休不结算、
  不滚 prev_close、不解锁 T+1、不过期 DAY 单；等价于"暂停"。
- `GET /api/market/session` 响应增 `phase` 键（永远存在；us 为
  open/closed）。核对现有 session 路由测试是否断言精确 dict——若是，
  只允许以"增键"方式扩展断言之外的行为（不许改测试）。
- main.py：profile.midday_break 且时钟启用时，midday_break_seconds =
  FINALLY_SESSION_BREAK_SECONDS 的解析值（默认 120）。
- 闭市拒单消息 locale 化：zh:「休市中」（现 "Market closed"）。

## 6. CN-1 遗留缺口：板块 burst 的 sector 注入

`compute_peer_shocks(..., sector_fn: Callable[[str], str] | None = None)`
增量参数（None → 现有模块级 sector_for）；`GBMSimulator.step` 在
universe 注入时传 `universe.sector_for` → CN 事件正确联动白酒/新能源
板块。

## 7. 回测口径一致（backtest.py 增量）

- `normalize_backtest_config(..., profile: MarketProfile | None = None)`：
  买入数量整手校验（zh 消息同 §3）；universe 参数保持 CN-1 语义。
- `run_backtest(..., profile=None)` → `_simulate` 费用改走 §1 公式
  （None/us → 现有 bps 数学逐分不变）；`profile.t_plus > 0` 时**入场
  当日禁止出场**——TP/SL/horizon 检查跳过 entry 同日的 bar（按
  BARS_PER_DAY 判定日界）。
- routes/backtest.py 工厂已有 profile（CN-1）——透传即可。

## 8. 测试（预计 +60，全部新文件）

- US_PROFILE ≡ None 恒等性（trade/order/rule/backtest 各一组）。
- 迁移幂等；T+1：买入锁定、卖出拒绝（zh 消息）、部分可卖（老仓 100 +
  今买 100 → 可卖 100）、roll 解锁、24/7 禁用、AI/规则路径同样受限。
- 整手：买 150 拒/买 200 过/卖 37 过；rule 创建与回测 config 校验。
- 涨跌停：漏斗钳制（含事件 shock 超板被钳）、to_dict 键条件输出、
  us SSE 形状不变、内部价回写、委托超板 400、prev_close 滚动后板价
  重算。
- 费用：min ¥5 地板（小单）、大单按 bps、印花税仅卖出、realized_pnl
  口径。
- 午休：phase 序列 am→midday→pm→closed→am、午休不触发 on_close/
  on_open、session_id 只日增、midday=0 与现状恒等、session 路由带
  phase、午休期间市价单拒（zh）。
- burst sector_fn 注入（CN 两只白酒联动、跨板块不联动）。
- 回测：T+1 出场推迟到次日 bar、CN 费用进 stats.commission_paid。
