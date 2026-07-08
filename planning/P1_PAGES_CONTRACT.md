# P1 契约 — 多页面工作台骨架（V2 §一）

前置：V2_EXPANSION_PLAN.md 已确认；CN-1..4 已落地（abab154）。
范围：全局导航 + 跨页常驻 AI 聊天 + `/market` `/symbol?c=X` `/journal`
`/arena` 四页（重组现有数据 + 少量新端点）。策略/回测库/开发者页属
P2/P3；情绪指数/相关性热力图/成交日历/选手主页属 P4，本期不做。

硬门槛：**现有 pytest（~762）/ jest（~220）/ E2E 10 spec 一字不改全部
通过**（us 默认 + cn 套件）；`next build` 静态导出干净；交易台（`/`）
现有布局、testid、键盘快捷键、交互全部不动。

核心不变量（对抗验证重点核查）：
- **index.test.tsx 的命门**：Dashboard 自身必须继续渲染 Header/
  NewsTicker/StatusBar/ChatPanel 与三栏布局（Test 5b/8/9/10/14）→
  聊天"下沉"**不是**把 JSX 挖到 _app，而是：SSE 订阅下沉 _app（唯一
  不能断的资源）+ 聊天状态下沉 uiStore（开合/草稿跨页存续）+ 新页面
  经 AppShell 挂同一个 ChatPanel 组件。Dashboard JSX 仅两处最小改动
  （见 §2），其余原样。
- **Header 不得裸用 useRouter 属性**：jest 直挂 `<Header/>` 无
  RouterContext → 一律 `router?.pathname ?? '/'` 空安全，否则现有
  Header 测试崩（gate 失守）。
- 既有端点响应**字节不变**：新增只做"新端点 + 既有端点可选参数且
  默认行为不变"（§3）。`/api/market/events` 原样保留（NewsTicker 依赖）。
- 新页面全部走 i18n 双字典 + `terminal-up/down`（CSS 变量方向色）+
  `formatMoney/formatShares` + `profile.names` —— cn 下红涨绿跌、中文、
  ¥、名称、涨跌停徽章第一天就对。
- **Next 版本告警**（frontend/AGENTS.md：非标准 Next）：前端动工前必须
  先查 `node_modules/next/dist/docs/` 确认 Pages Router 的 Link/
  useRouter/trailingSlash/静态导出行为，以本地文档为准。

---

## 1. 路由与静态导出

- 新页面文件：`src/pages/{market,symbol,journal,arena}.tsx`（Pages
  Router；个股页动态参数走 query：`/symbol?c=600519`）。
- `next.config.js` 增 `trailingSlash: true` → 导出 `market/index.html`
  等目录形态；Starlette `StaticFiles(html=True)` 对 `/market` 307 →
  `/market/` → 命中 index.html，**深链接/刷新可用**（E2E 必测：直开
  `/market/` 与无斜杠 `/market` 都要通）。根 `/` 行为不变。
- `/symbol` 静态导出下首帧 `router.query.c === undefined`（hydration
  时序）→ 页面必须优雅处理：undefined 渲染空态占位
  `symbol-empty`，query 就绪后再挂图表；代码一律大写归一。
- 导航用 next/link 客户端路由 → SSE/priceStore/SWR 缓存跨页不断流。

## 2. 全局架构

- **_app.tsx**：增 `usePriceStream()` 调用（与 MarketProfileEffect 并
  列）——全应用唯一 SSE 连接；`index.tsx` 删除该调用（index.test 只
  mock 未断言调用次数，安全）。其余 _app 结构不动。
- **uiStore 增量**：`chatOpen: boolean`（默认 true）+ `setChatOpen`；
  `chatDraft: string` + setter（输入草稿跨页存续）；
  `pendingChatMessage: string | null` + setter（一次性：ChatPanel effect
  消费→自动发送→清空；默认 null 时零行为差异）。
