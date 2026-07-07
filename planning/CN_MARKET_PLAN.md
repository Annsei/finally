# FinAlly-CN — 中文 A 股版本方案

状态：待用户确认后执行。约束：**不覆盖现有文件** —— 美股版行为逐字节不变，
现有 623 pytest / 176 jest / 11 E2E 全部保持通过是每个阶段的硬门槛。

---

## 0. 架构决策：市场配置层（Market Profile），不是复刻一份代码

两条路线的取舍：

| 路线 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| A. 完全复刻 | 拷贝整个项目到 `finally-cn/` | 字面意义上零触碰现有文件 | 代码翻倍；M1-M5 的每个 bug 修复、每个新功能都要维护两份；两周后必然漂移 |
| **B. 市场配置层（推荐）** | 新增 `MarketProfile` 抽象；`FINALLY_MARKET=cn` 环境变量激活；默认 `us` 一切照旧 | 整个 M1-M5 功能栈（高级订单/规则引擎/AI 代理/回测/多用户/赛季）A 股版**免费获得**；一个镜像跑两个市场 | 约 10 个现有文件需要"增量挂钩"式修改（见 §6 清单） |

对"不覆盖现有文件"的解读（需用户确认）：**新机制全部放在新文件里；
现有文件只做增量注入点**（注册 profile、把硬编码常量改为从 profile 读取、
颜色 hex 改为 CSS 变量），每处都以 `us` 为默认值 —— 不删除、不替换任何
现有行为。若用户坚持字面意义的"一个现有文件都不动"，则只能走路线 A
（建议开新分支 `finally-cn` 或新目录，接受双维护成本）。

**运行形态**：同一镜像，`FINALLY_MARKET=cn` 决定后端市场；前端**运行时**
从新端点 `GET /api/market/profile` 拿到市场配置（币种/手/涨跌停/语言），
一份静态构建同时服务两个市场。部署时两容器并行：

```bash
docker run -d --name finally-app    -v finally-data:/app/db    -p 8800:8000 --env-file .env finally                      # 美股版（不变）
docker run -d --name finally-app-cn -v finally-data-cn:/app/db -p 8801:8000 --env-file .env -e FINALLY_MARKET=cn finally  # A股版
```

数据卷独立（`finally-data-cn`），两版数据互不污染。

---

## 1. 后端：市场配置层（新文件 `backend/app/market/profiles.py`）

```python
@dataclass(frozen=True)
class MarketProfile:
    key: str                    # "us" | "cn"
    currency_symbol: str        # "$" | "¥"
    locale: str                 # "en-US" | "zh-CN"
    lot_size: int               # 1 | 100（整手买入）
    t_plus: int                 # 0 | 1（T+1 锁仓）
    stamp_tax_bps_sell: float   # 0 | 5（印花税 0.05%，仅卖出）
    min_commission: float       # 0 | 5.0（佣金最低 ¥5）
    commission_bps: float       # 0 | 2.5（万2.5，双边）
    midday_break: bool          # False | True（午间休市）
    price_limit_pct(ticker) -> float | None   # None | 10/20/5 按板块
    up_is_red: bool             # False | True（红涨绿跌，透传给前端）
```

- `us` profile = 现状的全部默认值（费用沿用 FINALLY_COMMISSION_BPS）。
- 选择逻辑照抄 commission 模式：main.py 启动时读一次 `FINALLY_MARKET`，
  注入所有 router/后台任务，helper 永不自己读环境变量。
- 新端点 `GET /api/market/profile`（新文件 routes/profile.py 或并入
  routes/market.py 的增量路由）——前端运行时配置的唯一来源。

## 2. A 股标的宇宙（新文件 `backend/app/market/seed_prices_cn.py`）

约 14 个标的，覆盖板块与涨跌停差异（价格为拟真种子价）：

