/**
 * i18n.ts — lightweight runtime translation (FinAlly-CN, CN-3 §3)
 *
 * Two dictionaries keyed by language. The `en` values are the VERBATIM current
 * hardcoded English strings — byte-for-byte, including punctuation and case —
 * so that when the profile is US (or still loading), every component renders
 * exactly as before and the existing jest assertions keep passing.
 *
 * `zh` values render only when the resolved profile locale is Chinese
 * (locale === 'zh-CN'), i.e. on the A-share market.
 *
 * Dynamic copy uses {param} placeholders filled via the second argument.
 */
import { useMarketProfile } from '@/lib/marketProfile';

export type Lang = 'en' | 'zh';

export function langFromLocale(locale: string | undefined | null): Lang {
  return typeof locale === 'string' && locale.toLowerCase().startsWith('zh') ? 'zh' : 'en';
}

type Dict = Record<string, string>;

// ---------------------------------------------------------------------------
// English dictionary — MUST match the current source strings exactly.
// ---------------------------------------------------------------------------
const en: Dict = {
  // Header
  'header.cash': 'Cash',
  'header.realized': 'Realized',
  'header.dayPnl': 'Day P&L',
  'header.portfolio': 'Portfolio',
  'header.signOut': 'Sign out',
  'header.guestSignIn': 'Guest · Sign in',
  'header.go': 'Go',
  'header.traderNamePlaceholder': 'Trader name…',
  'header.traderNameAria': 'Trader name',
  'header.signInFailed': 'Sign-in failed',
  'header.signInFailedStatus': 'Sign-in failed ({status})',

  // TradeBar
  'tradebar.type': 'Type',
  'tradebar.ticker': 'Ticker',
  'tradebar.qty': 'Qty',
  'tradebar.qtyLots': 'Lots',
  'tradebar.stopLabel': 'Stop {sym}',
  'tradebar.stopAria': 'Stop price',
  'tradebar.limitLabel': 'Limit {sym}',
  'tradebar.limitAria': 'Limit price',
  'tradebar.tif': 'TIF',
  'tradebar.buy': 'Buy',
  'tradebar.sell': 'Sell',
  'tradebar.est': 'Est.',
  'tradebar.bid': 'Bid',
  'tradebar.ask': 'Ask',
  'tradebar.maxBuy': 'Max buy',
  'tradebar.held': 'Held',
  'tradebar.errTickerQty': 'Enter a valid ticker and quantity.',
  'tradebar.errLimit': 'Enter a valid limit price.',
  'tradebar.errStop': 'Enter a valid stop price.',
  'tradebar.errOrderFailed': 'Order failed',
  'tradebar.errTradeFailed': 'Trade failed',
  'tradebar.concentration': '⚠ A buy this size would make {ticker} ~{pct}% of your portfolio.',

  // Fills / order confirmations (shared by TradeBar toasts and ChatPanel badges)
  'fill.bought': 'Bought {qty} {ticker} @ {price}',
  'fill.sold': 'Sold {qty} {ticker} @ {price}',
  'fill.stopPlaced': 'Stop placed: {verb} {qty} {ticker} @ stop {stop}',
  'fill.stopLimitPlaced': 'Stop-limit placed: {verb} {qty} {ticker} @ stop {stop} / {cmp}{limit}',
  'fill.orderPlaced': 'Order placed: {verb} {qty} {ticker} @ {cmp}{limit}',

  // ChatPanel
  'chat.title': 'FinAlly AI',
  'chat.review': 'Review',
  'chat.reviewTitle': 'Ask FinAlly for a daily portfolio review',
  'chat.toggle': 'Toggle chat panel',
  'chat.empty': 'Ask FinAlly to analyze your portfolio, suggest trades, or manage your watchlist.',
  'chat.thinking': 'Thinking…',
  'chat.placeholder': 'Ask FinAlly about your portfolio…',
  'chat.send': 'Send',
  'chat.errGeneric': 'Something went wrong. Please try again.',
  'chat.collapse': 'Collapse',
  'chat.showFull': 'Show full brief',
  'chat.kind.brief': 'Market Brief',
  'chat.kind.review': 'Daily Review',
  'chat.kind.rule': 'Rule',

  // ChatPanel action badges
  'badge.stopWord': 'stop',
  'badge.win': 'win',
  'badge.tradeFailed': 'Trade failed: {ticker} — {error}',
  'badge.orderFailed': 'Order failed: {ticker} — {error}',
  'badge.orderPlaced': 'Order placed: {verb} {qty} {ticker} @ {detail}',
  'badge.ruleFailed': 'Rule failed: {ticker} {error}',
  'badge.ruleArmed': 'Rule armed: {desc}',
  'badge.backtestFailed': 'Backtest failed: {ticker} — {error}',
  'badge.backtest': 'Backtest {ticker}: {ret} (B&H {bh}) · {rt} trades{win}',
  'badge.watchlistFailed': 'Watchlist change failed: {ticker} — {error}',
  'badge.added': 'Added {ticker}',
  'badge.removed': 'Removed {ticker}',
  'badge.rejected': 'rejected',

  // WatchlistPanel
  'watchlist.addPlaceholder': 'Add ticker…',
  'watchlist.add': 'Add',
  'watchlist.addAria': 'Add ticker',
  'watchlist.errFormat': 'Ticker must be 1-10 letters (A-Z).',
  'watchlist.errAlready': '{ticker} is already in the watchlist.',
  'watchlist.errAddFail': 'Failed to add ticker.',
  'watchlist.errRemoveFail': 'Failed to remove ticker.',
  'watchlist.noPrices': 'No prices yet',
  'watchlist.waitingFeed': 'Waiting for the live market feed…',
  'watchlist.colSymbol': 'Symbol',
  'watchlist.colPrice': 'Price',
  'watchlist.colDayPct': 'Day %',
  'watchlist.colChart': 'Chart',

  // StatusBar
  'status.sim247': 'SIM 24/7',
  'status.open': 'OPEN',
  'status.closed': 'CLOSED',
  'status.closesIn': 'closes in {t}',
  'status.opensIn': 'opens in {t}',
  'status.feedNone': 'Feed: —',
  'status.feed': 'Feed: {age} ago',
  'status.shortcuts': 'Shortcuts:',
  'status.scSearch': 'search',
  'status.scSelect': 'select',
  'status.scTrade': 'trade',

  // NewsTicker
  'news.empty': 'Market events appear here — watching for unusual moves…',

  // PnL chart
  'pnl.title': 'Portfolio P&L',
  'pnl.empty': 'No portfolio history yet.',

  // Positions table
  'positions.colTicker': 'Ticker',
  'positions.colQty': 'Qty',
  'positions.colAvgCost': 'Avg Cost',
  'positions.colPrice': 'Price',
  'positions.colPnl': 'P&L',
  'positions.colChange': 'Change %',
  'positions.empty': 'No positions yet. Use the trade bar to buy shares.',

  // Fills (trade blotter)
  'fills.colTime': 'Time',
  'fills.colSide': 'Side',
  'fills.colTicker': 'Ticker',
  'fills.colQty': 'Qty',
  'fills.colPrice': 'Price',
  'fills.colValue': 'Value',
  'fills.colFee': 'Fee',
  'fills.colRealized': 'Realized',
  'fills.empty': 'No trades yet. Fills appear here the moment they execute.',

  // Open orders
  'orders.colTime': 'Time',
  'orders.colSide': 'Side',
  'orders.colTicker': 'Ticker',
  'orders.colQty': 'Qty',
  'orders.colKind': 'Kind',
  'orders.colLimit': 'Limit',
  'orders.colStop': 'Stop',
  'orders.empty':
    'No open orders. Place a limit order from the trade bar — it rests here until the price crosses your limit.',

  // Rules
  'rules.colRule': 'Rule',
  'rules.colCondition': 'Condition',
  'rules.colAction': 'Action',
  'rules.colStatus': 'Status',
  'rules.colFired': 'Fired',
  'rules.empty':
    'No standing rules. Ask FinAlly to create one — e.g. “buy 5 NVDA if it drops 3% today.”',

  // Portfolio tabs
  'tabs.positions': 'Positions',
  'tabs.orders': 'Orders',
  'tabs.fills': 'Fills',
  'tabs.rules': 'Rules',
  'tabs.backtest': 'Backtest',
  'tabs.analytics': 'Analytics',
  'tabs.board': 'Board',

  // Analytics
  'analytics.loading': 'Loading analytics…',
  'analytics.trades': 'Trades',
  'analytics.winRate': 'Win rate',
  'analytics.realizedPnl': 'Realized P&L',
  'analytics.maxDrawdown': 'Max drawdown',
  'analytics.sharpe': 'Sharpe',
  'analytics.allocation': 'Allocation',
  'analytics.bestTrade': 'Best trade',
  'analytics.worstTrade': 'Worst trade',
  'analytics.buy': 'Buy',
  'analytics.sell': 'Sell',

  // Leaderboard
  'board.loading': 'Loading leaderboard…',
  'board.seasonSince': 'Season {id} · since {date}',
  'board.resetSeason': 'Reset season',
  'board.confirmReset': 'Confirm reset?',
  'board.colTrader': 'Trader',
  'board.colValue': 'Value',
  'board.colReturn': 'Return',
  'board.you': '(you)',

  // Backtest panel
  'backtest.buyWhen': 'Buy when',
  'backtest.priceLabel': 'Price {sym}',
  'backtest.dayPct': 'Day %',
  'backtest.qty': 'Qty',
  'backtest.tp': 'TP %',
  'backtest.sl': 'SL %',
  'backtest.days': 'Days',
  'backtest.runs': 'Runs',
  'backtest.run': 'Run Backtest',
  'backtest.running': 'Running…',
  'backtest.errTicker': 'Enter a valid ticker.',
  'backtest.errThresholdPrice': 'Price threshold must be greater than 0.',
  'backtest.errThreshold': 'Enter a valid threshold.',
  'backtest.errQty': 'Quantity must be greater than 0.',
  'backtest.errDays': 'Days must be an integer between 5 and 120.',
  'backtest.errTp': 'Take profit % must be greater than 0 (or empty).',
  'backtest.errSl': 'Stop loss % must be greater than 0 (or empty).',
  'backtest.errFailed': 'Backtest failed',
  'backtest.statReturn': 'Return',
  'backtest.statBuyHold': 'Buy & Hold',
  'backtest.statMaxDd': 'Max DD',
  'backtest.statWinRate': 'Win rate',
  'backtest.statEntries': 'Entries',
  'backtest.statRoundTrips': 'Round trips',
  'backtest.statProfitFactor': 'Profit factor',
  'backtest.statFinalEquity': 'Final equity',
  'backtest.colTime': 'Time',
  'backtest.colSide': 'Side',
  'backtest.colQty': 'Qty',
  'backtest.colPrice': 'Price',
  'backtest.colReason': 'Reason',
  'backtest.colPnl': 'P&L',
  'backtest.reason.trigger': 'entry',
  'backtest.reason.take_profit': 'take profit',
  'backtest.reason.stop_loss': 'stop loss',
  'backtest.reason.horizon_end': 'horizon end',
  'backtest.trigDayBelow': 'Day % ≤',
  'backtest.trigDayAbove': 'Day % ≥',
  'backtest.trigPriceBelow': 'Price ≤ {sym}',
  'backtest.trigPriceAbove': 'Price ≥ {sym}',
  'backtest.helper':
    "Simulated history (GBM, the ticker's own volatility) — the trigger re-arms daily, entries exit via TP/SL or at horizon end. Dashed line = buy & hold the same $10k.",
  'backtest.empty':
    'Validate a strategy before arming it live — or click “test” on a rule in the Rules tab.',
};