- **index.tsx 仅两处改动**：①删 `usePriceStream()`；②本地
  `chatOpen` useState 换读写 uiStore（默认 true → Test 8 照过）。
  其余（selectedTicker、快捷键、布局、datalist）逐行不动。
- **ChatPanel 增量（additive）**：草稿受控于 uiStore.chatDraft；effect
  监听 pendingChatMessage（非空且非 loading → 作为用户消息发送并清空）。
  props 接口 `{open,onToggle,onNewTrade?}` 不变 → ChatPanel.test 照过。
- **AppShell（新组件 `src/components/AppShell.tsx`）**：新四页的统一
  chrome：`<Header/> <NewsTicker/>` + 主区（children）+ 右侧 ChatPanel
  停靠列（w-80/w-8 开合，读 uiStore）+ `<StatusBar/>`；根 div 复用
  `h-screen overflow-hidden flex flex-col bg-terminal-bg text-terminal-text`
  视口锁定模式（页面不滚、面板滚）。onNewTrade 用
  `useSWRConfig().mutate` 重验 `/api/portfolio/` `/api/portfolio/trades`
  `/api/portfolio/orders?status=open` `/api/rules` `/api/watchlist/`
  （与 index refreshAfterTrade 同键集）。**交易台不用 AppShell**（保持
  现状 JSX）。
- **Header 增导航区**（品牌与右簇之间，additive）：交易台 `/`、市场
  `/market`、复盘 `/journal`、竞技场 `/arena`；testid
  `nav-desk|nav-market|nav-journal|nav-arena`；激活态按空安全 pathname
  （斜杠归一后比较）高亮（accent 下划线/文字色）；i18n `nav.*`。
- **SymbolLink（新组件）**：`<Link href={{pathname:'/symbol',
  query:{c}}}>`，testid `symbol-link-${code}`，样式继承+hover 下划线。
  应用面：新四页所有代码、NewsTicker 事件项、PortfolioTabs 四表
  （positions/orders/fills/rules）的 ticker 单元格。**WatchlistRow 不
  改**（点击选图是既有交互与 E2E 契约）。若任一现有 jest 因链接包装
  失败 → 回退该处应用，**不改测试**。

## 3. 后端增量（全部 additive）

1. **market_events 表**（schema.sql 增，init_db 幂等）：
   `id TEXT PK, ticker TEXT NOT NULL, headline TEXT NOT NULL,
   narrative TEXT, change_percent REAL NOT NULL, direction TEXT NOT NULL,
   timestamp REAL NOT NULL`；索引 `(timestamp DESC)`、`(ticker,
   timestamp DESC)`。市场级数据，无 user_id。
2. **事件落库循环**（main.py lifespan 后台任务，参照 briefs 循环）：
   每 ~5s 取 `price_cache.get_events(limit=100)`，
   `INSERT ... ON CONFLICT(id) DO UPDATE SET narrative=excluded.narrative`
   —— 叙事晚到自动回填（事件仍在 100 环形缓冲窗口内即可）。
3. **GET `/api/market/events/archive?ticker=&limit=&before=`**（新，
   market.py，无鉴权）：读 DB；ticker 可选（大写归一精确匹配）；limit
   默认 50 夹 1..200；before 可选 float 时间戳（严格小于，翻页游标）；
   按 timestamp DESC。→ `{"events":[{id,ticker,headline,narrative,
   change_percent,direction,timestamp}], "has_more": bool}`。
4. **GET `/api/market/quotes`**（新，market.py，无鉴权）：PriceCache
   全量快照 → `{"quotes":[{...PriceUpdate.to_dict(), "sector": str}]}`，
   按 ticker 升序；sector 取 universe.sector_for（未知→"other"）。
5. **GET `/api/portfolio/trades` 增 `ticker` 可选参数**：大写归一，
   `AND ticker=?`；缺省 SQL 与响应字节不变。
