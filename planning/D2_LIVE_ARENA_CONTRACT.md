# D2 契约 — A股实时喂价 · ATR/KDJ · 限时私房赛 · 组合 VaR/beta（V3 §三）

前置：D1 已落地（644ff17）。基线：**pytest 1485 / jest 534（68
suites）/ E2E us 38 + cn 16 一字不改全部通过**；`next build` 干净；
金样本、零外网、CN 字节钉死照旧。

核心不变量（对抗验证重点核查）：
- **默认行为零变化**：不设 FINALLY_LIVE_SOURCE 时数据源选择与现状
  逐字节一致（MASSIVE_API_KEY→Massive，否则模拟器）；模拟器仍是
  产品默认。akshare 实时源是**显式 opt-in**。
- **测试零外网**：AkshareLiveSource 测试全走注入 fake fetcher；
  E2E 不启用实时源；VaR/beta 测试用 sample daily_bars。
- 竞赛无后台循环：状态由时间推导，榜单读时计算；结束后终值取
  ends_at 前最近一次 portfolio_snapshot（30s 快照本就存在）。
- 竞赛**创建仅 cookie**（Bearer 403，对齐管理面红线）；**加入/查看
  允许 Bearer**（机器人可自报名参赛——竞技场闭环的刻意设计，
  文档备案）。
- 声明式底线不变：新指标字段全走 FIELD_SPECS 注册表严格校验。
- 既有测试一字不改全绿；analytics 既有键集不动（新键 additive）。

---

## 1. A 股实时喂价（backend/app/market/akshare_live.py + factory）

- **AkshareLiveSource** 镜像 MassiveDataSource 形态（start/stop/
  add_ticker/remove_ticker/_poll_loop/_poll_once）：每
  `FINALLY_AKSHARE_POLL_SECONDS`（默认 15，夹 5..120）拉一次
  ak.stock_zh_a_spot_em 全市场快照（延迟导入，fetcher 可注入），
  过滤宇宙内代码 → cache.update（最新价；成交量列为**累计量**，
  写入前对上次值做差得每 tick 增量，首帧增量记 0；有买一卖一列则
  带上，无则省略）。异常：log + 保留上帧报价，循环不死；连续失败
  节流告警（复用 warn 机制风格）。
- **选择逻辑**（factory.py + main.py 增量）：新 env
  `FINALLY_LIVE_SOURCE` ∈ auto(默认)|simulator|massive|akshare。
  auto = 现状逐字节（MASSIVE_API_KEY 有→massive 否则 simulator）；
  simulator/massive 显式指定；akshare 仅当 FINALLY_MARKET=cn，
  否则**启动即失败**（RuntimeSettings.validate 式明确报错——显式
  误配置不静默）。akshare 与 massive 同样**强制 always_open**
  （session.py 条件增量，镜像 MASSIVE 分支）。
- 文档（.env.example + planning/OPERATIONS.md + CURRENT.md）：真实
  行情收盘时段报价冻结 → 新鲜度闸门会拦截交易（预期行为；课堂
  白天用，默认仍是模拟器）；akshare 数据仅供教学。

## 2. ATR / KDJ（indicators.py + FIELD_SPECS）

- 纯函数：`atr(highs, lows, closes, period=14)`（Wilder TR 平滑）、
  `kdj(highs, lows, closes, n=9, k_smooth=3, d_smooth=3) ->
  (K, D, J)`（RSV 递推，K/D 初值 50，J=3K−2D）。`*_series` 变体
  供回测快路径，与 D1 模式一致。
- FIELD_SPECS 新字段（op above/below，多余键 400，暖机→False）：
  | field | params | 语义（above） |
  |---|---|---|
  | kdj_cross | n 5..30 默认 9 | K 本根上穿 D（前根 K≤D 且当根 K>D；below=下穿） |
  | atr_pct | period 5..50 默认 14；value 必填 >0 | ATR/close×100 ≥ value（波动率过滤） |
