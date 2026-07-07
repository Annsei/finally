# CN-3 契约 — 前端红涨绿跌 + 中文化 + 手/¥ 格式 + AI 中文提示词

前置：CN-1（profile 端点）、CN-2（机制）已落地（1dc630c）。
硬门槛：**默认 us 下现有 176 jest 一字不改全部通过**；`next build` 干净；
zh 仅在 `data-market="cn"` / profile.locale=='zh-CN' 时生效。

核心不变量（验证阶段重点核查）：
- 颜色翻转在 **CSS 变量层**，class 名不变 → 所有断言 `text-terminal-up`
  等的 jest 保持通过。
- **en 字典逐字等于现有硬编码文案**（含标点/大小写）→ 断言英文文案的
  jest（`getByText('Positions')`、`/No standing rules/i` 等）保持通过。
- profile SWR 未加载（undefined）时**默认回落 us** → 不 mock profile 的
  现有组件测试保持 us 行为。
- testid 契约完全不动 → 现有 E2E 不受影响。

---

## 1. 运行时 profile（前端）— 新文件 `src/lib/marketProfile.ts`

- `useMarketProfile()`：SWR on `/api/market/profile`。返回带 us 默认值的
  profile 对象（加载中/失败 → us 默认：market 'us'、currency '$'、
  locale 'en-US'、lot_size 1、up_is_red false、names {}、price_limit_pct
  {}、seed_cash 10000）。字段名严格对齐 CN-1 端点响应。
- `applyMarketAttr(market)`：`document.documentElement.setAttribute(
  'data-market', market)`（us 时也可设 'us'，CSS 默认值即 us，无副作用）。
- `directionColors(upIsRed)`：返回 `{up, down}` 的 hex（供 canvas 图表用
  —— lightweight-charts 是 canvas，读不到 CSS 变量）。us:{up:'#22c55e',
  down:'#ef4444'}；cn 交换。
- `_app.tsx` 增量：挂一个消费 useMarketProfile 的小组件/effect，设置
  data-market 属性。us（默认）→ 属性为 'us' 或不设，视觉不变。

## 2. 红涨绿跌 — CSS 变量翻转（globals.css + tailwind.config）

- globals.css `:root` 增：`--color-up:#22c55e; --color-down:#ef4444;`
  （= 现值）。增 `:root[data-market="cn"]{ --color-up:#ef4444;
  --color-down:#22c55e; }`。amber/accent/blue/purple 不变。
- tailwind.config：`terminal.up`→`var(--color-up)`、
  `terminal.down`→`var(--color-down)`。**flash keyframes** 的 rgba 改为
  基于变量（可用 `color-mix(in srgb, var(--color-up) 25%, transparent)`
  作 flash 起始色，或在 globals.css 用变量重定义 flash 工具类）——
  要求：us flash 颜色与现状视觉一致，cn 随方向翻转。
- **内联 hex 转换**：7 个组件里表示**价格方向**的 `#22c55e`/`#ef4444`
  内联样式改为 `var(--color-up)`/`var(--color-down)`（jest 不断言内联
  hex，安全）。含 ChatPanel 徽章、Header、NewsTicker、TradeBar 估算等。
- **买卖按钮**：Buy 按钮用 `var(--color-up)`、Sell 用 `var(--color-down)`
  → us 买绿卖红（现状），cn 买红卖绿（A 股 买盘红/卖盘绿 惯例）。
- **canvas 图表**（MainChart 蜡烛、PnLChart、SparklineChart、Heatmap 若
  用 canvas）：颜色不能用 CSS 变量，改为从 `directionColors(profile.
  up_is_red)` 取 hex 传入图表 options；profile 变化时重建/更新。us →
  取到现有绿涨红跌，视觉不变。
- **必须钉住不翻的状态色**：连接状态点
  `[data-testid="connection-status"]`。它复用 bg-terminal-up/down/amber
  表达"已连接/断开"，属于**通用状态语义**（绿=连接 全球通用），不随
  涨跌翻转。做法：globals.css 用属性选择器按 data-state 钉死固定色
  （connected→#22c55e，disconnected→#ef4444，reconnecting→amber 不变），
  class 名保留 `bg-terminal-up` 等 → Header 测试通过，渲染色被覆盖。
  属性选择器特异性高于单类，无需 !important。
- 其余用 terminal-up/down 的元素（P&L、自选涨跌、买卖列、feed 延迟、
  分析）**随方向翻转**——它们都是"涨/跌/盈/亏"语义，翻转正确。

## 3. i18n — 新文件 `src/lib/i18n.ts`

- `t(key: string, params?: Record<string,string|number>) `+ `en`/`zh`
  两套字典 + `useT()`（locale 来自 profile，默认 en）。