6. **GET `/api/chat/` 增 `kind`、`limit` 可选参数**：kind ∈
   chat|brief|review|rule（非法 400）；limit 默认 20 夹 1..200；语义
   仍是"最近 N 条（可按 kind 过滤）升序"；缺省行为字节不变。
7. 排行榜/赛季/复盘执行不改：`/api/leaderboard` `/api/seasons`
   `POST /api/chat/review` 现状够用。

## 4. /market 全市场页

- 数据：`/api/market/quotes`（SWR 10s，初始快照+sector）⊕ priceStore
  实时覆盖（SSE 本就推全宇宙）；名称取 profile.names。
- **行情网格** `market-grid`，行 `market-row-${ticker}`：代码（cn 下
  并排名称）、现价（沿用 flash-up/down 动画类）、日涨跌%（方向色）、
  日高/低、量（每 tick）、板块 chip、cn 涨跌停徽章（复用
  `limit-badge-*` 模式）。表头点击客户端排序（代码/涨跌%/量），默认
  代码升序（确定性，利于测试）。行点击 → `/symbol?c=X`。
- **板块热力图** `market-heatmap`（DOM 实现，非 canvas）：按 sector
  分组的等尺寸磁贴，`market-heatmap-tile-${ticker}`，底色
  `color-mix(in srgb, var(--color-up|down) N%, transparent)`，N 随
  |day_change_percent| 线性夹至 3% 满档；贴内代码+涨跌%；点击跳个股。
  cn 方向色自动翻转（变量层，零额外代码）。
- **事件归档** `market-events`：`/archive` 端点分页列表，项
  `market-event-${id}`：本地时间、SymbolLink 代码、headline、
  narrative（muted 次行）、涨跌%（方向色）；`market-events-more`
  加载更多（before=当前最旧 timestamp）；空态 i18n。

## 5. /symbol 个股详情页

- `c` 归一大写；无效/未就绪 → `symbol-empty` 空态。
- 标题行：代码+名称+实时大字价（flash）+日涨跌%+cn 涨跌停徽章。
- **主图**：直接复用 `<MainChart ticker={c}/>`（1s/5s/1m 多周期、
  方向色、涨跌停徽章全部现成）。
- **当日统计** `symbol-stats`：昨收、日高/低、振幅
  `(high-low)/prev_close*100`（prev_close>0 守卫）、量、买一/卖一、
  cn 下涨停/跌停价两行。数据：`useTicker(c)` 实时，初始回落
  `/api/market/quotes`。
- **下单**：复用 `<TradeBar selectedTicker={c}/>`，onTradeComplete 走
  AppShell 同键集重验。
- **我的持仓** `symbol-position`：`/api/portfolio/` 客户端 find；
  qty/avg_cost/浮盈/％（formatShares/formatMoney）；空态。
- **我的成交** `symbol-trades`：`/api/portfolio/trades?ticker=c&limit=100`。
- **该票事件史** `symbol-events`：`/archive?ticker=c`。
- **AI 分析按钮** `symbol-ai-analyze`：置
  `pendingChatMessage = t('symbol.aiPrompt', {ticker})` + chatOpen=true
  → 全局聊天自动发送（en/zh 模板进字典）。

## 6. /journal 复盘页

- **复盘归档** `journal-reviews`：`/api/chat/?kind=review&limit=100`
  倒序展示全文（kind 边框色沿用 KIND_BORDER 语义）；
  `journal-run-review` 按钮 → POST `/api/chat/review` → 重验列表
  （loading/错误态同 ChatPanel 复盘按钮模式）。
- **按日成交** `journal-days`：`/api/portfolio/trades?limit=500` 客户端
  按本地日分组；日节 `journal-day-${YYYY-MM-DD}`：日期头（笔数 +
  当日已实现盈亏合计，方向色）+ 行（时间、SymbolLink、方向、量
  formatShares、价、佣金、realized_pnl）。
- **按标的过滤** `journal-filter`：ticker 文本输入客户端过滤。
- 空态 i18n；成交日历（P4）不做。