- 金向量：开发期以 pandas-ta-classic（或手算）预算值内嵌测试，
  无运行时依赖；实时（1m 聚合）与历史（日线）同函数复用。
  live warmup 容量 pin 测试若受影响，按 D1 先例更新容量常数断言
  ——**仅允许改 D1 新增的容量 pin 测试，HEAD 基线测试不动**。

## 3. 限时私房赛（backend/app/routes/competitions.py + schema）

- **competitions 表**：`id TEXT PK, name TEXT NOT NULL, code TEXT NOT
  NULL UNIQUE`（6 位 A-Z2-9 去易混淆字符）`, created_by TEXT NOT
  NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, created_at
  TEXT NOT NULL`；**competition_members 表**：`competition_id, user_id,
  joined_at TEXT, baseline_value REAL NOT NULL, PK(competition_id,
  user_id)`。无迁移（新表）。状态推导：now<starts→upcoming（本期
  starts=create 即 running）、<ends→running、否则 ended。
- 端点：
  - `POST /api/competitions` `{name 1..40, hours 1..168}`（cookie
    only，Bearer 403）→ 201 `{competition}` 含 code；创建者自动入赛
    （baseline=compute_standings 口径的当前 total_value）。每用户
    进行中创建数 ≤5（400）。
  - `POST /api/competitions/join` `{code}`（Bearer 允许）→ running
    才可入（ended/未知 code→400/404）；重复加入幂等返回 200 现状。
  - `GET /api/competitions?scope=mine(默认)|all` → `{competitions:
    [{id,name,code(仅 mine 且本人创建),status,member_count,starts_at,
    ends_at}]}`。
  - `GET /api/competitions/{id}` → 详情 + `board:[{user_id,name,
    baseline_value,value,return_pct,rank}]`：running 用实时
    compute_standings 口径；ended 用各成员 ends_at 前最近一次
    portfolio_snapshot（无快照 → baseline，收益 0）。rank 按
    return_pct desc，并列按 joined_at。
- 竞赛不隔离资金（同一组合参加多赛，教学取舍写文档）；赛季重置
  （seasons）清空组合会影响进行中竞赛——board 如实反映，备案。

## 4. 组合 VaR / beta（portfolio.py analytics 增量）

- 数据：daily_bars（source 无关，sample 开箱可用）。当前持仓市值
  权重 w_i；取全部持仓共同覆盖的最近 ≤60 根日线收盘算日收益：
  组合日收益 r_p = Σ w_i·r_i；市场基准 = 宇宙等权日收益（同日期
  交集）。
- `var_95_pct` = −5 分位(r_p)×100（1 日历史 VaR，正数表示损失%，
  2dp）；`beta` = cov(r_p, r_mkt)/var(r_mkt)（2dp）。共同 bar <20
  或无持仓或基准方差为 0 → 两者 null。响应 additive 增三键：
  `var_95_pct, beta, risk_window_bars`（实际参与根数，无数据 0）。
  既有键与数值逐字节不变（回归测试）。
- 计算在既有 analytics 路径内联（同 conn 读 daily_bars），无新
  端点。

## 5. 前端

- **/arena 竞赛区**（纯追加，排行榜/赛季区 DOM/testid 不动）：
  `comp-create` 表单（`comp-name`/`comp-hours` 1..168 校验）→ 创建
  成功展示 code + 复制；`comp-join-code` 输入 + `comp-join` 按钮
  （错误 toast）；我的竞赛列表 `comp-row-${id}`（名称/状态 chip/
  人数/倒计时 `comp-countdown-${id}`，每秒本地递减）→ 点击展开
  `comp-board-${id}`（rank/name/return% 方向色，SWR 10s；ended 显示
  终榜标记）。Guest 可创建可加入（单机模式照常）。
- **Analytics 页签**：追加两张 StatCard `analytics-var` /
  `analytics-beta`（null → 显示 "—" + `analytics-risk-hint` 提示
  "同步历史数据后可用"，链接市场页数据卡）；`risk_window_bars`
  角标。既有卡与 testid 不动。
- i18n `arena.comp*`、`analytics.var/beta/riskHint` en/zh；金额
  formatMoney、方向色变量。