| 代码 | 名称 | 板块 | 种子价 | 涨跌停 | GBM σ |
|---|---|---|---|---|---|
| 600519 | 贵州茅台 | 白酒 | ¥1700 | ±10% | 0.22 |
| 000858 | 五粮液 | 白酒 | ¥140 | ±10% | 0.28 |
| 300750 | 宁德时代 | 新能源 | ¥180 | ±20% | 0.35 |
| 002594 | 比亚迪 | 新能源 | ¥250 | ±10% | 0.32 |
| 601012 | 隆基绿能 | 新能源 | ¥18 | ±10% | 0.38 |
| 688981 | 中芯国际 | 半导体 | ¥45 | ±20% | 0.42 |
| 300059 | 东方财富 | 券商 | ¥15 | ±20% | 0.40 |
| 601318 | 中国平安 | 金融 | ¥45 | ±10% | 0.20 |
| 600036 | 招商银行 | 金融 | ¥35 | ±10% | 0.18 |
| 601988 | 中国银行 | 金融 | ¥4.5 | ±10% | 0.12 |
| 600900 | 长江电力 | 公用 | ¥28 | ±10% | 0.14 |
| 601899 | 紫金矿业 | 有色 | ¥17 | ±10% | 0.30 |
| 000333 | 美的集团 | 家电 | ¥75 | ±10% | 0.24 |
| 600276 | 恒瑞医药 | 医药 | ¥45 | ±10% | 0.28 |

- 相关性分组：白酒 0.7 / 新能源 0.6 / 金融 0.5（复用 Cholesky 机制）。
- 事件叙事、板块联动 burst 直接继承 M3.2 —— 中文提示词见 §4。
- 初始资金：¥100,000（$10k 在 A 股买不起一手茅台）。种子现金按
  profile 注入 db seed（新用户/新赛季一致）。

## 3. A 股交易机制（每条 = profile 挂钩 + 新逻辑文件/函数 + 测试）

1. **T+1 锁仓**（核心差异，M 工作量）
   - positions 表增量迁移一列 `t1_locked`（当日买入锁定量，us 版恒 0）。
   - 买入时 `t1_locked += qty`；卖出校验 `qty <= quantity − t1_locked`，
     超出返回 400「T+1：今日买入的股份明日方可卖出」。
   - 换日解锁挂在**现有** settlement 的 roll_session_open 钩子上（收盘
     结算已有基础设施，加一条 `UPDATE positions SET t1_locked = 0`）。
   - 规则引擎/AI 卖出/回测引擎同样走 execute_trade_on_conn，天然生效。
2. **涨跌停板**（M）
   - PriceUpdate 增字段 `limit_up` / `limit_down`（us 版为 null）。
   - 模拟器 tick 后按 prev_close 钳制：触板后价格冻结在板上（封板），
     每 tick 小概率（~2%）开板回落 —— 戏剧性与真实感兼得。
   - 触板时事件流发「涨停/跌停」事件；限价单在板价之外直接 400。
3. **整手交易**（S）：买入数量必须是 lot_size 整数倍（400「A股买入须为
   100 股整数倍」）；卖出允许零股。TradeBar 数量框以「手」为单位输入
   （见 §5）。回测引擎的 quantity 同样校验。
4. **费用模型**（S）：`fees = max(¥5, 成交额×万2.5)（双边） + 成交额×0.05%
   （仅卖出，印花税）`。扩展现有 commission 管道（trade 行已有
   commission 列，语义不变），Fills 页签中文列名「手续费」。回测引擎
   同参数，保证回测与实盘口径一致。
5. **交易时段**（S）：沿用加速时钟（演示友好），CN profile 把一个
   session 切成 上午盘 → 午间休市（break）→ 下午盘；StatusBar 徽章
   显示「上午盘/午间休市/下午盘/已收盘」。真实日历（9:30-11:30 /
   13:00-15:00 Asia/Shanghai）做成 env 可选（`FINALLY_SESSION_REAL=true`），
   默认不开（否则半夜演示永远闭市）。
6. **做空**：A 股散户无融券 —— 平台本就 long-only，天然一致，无需改动。

## 4. AI 中文化（S-M）

- chat/briefs/reviews/narratives 四组系统提示词按 profile 切换：CN 版
  全部中文输出，术语对齐（手/涨停/跌停/T+1/印花税），并注入 A 股约束
  （「买入必须整手」「今日买入不可卖出」——否则 AI 会生成非法交易）。
- 结构化输出 schema 不变（trades/orders/rules/backtests 字段照旧），
  只有 message 与叙事语言变化 —— 前端零适配。
- LLM_MOCK 中文分支：deterministic 中文 mock（E2E 用）。

## 5. 前端：运行时市场适配（不 fork 组件）

1. **运行时配置**（新文件 `src/lib/marketProfile.ts` + SWR）：启动即拉
   `/api/market/profile`，挂 `<html data-market="cn">`。
