# FinAlly V2 — 多页面架构 · 策略中心 · 量化接口 方案

状态：已确认，按 P1→P2→P3→P4 推进。P1 已完成（2026-07-08，契约
planning/P1_PAGES_CONTRACT.md）。P2 已完成（2026-07-08，契约
planning/P2_STRATEGY_CONTRACT.md，含实施偏离备案；双市场全套件
回归通过）。针对三个问题：①页面过于扁平化（单页塞下一切，无可
点击跳转）；②交易策略缺乏体系（规则+回测是零散功能，不是一等公民）；
③未来量化交易接入没有接口。

参照项目的可借鉴点：
- **Vibe-Trading (HKUDS)**：Run Library/Run Detail（每次回测是持久化、可
  回看、可对比的"研究产物"）；假设→信号→回测→部署的研究闭环；纸面
  交易连接器 + **授权护栏**（限定标的/单笔上限/日上限 + 审计台账 + 急停
  开关）；Settings/Swarm 等独立页面的导航结构。
- **tickflow-stock-panel**：选股/回测/监控/复盘的多页工作台；声明式
  条件信号（字段-算子-值 + AND/OR）；指标流水线；市场情绪总览页。

不变的工程底线：单容器单端口、Next.js 静态导出、SQLite、双市场
（us/cn profile）全部新页面从第一天就走 i18n + 方向色变量；现有测试
一字不改全部通过。

---

## 一、信息架构：从单页终端到多页工作台（P1）

顶部全局导航（Header 增导航区），**AI 聊天从"仪表盘的侧栏"升级为跨
页面常驻的全局停靠面板**（布局组件下沉到 _app 级）：

```
交易台 /            现有终端（自选/主图/下单/持仓页签）——保持不动，仍是首页
市场   /market      全市场页：全部标的行情网格、板块热力图、情绪指数、
                    事件/新闻历史归档（现在新闻滚过就没了）
个股   /symbol?c=X  个股详情页：大图表+多周期、当日统计（高低/量/振幅/
                    涨跌停价）、我在该票的持仓与全部成交、该票事件史、
                    「AI 分析这只票」按钮。全站所有代码点击可跳转
策略   /strategies  策略中心（见二）：策略库列表 + /strategy?id=X 详情
回测库 /runs        Run Library（学 Vibe）：历次回测持久化、可筛选、
                    可对比；/run?id=X 详情页复现完整结果
复盘   /journal     交易日志：AI 每日复盘归档、成交日历（P&L calendar）、
                    按日/按标的浏览历史
竞技场 /arena       排行榜 + 赛季史 + 选手公开主页（点排行榜名字可跳）
开发者 /developers  量化接口（见三）：API Key 管理、接口文档、示例代码
```

技术要点：
- 静态导出下动态路由用 **query 参数**（`/symbol?c=600519`），不用
  `[code]` 目录（getStaticPaths 无法枚举用户自加标的）。
- SSE 订阅、priceStore、profile 等全局状态已在 _app 层可复用；导航切换
  不断流（Next 客户端路由）。
- 交易台现有 testid 契约不动——E2E 全部照跑；新页面新增自己的契约。

## 二、策略中心：把"规则+回测"升级为一等公民（P2）

现状：rules 是一次性触发器、回测是无状态一次性计算、两者互不认识。
目标：**策略 = 有生命周期的实体**，闭环对齐 Vibe 的研究流水线：

```
创建(手动/AI) → 回测验证(留档) → 部署实盘 → 绩效归因 → 复盘/迭代
   draft          backtested        live         (每步可回退/暂停)
```

1. **strategies 表**：id/user_id/name/status(draft|live|paused|archived)/
   entry(JSON)/exits(JSON)/sizing(JSON)/created_at/deployed_at。
   - entry：声明式条件组（学 tickflow）：`{all|any: [{field, op, value}]}`，
     field 扩展到指标（见 3）；
   - exits：take_profit_pct / stop_loss_pct / trailing_stop_pct / max_holding_days；
   - sizing：fixed_qty | cash_pct（占可用资金比例）。
2. **策略引擎**：现有 rules 评估循环泛化——live 策略按 entry 评估开仓、
   按 exits 管理持仓（服务端持续跟踪，替代目前"一次性 fire"）；每笔
   成交打 strategy_id（trades 增列）→ **按策略归因绩效**（每个策略自己
   的权益曲线、胜率、回撤，直接复用 M5 统计口径）。
3. **指标触发器**：新增指标模块（从 PriceCache 分钟/会话 bar 计算
   MA/EMA/RSI/最高最低 N 日），rules/策略条件字段扩展如
   `ma_cross_above(5,20)`、`rsi_below(30)`。坚持**声明式 JSON、绝不让
   AI 生成可执行代码**（tickflow 的 AST 白名单方案是 RCE 面，不学）。
