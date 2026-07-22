# P3 契约 — 量化接口：FinAlly 作为开放纸面券商（V2 §三）

前置：P1/P2 已落地（5389be9）。基线：**pytest 1143 / jest 376（50
suites）/ E2E us 24 + cn 10 —— 一字不改全部通过**；`next build` 干净。
范围：API Key 认证 + 限流 + 授权护栏 + 审计台账 + /developers 页 +
OpenAPI 文档暴露 + 示例机器人。P4 不做。

核心不变量（对抗验证重点核查）：
- **无 Authorization 头的请求行为字节不变**：cookie/匿名路径（全部
  现有 UI 与 E2E 流量）不经任何新逻辑分支；`get_current_user_id`
  匿名回落 'default' 的契约保持（never raises）。
- **中间件必须是纯 ASGI 透传**（不用 BaseHTTPMiddleware）：不缓冲
  响应体——SSE 流式推送零回归（sse-resilience.spec 是命门）；无
  Bearer 头直接 passthrough。
- **Key 明文只出现一次**（创建响应），落库仅 sha256 哈希 + 展示前缀；
  审计/日志/响应任何地方不得回显明文或哈希。
- **Key 不能管 Key**：key 管理端点仅认 cookie 身份，Bearer 调用一律
  403（防提权：泄漏的 key 不能自我解冻/扩权/铸新 key）。
- 护栏/限流/审计只作用于 Bearer 请求；违规 403 + 审计 denied，急停
  （frozen）即时生效。
- 既有测试一字不改全绿。

---

## 1. 数据模型（schema.sql，全新表无迁移）

- **api_keys**：`id TEXT PK, user_id TEXT NOT NULL, label TEXT NOT
  NULL, key_hash TEXT NOT NULL UNIQUE, prefix TEXT NOT NULL, created_at
  TEXT NOT NULL, last_used_at TEXT, frozen INTEGER NOT NULL DEFAULT 0,
  allowed_tickers TEXT, max_order_qty REAL, daily_trade_cap INTEGER`；
  索引 `(user_id)`。allowed_tickers 为 JSON 数组或 NULL（=不限）。
- **api_audit**：`id TEXT PK, key_id TEXT NOT NULL, user_id TEXT NOT
  NULL, method TEXT NOT NULL, endpoint TEXT NOT NULL, payload_digest
  TEXT, result TEXT NOT NULL`（ok|denied|error|rate_limited）`,
  status_code INTEGER, created_at TEXT NOT NULL`；索引
  `(key_id, created_at DESC)`。digest = 请求体紧凑 JSON 截 200 字符。

## 2. Bearer 认证（新 `app/api_gateway.py` + auth.py 增量）

- Key 形态：`fk_` + secrets.token_urlsafe(32)；prefix = 明文前 11 字符
  （`fk_XXXXXXXX`）供列表识别；存 sha256 hex。
- **ApiKeyGatewayMiddleware（纯 ASGI）**：仅拦 `Authorization:
  Bearer *`：哈希查 api_keys——未知 → 401 `{"error":"Invalid API
  key"}`（不入审计，仅 log）；frozen → 403 + 审计 denied；有效 →
  `scope`/request.state 注入 `api_user_id`/`api_key_id`/约束行，
  last_used_at 写库（每 key ≥60s 节流，进程内缓存）。
- **auth.py 增量（additive 三行）**：`get_current_user_id` 开头检查
  `request.state.api_user_id`，存在即返回（Bearer 优先于 cookie）；
  其余逐行不变。
- 工厂注入沿用：中间件在 main.py lifespan 后 `app.add_middleware`
  等价的纯 ASGI 包裹（`app.build_middleware_stack` 前注册），持
  db_path。

## 3. 限流（Bearer 全部请求）

- 令牌桶每 key：容量 10、补充 5/s，进程内 dict。超限 → 429
  `{"error":"Rate limited"}`；审计 rate_limited 行每 key 10s 节流
  （防审计表自爆）。

## 4. 授权护栏（Bearer 且命中 `POST /api/portfolio/trade`、
`POST /api/portfolio/orders`）

- 中间件读请求体（读后原样回放给下游）：JSON 不可解析 → 放行
  （路由自会 400）。可解析则依次：
  1. allowed_tickers 非空 且 body.ticker（大写归一）∉ 列表 → 403
     `{"error":"Ticker not allowed for this key"}`；
  2. max_order_qty 非空 且 body.quantity > 上限 → 403
     `{"error":"Quantity exceeds key limit"}`；
  3. daily_trade_cap 非空 且 当日（UTC）该 key 审计中两个下单端点
     result='ok' 行数 ≥ cap → 403 `{"error":"Daily trade cap
     reached"}`。
- 全部违规入审计 denied。急停 = PATCH frozen=1，即时 403（每请求
  读库）。

## 5. 审计台账