## 7. /arena 竞技场页

- **排行榜**：`<Leaderboard/>` 组件零改动直接挂载（board 页签同时
  保留，PortfolioTabs 不动）。
- **赛季史** `arena-seasons`：`/api/seasons`；每季 `arena-season-${id}`：
  期间、进行中标记；已结束季展开结果表（rank/name/final_value/
  return_pct，formatMoney，冠军高亮 accent）；空态 i18n。

## 8. i18n 与双市场

- 新键命名空间：`nav.*`、`market.*`、`symbol.*`、`journal.*`、
  `arena.*`，en/zh 双字典同步补齐；不译 testid/代码/数字。
- 方向色只经 `terminal-up/down` 类或 `var(--color-up/down)`；连接点
  语义色规则沿用（不随市场翻转）。
- cn 验收（8801）：导航中文、市场页名称列+涨跌停徽章、热力图红涨
  绿跌、个股页 ¥/手/涨跌停价、journal 中文分组、arena ¥。

## 9. 测试

- 现有全部套件零改动全绿（us + cn）。
- **新 pytest（约 +28）**：market_events 建表/落库循环 upsert/叙事
  回填/去重；archive 端点（ticker 过滤、before 翻页、limit 夹取、
  空库）；quotes 端点（形状、sector、排序、cn 宇宙）；trades ticker
  过滤 + 缺省字节不变；chat kind/limit + 非法 400 + 缺省字节不变。
- **新 jest（约 +35）**：uiStore 增量（含 beforeEach 重置，防单例
  泄漏）；Header 导航 testid/激活态/无 Router 不崩；AppShell chrome
  齐全+视口锁定类；SymbolLink href；market 页网格渲染/排序/热力图
  磁贴方向类/事件列表；symbol 页 query 解析/空态/振幅计算/按票成交；
  journal 按日分组与合计/过滤；arena 赛季渲染；ChatPanel
  pendingChatMessage 自动发送。
- **新 E2E `pages.spec.ts`**（US 项目自动纳入，约 7 条）：①导航四页
  往返且连接点保持 connected（SSE 跨页不断）；②深链接直开
  `/market/` 与 `/market`（307）都渲染网格；③市场页网格有行且价格
  在跳；④点代码 → `/symbol?c=` 图表+统计渲染；⑤个股页下单成功
  （复用 trade 流断言）；⑥journal 运行复盘（LLM_MOCK）出现归档项；
  ⑦arena 排行榜+赛季渲染。
- **新 E2E `pages-cn.spec.ts`**（文件名同时命中 CN 项目 testMatch 且
  被 US 项目 testIgnore 排除，零配置歧义；cn compose 命令改为跑
  `cn.spec.ts pages-cn.spec.ts` 两文件——基建改动，非测试改动）：
  中文导航、市场页名称列、个股页 ¥ 与涨停价行。
- 双市场回归 = jest + pytest + `next build` + US compose E2E + CN
  compose E2E 全绿。

## 10. workflow 分工（实现→对抗验证→修复）

- **W1 后端 agent**（只碰 backend/）：§3 全部 + 新 pytest。与前端
  文件集不相交，可并行。
- **W2 前端基建 agent**（只碰 frontend/）：先读 Next 本地文档（§0
  告警）；§1 + §2（_app/uiStore/index 两处/ChatPanel 增量/AppShell/
  Header 导航/SymbolLink）+ `nav.*` i18n + 对应 jest。
- **W3 前端页面 agent**（W2 后串行，避免 i18n.ts/组件冲突）：§4-§7
  四页 + 各自 i18n 键 + 对应 jest。
- **W4 E2E agent**（W1-3 后）：pages.spec.ts + pages-cn.spec.ts +
  cn compose 命令增量。
- **对抗验证**：契约逐条核查（尤其"核心不变量"五条）+ 全套件回归 +
  修复循环，绿了才提交。