4. **回测持久化（Run Library，学 Vibe）**：backtest_runs 表存 config+
   stats+曲线（压缩 JSON）；回测页"保存本次结果"；策略详情页列出它的
   全部回测记录，支持两次 run 并排对比；「部署」按钮要求至少一次回测
   （软门槛，提示但不强制——课程叙事：先验证再上线）。
5. **AI 集成**：chat 结构化输出增 `strategies` 动作（创建/回测/部署/
   暂停）；一句话「做一个 NVDA 均线金叉策略，先回测 60 天，好就上线」
   → 创建 draft → 自动回测 → 附带统计徽章 → 用户确认部署。
6. **策略模板库**：内置 5-6 个模板（抄底、动量突破、均线金叉、网格、
   RSI 超卖反弹），一键实例化改参——新用户的起点。

## 三、量化接口：FinAlly 作为"纸面券商"开放 API（P3）

反转 Vibe 的视角：Vibe 连接真实券商做纸面交易；**FinAlly 本身就是模拟
交易所**——把它开放成任何外部程序（学生自己写的 Python 机器人、甚至
Vibe-Trading 这类 agent）都能接入的 paper broker。

1. **API Key 认证**：api_keys 表（key hash/user_id/label/created/
   last_used）；`Authorization: Bearer <key>` 作为 cookie 之外的第二认证
   路径（get_current_user_id 增量分支）；/developers 页面创建/吊销 Key。
2. **接口面**：现有 REST 已够用（行情/下单/委托/规则/回测/组合），补：
   - `GET /api/openapi.json` + FastAPI 自带 Swagger 暴露并在 /developers
     内嵌文档；
   - SSE 行情流对 Bearer 开放；
   - 简单限流（每 key 每秒 N 请求，内存桶）。
3. **授权护栏（学 Vibe 的 mandate-gating，简化版）**：每个 API Key 可
   配置约束——允许标的列表、单笔最大数量、日成交上限；越界返回 403 +
   记入审计表。一键「冻结此 Key」急停。虽是模拟盘，这是课程里讲
   agent 安全的最佳素材。
4. **审计台账**：api_audit 表记录每笔 API 触发的动作（key/endpoint/
   payload 摘要/结果），/developers 页可查。
5. **Python SDK 示例**：`examples/finally_bot.py`——50 行的动量机器人
   （轮询行情→均线判断→下单），README 教程；机器人以自己的用户身份
   接入 → **直接上竞技场排行榜**，和人类、和 AI 助手同台（M4 arena 的
   完整闭环：手动交易者 vs 聊天 AI vs 外部量化程序）。
6. 双市场天然支持：Key 属于用户，用户在哪个市场容器注册就交易哪个市场
  （8800 美股 / 8801 A 股各自独立）。

## 四、锦上添花（P4，可裁剪）

- **市场情绪指数**（tickflow 六轴的简化三轴：涨跌家数比/平均波动/量
  能）：/market 页仪表盘 + AI 简报引用；
- **相关性热力图**（Vibe 的 Correlation Dashboard）：/market 页，从
  快照收益率算滚动相关性，教学价值高（板块相关性本来就是模拟器内置的，
  可视化它很酷）；
- **成交日历**：/journal 页按日 P&L 上色的月历；
- 竞技场选手公开主页：权益曲线+持仓概要（隐私开关）。

## 五、阶段划分与工作量

| 阶段 | 内容 | 工作量 | 依赖 |
|---|---|---|---|
| P1 | 多页面骨架：全局导航+常驻聊天、市场页、个股页、复盘页、竞技场页（重组现有数据 + 少量新端点） | L | — |
| P2 | 策略中心：策略实体+引擎+指标触发+回测持久化(Run Library)+AI 动作+模板 | XL | P1（页面挂载点） |
| P3 | 量化接口：API Key/Bearer、护栏、审计、文档页、示例机器人上榜 | M-L | P1（/developers 页） |
| P4 | 情绪指数、相关性热力图、成交日历、选手主页 | M | P1 |

建议顺序 **P1 → P2 → P3 → P4**：P1 立刻解决"扁平化"观感问题且为
后两者提供挂载点；P2 是产品深度（策略是灵魂）；P3 是课程叙事的高潮
（自己写 bot 接入自己的交易所打排位）。每阶段独立可交付，沿用
契约先行 → workflow（实现→对抗验证→修复）→ 双市场回归 的流程。

## 六、明确不做

- AI 生成可执行 Python 策略（安全底线，声明式 JSON 到底）
- WebSocket（SSE 够用）、真实券商对接、期权/期货回测引擎
- Vibe 的 Alpha 因子库/多智能体 swarm（GBM 模拟盘上因子 IC 无意义）
- 多容器架构（保持单容器；页面全部静态导出）