- **en 字典值 = 现有硬编码英文原文，逐字节一致**（这是 us 回归的命门）。
- 覆盖静态 UI 文案：PortfolioTabs 页签、各表头、按钮（Buy/Sell/Send/
  Review/Run Backtest…）、输入占位符、空状态、toast 文案、区块标题
  （PORTFOLIO P&L、FINALLY AI…）、Header 标签（CASH/REALIZED/DAY P&L/
  PORTFOLIO）、StatusBar（OPEN closes in…/Shortcuts…）。
- **不翻译**：testid、ticker 代码、数字。**testid 一律不动**。
- 动态文案（含变量）用 params 占位，如 `t('concentration.warn',
  {ticker, pct})`。
- zh 术语对齐：Positions 持仓 / Orders 委托 / Fills 成交 / Rules 规则 /
  Backtest 回测 / Analytics 分析 / Board 榜单 / Buy 买入 / Sell 卖出 /
  Cash 可用 / Day P&L 当日盈亏 / Portfolio 总资产 / Realized 已实现。

## 4. 手/¥ 格式（`format.ts` 增量，勿动 formatQuantity）

- `formatMoney(n, {currency_symbol, locale})`：符号 + 分组，2 位小数。
- `formatLargeCount(n, locale)`：zh → 万/亿（如 3.5万手、成交量），en →
  现状（K/M 或原样，保持现有成交量显示不变）。
- `formatShares(n, profile)`：cn(lot>1) → 「N手」（整手）+ 零股附注；
  us → 复用 formatQuantity（不变）。
- **TradeBar 手模式**（profile.lot_size>1）：Qty 输入单位为「手」，
  label「手」，提交时 quantity = 输入×lot_size；Max buy/Held 以手显示；
  校验整手（后端也校验，前端仅提示）。us(lot=1) 路径逐行不变。
- **WatchlistRow**：profile.names[code] 存在时显示「代码 名称」双行/并排
  （如 600519 贵州茅台）；us(names={}) → 仅代码，现状不变。
- **涨跌停徽章**：watchlist 行/主图，当 quote.price≈limit_up → 「涨停」
  （红/CN 语义），≈limit_down →「跌停」。us（无 limit 字段）→ 不显示。

## 5. 后端 AI 中文提示词（chat.py / briefs.py）—— 按 profile.locale 切换

- chat.py `SYSTEM_PROMPT`、briefs `BRIEF_SYSTEM_PROMPT`/
  `NARRATIVE_SYSTEM_PROMPT`、review `REVIEW_SYSTEM_PROMPT`：locale=='zh-CN'
  → 中文版；否则现有英文，**逐字节不变**（profile None/us 走英文）。
- 中文版注入 A 股约束：买入必须整手（100 股整数倍）、T+1（今日买入
  次日方可卖出）、货币 ¥、术语（涨停/跌停/印花税）；否则 AI 会生成
  被后端拒的非法交易。**结构化输出 schema 字段名不变**（trades/orders/
  rules/backtests 英文键）——前端零适配，只有 message/叙事语言变。
- LLM_MOCK **中文分支**：locale zh 时返回确定性中文 mock（含 backtest
  关键词分支的中文版），供 CN E2E。us mock 逐字节不变（现有 E2E 命门）。
- 路由/后台循环需拿到 profile：chat 工厂已有（CN-2）；briefs_watch_loop
  增量 profile 参数，main.py 注入；review 路由用 chat 工厂的 profile。
- 现有 chat/briefs 测试用 LLM_MOCK 且断言精确英文 mock → 保持 us、不变。

## 6. 测试

- 现有 176 jest 在默认（无 profile mock / undefined）下全绿，零改动。
- 新 jest（预计 +20）：useMarketProfile 默认 us / cn 解析；i18n en==原文
  抽样 + zh 渲染（mock profile locale zh）；formatMoney/LargeCount/Shares；
  TradeBar 手模式提交 ×lot 且校验整手；WatchlistRow 名称行；连接点在
  cn 下仍绿（可查属性选择器存在性或渲染 class 不变）；涨跌停徽章。
- 新 pytest（预计 +15）：zh 提示词选择、中文 mock 分支、us 提示词字节
  不变、schema 字段不变、briefs profile 注入。
- 现有 pytest 788 全绿零改动。
- 交付验证放 CN-4：8801 起 cn 容器，浏览器实测红涨绿跌 + 中文 +
  连接点仍绿 + 8800 us 视觉不变。

## 7. 分工（workflow）

- 前端 agent：src/lib/{marketProfile,i18n}.ts、format.ts 增量、globals.css
  + tailwind.config、_app.tsx、~20 组件 t()/颜色/格式改造、TradeBar 手、
  WatchlistRow 名称、涨跌停徽章、jest。**只碰 frontend/**。
- 后端 agent：chat.py + briefs.py 提示词 locale 化 + 中文 mock + profile
  注入 + pytest。**只碰 backend/**。两者文件集不相交，可并行。