// ---------------------------------------------------------------------------
// Chinese dictionary (A-share market). Terms per CN-3 §3 glossary.
// ---------------------------------------------------------------------------
const zh: Dict = {
  // Header
  'header.cash': '可用',
  'header.realized': '已实现',
  'header.dayPnl': '当日盈亏',
  'header.portfolio': '总资产',
  'header.signOut': '退出',
  'header.guestSignIn': '访客 · 登录',
  'header.go': '确定',
  'header.traderNamePlaceholder': '交易者名称…',
  'header.traderNameAria': '交易者名称',
  'header.signInFailed': '登录失败',
  'header.signInFailedStatus': '登录失败（{status}）',

  // TradeBar
  'tradebar.type': '类型',
  'tradebar.ticker': '代码',
  'tradebar.qty': '数量',
  'tradebar.qtyLots': '手',
  'tradebar.stopLabel': '止损 {sym}',
  'tradebar.stopAria': '止损价',
  'tradebar.limitLabel': '限价 {sym}',
  'tradebar.limitAria': '限价',
  'tradebar.tif': '有效期',
  'tradebar.buy': '买入',
  'tradebar.sell': '卖出',
  'tradebar.est': '预估',
  'tradebar.bid': '买价',
  'tradebar.ask': '卖价',
  'tradebar.maxBuy': '最大可买',
  'tradebar.held': '持有',
  'tradebar.errTickerQty': '请输入有效的代码和数量。',
  'tradebar.errLimit': '请输入有效的限价。',
  'tradebar.errStop': '请输入有效的止损价。',
  'tradebar.errOrderFailed': '下单失败',
  'tradebar.errTradeFailed': '交易失败',
  'tradebar.concentration': '⚠ 这笔买入将使 {ticker} 约占组合 {pct}%。',

  // Fills / order confirmations
  'fill.bought': '买入 {qty} {ticker} @ {price}',
  'fill.sold': '卖出 {qty} {ticker} @ {price}',
  'fill.stopPlaced': '止损单已挂: {verb} {qty} {ticker} @ 止损 {stop}',
  'fill.stopLimitPlaced': '止损限价单已挂: {verb} {qty} {ticker} @ 止损 {stop} / {cmp}{limit}',
  'fill.orderPlaced': '委托已挂: {verb} {qty} {ticker} @ {cmp}{limit}',

  // ChatPanel
  'chat.title': 'FinAlly AI',
  'chat.review': '复盘',
  'chat.reviewTitle': '让 FinAlly 生成每日组合复盘',
  'chat.toggle': '折叠/展开对话',
  'chat.empty': '让 FinAlly 分析你的持仓、给出交易建议，或管理自选。',
  'chat.thinking': '思考中…',
  'chat.placeholder': '向 FinAlly 咨询你的组合…',
  'chat.send': '发送',
  'chat.errGeneric': '出错了，请重试。',
  'chat.collapse': '收起',
  'chat.showFull': '展开完整简报',
  'chat.kind.brief': '市场简报',
  'chat.kind.review': '每日复盘',
  'chat.kind.rule': '规则',

  // ChatPanel action badges
  'badge.stopWord': '止损',
  'badge.win': '胜率',
  'badge.tradeFailed': '交易失败: {ticker} — {error}',
  'badge.orderFailed': '委托失败: {ticker} — {error}',
  'badge.orderPlaced': '委托已挂: {verb} {qty} {ticker} @ {detail}',
  'badge.ruleFailed': '规则失败: {ticker} {error}',
  'badge.ruleArmed': '规则已启用: {desc}',
  'badge.backtestFailed': '回测失败: {ticker} — {error}',
  'badge.backtest': '回测 {ticker}: {ret} (基准 {bh}) · {rt} 笔{win}',
  'badge.watchlistFailed': '自选修改失败: {ticker} — {error}',
  'badge.added': '已添加 {ticker}',
  'badge.removed': '已移除 {ticker}',
  'badge.rejected': '被拒绝',

  // WatchlistPanel
  'watchlist.addPlaceholder': '添加代码…',
  'watchlist.add': '添加',
  'watchlist.addAria': '添加代码',
  'watchlist.errFormat': '代码必须为 1-10 位字母 (A-Z)。',
  'watchlist.errAlready': '{ticker} 已在自选中。',
  'watchlist.errAddFail': '添加代码失败。',
  'watchlist.errRemoveFail': '移除代码失败。',
  'watchlist.noPrices': '暂无行情',
  'watchlist.waitingFeed': '正在等待实时行情…',
  'watchlist.colSymbol': '代码',
  'watchlist.colPrice': '现价',
  'watchlist.colDayPct': '涨跌幅',
  'watchlist.colChart': '走势',

  // StatusBar
  'status.sim247': '模拟 24/7',
  'status.open': '交易中',
  'status.closed': '已收盘',
  'status.closesIn': '距收盘 {t}',
  'status.opensIn': '距开盘 {t}',
  'status.feedNone': '行情: —',
  'status.feed': '行情: {age}前',
  'status.shortcuts': '快捷键:',
  'status.scSearch': '搜索',
  'status.scSelect': '选择',
  'status.scTrade': '交易',

  // NewsTicker
  'news.empty': '市场异动将显示在此 — 正在监测异常波动…',

  // PnL chart
  'pnl.title': '组合盈亏',
  'pnl.empty': '暂无组合历史。',

  // Positions table
  'positions.colTicker': '代码',
  'positions.colQty': '数量',
  'positions.colAvgCost': '成本',
  'positions.colPrice': '现价',
  'positions.colPnl': '盈亏',
  'positions.colChange': '涨跌%',
  'positions.empty': '暂无持仓。使用下单栏买入股票。',

  // Fills
  'fills.colTime': '时间',
  'fills.colSide': '方向',
  'fills.colTicker': '代码',
  'fills.colQty': '数量',
  'fills.colPrice': '价格',
  'fills.colValue': '金额',
  'fills.colFee': '手续费',
  'fills.colRealized': '已实现',
  'fills.empty': '暂无成交。成交后将立即显示在此。',

  // Open orders
  'orders.colTime': '时间',
  'orders.colSide': '方向',
  'orders.colTicker': '代码',
  'orders.colQty': '数量',
  'orders.colKind': '类型',
  'orders.colLimit': '限价',
  'orders.colStop': '止损',
  'orders.empty': '暂无未成交委托。在下单栏挂出限价单 — 价格触及前将挂在此处。',

  // Rules
  'rules.colRule': '规则',
  'rules.colCondition': '条件',
  'rules.colAction': '操作',
  'rules.colStatus': '状态',
  'rules.colFired': '触发',
  'rules.empty': '暂无规则。可让 FinAlly 创建一条 — 例如「NVDA 今日跌 3% 就买入 5 股」。',

  // Portfolio tabs
  'tabs.positions': '持仓',
  'tabs.orders': '委托',
  'tabs.fills': '成交',
  'tabs.rules': '规则',
  'tabs.backtest': '回测',
  'tabs.analytics': '分析',
  'tabs.board': '榜单',

  // Analytics
  'analytics.loading': '正在加载分析…',
  'analytics.trades': '交易数',
  'analytics.winRate': '胜率',
  'analytics.realizedPnl': '已实现盈亏',
  'analytics.maxDrawdown': '最大回撤',
  'analytics.sharpe': '夏普',
  'analytics.allocation': '配置',
  'analytics.bestTrade': '最佳交易',
  'analytics.worstTrade': '最差交易',
  'analytics.buy': '买入',
  'analytics.sell': '卖出',

  // Leaderboard
  'board.loading': '正在加载榜单…',
  'board.seasonSince': '第 {id} 赛季 · 始于 {date}',
  'board.resetSeason': '重置赛季',
  'board.confirmReset': '确认重置?',
  'board.colTrader': '交易者',
  'board.colValue': '市值',
  'board.colReturn': '收益',
  'board.you': '(你)',

  // Backtest panel
  'backtest.buyWhen': '买入条件',
  'backtest.priceLabel': '价格 {sym}',
  'backtest.dayPct': '涨跌%',
  'backtest.qty': '数量',
  'backtest.tp': '止盈%',
  'backtest.sl': '止损%',
  'backtest.days': '天数',
  'backtest.runs': '次数',
  'backtest.run': '运行回测',
  'backtest.running': '运行中…',
  'backtest.errTicker': '请输入有效代码。',
  'backtest.errThresholdPrice': '价格阈值必须大于 0。',
  'backtest.errThreshold': '请输入有效阈值。',
  'backtest.errQty': '数量必须大于 0。',
  'backtest.errDays': '天数须为 5 到 120 之间的整数。',
  'backtest.errTp': '止盈% 必须大于 0（或留空）。',
  'backtest.errSl': '止损% 必须大于 0（或留空）。',
  'backtest.errFailed': '回测失败',
  'backtest.statReturn': '收益',
  'backtest.statBuyHold': '买入持有',
  'backtest.statMaxDd': '最大回撤',
  'backtest.statWinRate': '胜率',
  'backtest.statEntries': '入场',
  'backtest.statRoundTrips': '完整回合',
  'backtest.statProfitFactor': '盈亏比',
  'backtest.statFinalEquity': '期末权益',
  'backtest.colTime': '时间',
  'backtest.colSide': '方向',
  'backtest.colQty': '数量',
  'backtest.colPrice': '价格',
  'backtest.colReason': '原因',
  'backtest.colPnl': '盈亏',
  'backtest.reason.trigger': '入场',
  'backtest.reason.take_profit': '止盈',
  'backtest.reason.stop_loss': '止损',
  'backtest.reason.horizon_end': '到期',
  'backtest.trigDayBelow': '涨跌% ≤',
  'backtest.trigDayAbove': '涨跌% ≥',
  'backtest.trigPriceBelow': '价格 ≤ {sym}',
  'backtest.trigPriceAbove': '价格 ≥ {sym}',
  'backtest.helper':
    '模拟历史行情（GBM，采用该标的自身波动率）— 触发条件每日重置，持仓通过止盈/止损或到期平仓。虚线 = 同额 $10k 买入并持有。',
  'backtest.empty': '在实盘启用前先验证策略 — 或在规则页点击某条规则的「测试」。',
};

const DICTS: Record<Lang, Dict> = { en, zh };

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    key in params ? String(params[key]) : `{${key}}`
  );
}

/**
 * Pure translation: look up `key` in the language dictionary (falling back to
 * en, then to the raw key), then interpolate {params}.
 */
export function translate(
  lang: Lang,
  key: string,
  params?: Record<string, string | number>
): string {
  const template = DICTS[lang][key] ?? en[key] ?? key;
  return interpolate(template, params);
}

export type TFunction = (key: string, params?: Record<string, string | number>) => string;

/**
 * Build a bound `t` for a known language — handy for components that already
 * hold a MarketProfile (e.g. via props) and must avoid an extra SWR hook.
 */
export function makeT(lang: Lang): TFunction {
  return (key, params) => translate(lang, key, params);
}

/**
 * Hook flavour: resolves the language from the runtime market profile. Defaults
 * to `en` while the profile is loading/undefined, so US components are stable.
 */
export function useT(): TFunction {
  const profile = useMarketProfile();
  const lang = langFromLocale(profile.locale);
  return (key, params) => translate(lang, key, params);
}