- 实时源不做前端切换 UI（env 决定），仅 StatusBar 现有 feed 延迟
  显示天然生效——不改前端。

## 6. 测试

- 既有全套一字不改全绿（金样本、零外网、analytics 既有键回归）。
- 新 pytest（约 +60）：AkshareLiveSource（fake fetcher：解析/宇宙
  过滤/累计量差分首帧 0/负差归 0/异常循环不死/add-remove）；factory
  矩阵（auto 与现状逐字节、akshare+us 启动失败、akshare 强制
  always_open）；ATR/KDJ 金向量 + cross 边界 + 校验矩阵 + 回测/
  实盘双路径可用；competitions（创建/上限 5/入赛幂等/ended 400/
  未知 404/board 排名并列/ended 终值取快照/无快照回退 baseline/
  Bearer 矩阵：create 403 join 200/跨用户可见性）；VaR/beta
  （构造 daily_bars 固件手算对照/共同窗口对齐/不足 20 null/无持仓
  null/基准零方差 null/既有键字节回归）。
- 新 jest（约 +30）：竞赛区四态（创建成功展示 code/加入错误 toast/
  倒计时递减/board 排名方向色/ended 标记）；analytics 两卡 + null
  提示；i18n 键集对齐。
- 新 E2E `arena-comp.spec.ts`（US ~3 条）：创建竞赛→board 出现
  创建者；join code 第二身份（request context 登录 Bob）→ 两行
  排名；买入+sample 同步后 Analytics 显示 VaR/beta 数值（或首次
  null→同步→出数的完整链）。`arena-comp-cn.spec.ts`（~1-2 条：
  中文文案 + ¥）。cn compose 追加。实时源不进 E2E。
- 双市场回归 = 全套件 + US/CN compose 全绿。

## 7. workflow 分工（实现→对抗验证→修复）

- **BA 后端市场 agent**：§1 实时源/factory/session + §2 指标 +
  对应 pytest（只碰 backend/，main.py 接线归它）。
- **BB 后端服务 agent**（BA 后串行，避免 main.py/schema 冲突）：
  §3 竞赛 + §4 VaR/beta + 对应 pytest（只碰 backend/）。
- **F 前端 agent**（并行，只碰 frontend/）：§5 + jest。
- **E2E agent**（并行，只碰 test/）：§6 两 spec + cn compose 增量。
- **对抗验证**：套件门槛（金样本+零外网+analytics 键回归）+ 三路
  （后端市场与指标数学、竞赛/风险口径与权限矩阵、前端+CN+E2E）
  → 修复循环 ≤3 轮。

---

## 附：实施偏离备案（已认可，2026-07-12）

1. **注册表经 ACTIVE_FIELD_SPECS 下钻**而非直接扩 FIELD_SPECS：
   既有测试钉死了 P2/D1 各表键集，按 D1 pinned-registry-split 先例
   再分一层；校验/求值/容量推导全读新总表，行为等价。
2. **always_open 条件落在 main.py**：HEAD 的 MASSIVE 强制 24/7 分支
   本就在 main.py `_create_session_clock`（契约笔误写作 session.py），
   akshare 分支照此镜像。
3. **显式 massive 无 key → 启动 ValueError**（契约未明说，按"显式
   误配置不静默"原则补齐）。
4. **冻结帧跳写**：东财 spot 无交易所时间戳，价不变且零成交增量的
   帧不重打时间戳——否则收盘后报价永远"新鲜"，新鲜度闸门的拦截
   承诺不成立。
5. **ATR/KDJ 金向量为手算**（Fraction 精确参考实现 + 闭式小例），
   未用 pandas-ta-classic：其 RMA ewm 种子与契约钉死的 Wilder/50
   初值递推不同。
6. **VaR/beta 基准"宇宙"读作 default_watchlist**（US 10 支权益）而
   非含 BTC/ETH 的 seed_prices 全集：随库 sample 数据下两读法逐字节
   等价；用户补齐 crypto 日线后基准仍排除之——口径钉死于此，如需
   全集口径属后续演进。