- Bearer 的**变更类**请求（POST/PATCH/DELETE 命中 /api/portfolio/*、
  /api/rules*、/api/watchlist*、/api/chat*、/api/strategies*、
  /api/backtest/runs*、/api/season/reset）响应后落一行：result 按
  status（2xx→ok，4xx/5xx→error；护栏拒绝→denied；限流→
  rate_limited）。GET 不审计（限流仍适用）。
- `GET /api/keys/{id}/audit?limit=&before=`（cookie 鉴权，仅本人 key）：
  limit 默认 50 夹 1..200，before=created_at 游标，倒序 →
  `{"entries":[...], "has_more": bool}`。

## 6. Key 管理 API（新 `app/routes/keys.py`；**仅 cookie 身份**，
Bearer 调用 → 403 `{"error":"Keys cannot manage keys"}`）

- `POST /api/keys` `{label 1..40, allowed_tickers?: [str], max_order_qty?
  >0, daily_trade_cap?: int ≥1}` → 201 `{"key": "<明文，仅此一次>",
  "info": {...}}`；每用户 ≤10 个 key（400）。
- `GET /api/keys` → `{"keys":[{id,label,prefix,created_at,last_used_at,
  frozen,allowed_tickers,max_order_qty,daily_trade_cap}]}`（无哈希）。
- `PATCH /api/keys/{id}` `{label?, frozen?, allowed_tickers?(显式 null
  清除), max_order_qty?, daily_trade_cap?}` → info。
- `DELETE /api/keys/{id}` → `{"status":"ok"}`（审计行保留）；跨用户
  一律 404。

## 7. OpenAPI / Swagger

- FastAPI 构造参数改：`openapi_url="/api/openapi.json"`、
  `docs_url="/api/docs"`、`redoc_url=None`（已核实无测试引用旧根
  路径；根 /docs 由静态导出接管不冲突）。
- SSE `/api/stream/prices` 保持无鉴权（带 Bearer 亦可，行为等价），
  文档注明。

## 8. 前端 /developers 页

- 导航增 开发者 `/developers`（nav-developers，i18n nav.developers）。
- 区块：
  1. **Key 列表** `dev-keys`，行 `dev-key-row-${id}`：label、prefix、
     created/last_used、frozen chip、约束摘要；`dev-key-freeze-${id}`
     即时切换、`dev-key-revoke-${id}` 二次确认、`dev-key-edit-${id}`
     展开约束编辑（tickers 逗号输入/max qty/日上限，null=不限）。
  2. **创建** `dev-key-create`（label + 可选约束）→ 成功后一次性
     明文 `dev-key-secret` + 复制按钮 `dev-key-copy` + "仅显示一次"
     警示；列表刷新。
  3. **审计** `dev-audit`：key 下拉 + 表格（时间/method+endpoint/
     result 色徽：ok=up 色、denied/error=down 色、rate_limited=amber/
     digest muted）+ `dev-audit-more` 翻页。
  4. **快速上手** `dev-quickstart`：curl 与 Python 片段（Bearer 头、
     origin 动态取 location.origin）、Swagger 链接（`/api/docs`）、
     examples/finally_bot.py 指引。
- Guest 亦可建 key（单用户模式照常）。全部 i18n `dev.*` en/zh；
  结果色徽走 terminal-up/down/amber 语义。

## 9. 示例机器人（新顶层 `examples/`）

- `examples/finally_bot.py`（~60 行，requests + stdlib）：env
  `FINALLY_URL`（默认 http://localhost:8000）/`FINALLY_API_KEY`/
  `BOT_TICKER`（默认 NVDA）/`BOT_QTY`（默认 2）；轮询
  /api/market/quotes 维护近 N 价，短均线(5)上穿长均线(20)买、下穿
  清仓（读 /api/portfolio/ 持仓）；403/429 打印并退避；纯函数
  `crossed(prices, fast, slow)` 可测。
- `examples/README.md`：教程——登录起名 → /developers 建 key（演示
  限定标的+日上限护栏）→ 跑 bot → /arena 看排名（人类 vs 聊天 AI
  vs 外部程序同台）。
- pytest 轻验证：import + crossed() 已知序列断言（不发网络）。

## 10. 测试

- 既有全套一字不改全绿（含 sse-resilience）。
- 新 pytest（约 +55）：key 创建（明文仅一次/哈希落库/prefix/≤10 上限）
  /CRUD/跨用户 404；Bearer 解析矩阵（valid→user_id、unknown→401、
  frozen→403、Bearer 优先 cookie、last_used 节流）；匿名与 cookie
  路径字节回归；令牌桶（突发 10/补充 5/s/429/审计节流 10s）；护栏
  三项与组合、daily cap 达上限拒绝与 UTC 午夜重置；审计矩阵
  （ok/denied/error/rate_limited、digest 截断、GET 不审计、明文与
  哈希零泄漏）；key 管理端点 Bearer 403；/api/openapi.json 200 +
  title、/openapi.json 404；audit 分页端点；bot crossed()。
- 新 jest（约 +25）：/developers 四区块、一次性 secret 展示、freeze/
  revoke 二次确认、约束编辑 null 语义、审计色徽与翻页、nav、
  dev.* 键集 en/zh 对齐。
- 新 E2E `developers.spec.ts`（US ~5 条）：UI 建 key → 列表可见 →
  Playwright request 带 Bearer 下单成功 → 审计 ok 行出现在 UI；
  编辑 max_order_qty 后超量下单 → 403 → 审计 denied 行；freeze →
  即时 403；revoke → 401；GET /api/docs 200。
  `developers-cn.spec.ts`（~2 条：中文区块文案 + 建 key 流程；文件
  名命中 CN testMatch）；cn compose 命令追加。
- 双市场：护栏在中间件层与市场无关；CN 容器 key 独立（8801 自己的
  DB）——cn E2E 建 key 即证。

## 11. workflow 分工（实现→对抗验证→修复）

- **B 后端 agent**（只碰 backend/）：§1-§7 + pytest。
- **F 前端 agent**（并行，只碰 frontend/）：§8 + jest。
- **X 示例 agent**（并行，只碰 examples/ 新目录）：§9 + 对应轻
  pytest 文件（backend/tests/test_example_bot.py 允许，唯一例外）。
- **E2E agent**（并行，只碰 test/）：§10 两 spec + cn compose 增量。
- **对抗验证**：套件门槛 + 四路（后端契约与安全——重点：明文/哈希
  泄漏面、提权面、中间件对 SSE 与无头请求零影响；前端；CN 一致性；
  E2E 强度）→ 修复循环 ≤3 轮。