2. **红涨绿跌**（关键，M）：现有涨跌色一半是 Tailwind token
   （terminal-up/down）一半是内联 hex（#22c55e/#ef4444）。做法：
   - tailwind token 改指 CSS 变量（`--c-up/--c-down`，默认值 = 现值）；
   - `[data-market="cn"]` 交换两个变量；
   - 内联 hex 逐个替换为 `var(--c-up)` 形式（约 8 个组件的增量小改，
     us 视觉逐像素不变 —— jest 快照/断言保持通过验证这一点）。
   - 注意只翻转「方向色」；连接状态点（绿=连接）不翻。
3. **中文界面**（M-L）：新文件 `src/lib/i18n.ts` —— 轻量字典
   `t('key')`，locale 来自 profile，en 字典 = 现有文案原文（保证 us
   版渲染结果不变）。覆盖约 20 个组件的静态文案（表头/按钮/占位符/
   空状态/toast）。**测试 testid 契约不动**，现有 E2E 不受影响。
4. **数量单位「手」**（S）：TradeBar CN 模式数量框标签「手」，提交时
   ×100；持仓/成交表显示「N手(零股M)」；Max buy/Held 快捷键按手取整。
5. **格式化**（S）：`¥` 符号、万/亿大数（成交量 3.5万手）、
   「600519 贵州茅台」双行显示（自选列表代码+名称）。
6. **涨跌停标识**（S）：watchlist 行与主图触板时显示「涨停」「跌停」
   徽章（红/绿按 CN 语义）。

## 6. 现有文件"增量挂钩"清单（预计 ~10 个，全部 us 默认不变）

后端：main.py（读 env + 注入 profile）、portfolio.py / orders.py /
rules.py 的 execute/place 校验点（lot/T+1/费用/板价，`profile.us` 时
短路走原路径）、simulator.py（钳制钩子）、models.py（PriceUpdate 两个
可空新字段）、settlement.py（T+1 解锁一行）、chat.py + briefs.py
（提示词按 profile 选择）、db/schema（一列增量迁移）。
前端：tailwind.config + globals（CSS 变量）、~8 个组件的 hex→var 替换
与 t() 替换、TradeBar（手模式）、WatchlistRow（名称行）。
**全部新机制逻辑体**（profile 定义、CN 宇宙、T+1/涨跌停/费用函数、
i18n 字典、格式化工具）在新文件中。

## 7. 测试与交付门槛

- 硬门槛：现有全部测试在 us 默认下**不改一字**通过（回归保护）。
- 新 pytest（预计 +60~80）：profile 选择、T+1 锁定/换日解锁/AI 卖出
  被拒、整手校验、涨跌停钳制+封板+开板、费用（最低佣金/印花税单边）、
  CN 中文 mock 管线。
- 新 jest（预计 +15~25）：颜色变量翻转、手/¥/万亿格式化、i18n 渲染、
  TradeBar 手模式提交 ×100。
- 新 E2E：`docker-compose.cn.test.yml`（FINALLY_MARKET=cn + LLM_MOCK）
  + `cn.spec.ts`：中文界面出现、红涨绿跌、整手拒绝、T+1 拒绝。US E2E
  原样跑，双 compose 都过才算完成。
- 交付：8800 美股版照旧 + 8801 A 股版并行，浏览器截图验收。

## 8. 明确不做（本期）

- 真实 A 股行情源（默认模拟器；AkShare/Tushare 记为后续可选 —— 现有
  MASSIVE_API_KEY 不覆盖 A 股）
- 集合竞价撮合（开盘价 = 首 tick 简化）、融资融券、打新、可转债、
  科创板开户门槛、北交所
- 上证指数合成（可作 stretch：等权合成指数显示在 Header）

## 9. 执行拆分（沿用里程碑模式：契约 → 后端代理 + 前端并行 → 验证）

| 阶段 | 内容 | 工作量 |
|---|---|---|
| CN-1 | profile 层 + CN 宇宙 + /api/market/profile + 种子资金 ¥100k | M |
| CN-2 | 交易机制：T+1、整手、涨跌停、费用、午休时段（含回测口径） | L |
| CN-3 | 前端：红涨绿跌 CSS 变量、i18n 中文化、手/¥ 格式、AI 中文提示词 | L |
| CN-4 | CN E2E compose + 双版本回归 + 8801 部署 + 文档 | M |

预计整体 ≈ M3+M4 之和；每阶段独立可交付，随时可停。
