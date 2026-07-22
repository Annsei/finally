# CN-1 — Market Profile 层契约（固定，CN-2/3 在此之上构建）

原则（路线 B，用户已确认）：新机制全部在**新文件**；现有文件只做
增量挂钩，每个新参数都有 us 默认值 —— **现有 623 pytest / 176 jest 在
默认 us 下一字不改全部通过**是硬门槛。CN-1 不改交易机制（T+1/整手/
涨跌停/费用 = CN-2）、不改提示词语言（= CN-3）。

## 1. 新文件 `backend/app/market/universe.py`

```python
@dataclass(frozen=True)
class MarketUniverse:
    seed_prices: dict[str, float]
    ticker_params: dict[str, dict[str, float]]   # {"sigma","mu"}
    default_params: dict[str, float]
    default_watchlist: list[str]
    sectors: dict[str, str]
    names: dict[str, str]            # 显示名（us 为 {}）
    crypto_tickers: frozenset[str]   # us={"BTC","ETH"}, cn=frozenset()
    def pairwise_correlation(self, t1: str, t2: str) -> float
    def sector_for(self, ticker) -> str      # 未知 -> "other"
    def asset_class_for(self, ticker) -> str # crypto_tickers 之外 -> "equity"
US_UNIVERSE: MarketUniverse   # 从现有 seed_prices.py 常量构造，行为恒等
```

## 2. 新文件 `backend/app/market/seed_prices_cn.py` → `CN_UNIVERSE`

按 CN_MARKET_PLAN.md §2 的 14 只标的（代码/名称/板块/种子价/σ，
mu 取 0.03-0.08 合理值）。板块分组相关性：白酒 0.7、新能源 0.6、
金融 0.5、跨组 0.3（cn 无特殊独立股）。`CN_BOARD_LIMIT_PCT`：
30xxxx/68xxxx → 20.0，其余 → 10.0（导出函数 `cn_price_limit_pct(ticker)
-> float`，未知代码回退 10.0）。default_watchlist = 全部 14 只。

## 3. 新文件 `backend/app/market/profiles.py`

```python
@dataclass(frozen=True)
class MarketProfile:
    key: str                       # "us" | "cn"
    currency_symbol: str           # "$" | "¥"
    locale: str                    # "en-US" | "zh-CN"
    lot_size: int                  # 1 | 100
    t_plus: int                    # 0 | 1
    stamp_tax_bps_sell: float      # 0.0 | 5.0
    min_commission: float          # 0.0 | 5.0
    default_commission_bps: float  # 0.0 | 2.5
    midday_break: bool             # False | True
    up_is_red: bool                # False | True
    seed_cash: float               # 10_000.0 | 100_000.0
    universe: MarketUniverse
    def price_limit_pct(self, ticker) -> float | None  # us 恒 None；cn 按板块

US_PROFILE / CN_PROFILE
resolve_market_profile() -> MarketProfile
    # 读 FINALLY_MARKET（大小写不敏感；空/缺省/非法 -> us，非法值 warn 一次）
    # 只在 main.py 启动时调用一次（commission 模式），helper 不读 env
```

CN-1 中 stamp_tax/min_commission/default_commission_bps/midday_break/
t_plus/lot_size 仅作为**数据**存在并经端点透出，不改变任何执行路径
（CN-2 启用）。

## 4. 增量挂钩（现有文件，全部带 us 默认值）

- `simulator.py`：`GBMSimulator(..., universe: MarketUniverse | None=None)`
  与 `SimulatorDataSource(..., universe=None)` — None 时走现有模块常量
  路径（逐字节不变）；提供时 seed price / params / correlation /
  asset_class（闭市冻结判断）取自 universe。
- `interface.py` 的 `create_market_data_source(...)`：透传 universe
  （可选参数，默认 None）。
- `db` 初始化：`init_db(db_path, *, seed_cash: float = 10_000.0,
  default_watchlist: list[str] | None = None)` — None 时用现有
  DEFAULT_WATCHLIST。注意：已存在的 DB 不重播种（现状语义不变）。
- `main.py`：启动时 resolve；存 `app.state.market_profile`；把
  seed_cash/default_watchlist 传给 init_db、universe 传给数据源、
  profile 传给 seasons/leaderboard/backtest 工厂与 profile 路由。
- `seasons.py`：`create_seasons_router(..., seed_cash: float = 10_000.0)`
  — 赛季重置的 ¥ 归位金额。
- `leaderboard.py`：若 return% 基线硬编码 10000 → 注入
  `seed_cash: float = 10_000.0`（若按赛季首快照计算则无需改动——以
  代码实际情况为准，报告结论）。
- `backtest.py`/`routes/backtest.py`：`normalize_backtest_config(...,
  universe: MarketUniverse | None = None)`（anchor 回退与 params 查找
  用 universe；None = 现有 US 常量），`run_backtest(...,
  starting_cash: float = 10_000.0)`；路由工厂接收 profile 并传入两者。
  响应 stats 数学不变（return% 相对 starting_cash）。

## 5. 新路由 `backend/app/routes/profile.py` — `GET /api/market/profile`

工厂 `create_profile_router(profile)`，响应（前端 CN-3 的运行时契约）：

```json
{
  "market": "cn", "currency_symbol": "¥", "locale": "zh-CN",
  "lot_size": 100, "t_plus": 1, "up_is_red": true,
  "seed_cash": 100000.0, "midday_break": true,
  "names": {"600519": "贵州茅台"},
  "price_limit_pct": {"600519": 10.0, "300750": 20.0}
}
```

us 版：names={}、price_limit_pct={}、up_is_red=false、lot_size=1、
t_plus=0、seed_cash=10000.0。字段一次定全，CN-3 前端只消费不追加。

## 6. 测试（新增，预计 +30 左右）

- resolve：缺省/us/CN/Cn/非法 → 正确 profile；非法 warn。
- CN_UNIVERSE 完整性：14 只、全部有 params/sector/name、板块限幅
  正确（300750/688981/300059 → 20，600519 → 10）。
- 模拟器注入 universe：CN 票价从 CN 种子起步、相关性走 universe、
  crypto 集合为空时闭市全冻结。
- init_db(seed_cash=100000, default_watchlist=CN)：users_profile 现金
  与 watchlist 正确。
- 端点：us 与 cn 两种响应 shape/关键字段。
- 回测：universe 注入后 CN 票可回测（anchor 从 CN 种子取），
  starting_cash=100000 时 return% 口径正确。
- 回归：整个现有套件在默认 us 下通过（不许改任何现有断言）。
