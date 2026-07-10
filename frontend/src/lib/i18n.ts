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

  // --- CN-4a additions -------------------------------------------------------
  // Backtest aria-labels (screen-reader names for the config inputs)
  'backtest.ariaTicker': 'Backtest ticker',
  'backtest.ariaTrigger': 'Trigger type',
  'backtest.ariaThreshold': 'Threshold',
  'backtest.ariaQty': 'Backtest quantity',
  'backtest.ariaTp': 'Take profit percent',
  'backtest.ariaSl': 'Stop loss percent',
  'backtest.ariaDays': 'Days',
  // Backtest Monte-Carlo (runs > 1) distribution strip labels
  'backtest.summaryRuns': '{n} runs',
  'backtest.summaryMedian': 'Median',
  'backtest.summaryP5': 'P5',
  'backtest.summaryP95': 'P95',
  'backtest.summaryPositive': 'Positive',
  'backtest.summaryMedianDd': 'Median DD',
  // Leaderboard reset errors
  'board.resetFailed': 'Reset failed',
  'board.resetFailedStatus': 'Reset failed ({status})',
  // Open-orders cancel errors
  'orders.cancelFailed': 'Cancel failed',
  'orders.cancelFailedStatus': 'Cancel failed ({status})',
  // TradeBar whole-lot hint (lot markets only — never shown on US, lot_size 1)
  'tradebar.wholeLotHint': 'Enter a whole number of lots.',

  // --- P1 additions ----------------------------------------------------------
  // Header navigation (P1 §2)
  'nav.desk': 'Desk',
  'nav.market': 'Market',
  'nav.journal': 'Journal',
  'nav.arena': 'Arena',

  // Market page (P1 §4)
  'market.gridTitle': 'All Symbols',
  'market.colCode': 'Code',
  'market.colPrice': 'Price',
  'market.colDayPct': 'Day %',
  'market.colHigh': 'High',
  'market.colLow': 'Low',
  'market.colVolume': 'Vol',
  'market.colSector': 'Sector',
  'market.loading': 'Waiting for the live market feed…',
  'market.heatmapTitle': 'Sector Heatmap',
  'market.eventsTitle': 'Event Archive',
  'market.eventsEmpty': 'No archived market events yet.',
  'market.loadMore': 'Load more',
  'market.loadingMore': 'Loading…',

  // Symbol page (P1 §5)
  'symbol.empty': 'No symbol selected.',
  'symbol.statsTitle': 'Day Stats',
  'symbol.prevClose': 'Prev Close',
  'symbol.high': 'High',
  'symbol.low': 'Low',
  'symbol.amplitude': 'Range %',
  'symbol.volume': 'Vol',
  'symbol.bid': 'Bid',
  'symbol.ask': 'Ask',
  'symbol.limitUp': 'Limit Up',
  'symbol.limitDown': 'Limit Down',
  'symbol.positionTitle': 'My Position',
  'symbol.positionEmpty': 'No position in {ticker}.',
  'symbol.posQty': 'Qty',
  'symbol.posAvgCost': 'Avg Cost',
  'symbol.posPnl': 'Unrealized P&L',
  'symbol.tradesTitle': 'My Fills',
  'symbol.tradesEmpty': 'No fills for {ticker} yet.',
  'symbol.eventsTitle': 'Event History',
  'symbol.eventsEmpty': 'No events for this symbol yet.',
  'symbol.aiAnalyze': 'AI Analyze',
  'symbol.aiPrompt':
    "Analyze {ticker} for me: given my current position and today's price action, should I adjust?",

  // Journal page (P1 §6)
  'journal.reviewsTitle': 'Review Archive',
  'journal.runReview': 'Run Review',
  'journal.running': 'Running…',
  'journal.reviewFailed': 'Review failed',
  'journal.reviewsEmpty': "No reviews yet. Run one to archive today's takeaways.",
  'journal.daysTitle': 'Trades by Day',
  'journal.daysLoading': 'Loading trades…',
  'journal.daysEmpty': 'No trades yet.',
  'journal.tradeCount': '{n} trades',
  'journal.dayRealized': 'Realized',
  'journal.filterPlaceholder': 'Filter by ticker…',
  'journal.filterAria': 'Filter trades by ticker',

  // Arena page (P1 §7)
  'arena.seasonsTitle': 'Season History',
  'arena.season': 'Season {id}',
  'arena.inProgress': 'In progress',
  'arena.seasonsEmpty': 'No seasons yet.',
  'arena.colRank': '#',
  'arena.colTrader': 'Trader',
  'arena.colFinalValue': 'Final Value',
  'arena.colReturn': 'Return',

  // --- P2 additions (strategy center + Run Library) --------------------------
  // Header navigation (P2 §8)
  'nav.strategies': 'Strategies',
  'nav.runs': 'Runs',

  // Chat kind + strategy action badges (P2 §7/§8)
  'chat.kind.strategy': 'Strategy',
  'badge.strategyCreated': 'Strategy created: {name} ({ticker})',
  'badge.strategyDeployed': 'Strategy deployed: {name}',
  'badge.strategyPaused': 'Strategy paused: {name}',
  'badge.strategyFailed': 'Strategy failed: {name} — {error}',
  // …Backtest is the stats head; …BacktestSaved is the tail ChatPanel appends
  // after ' · ' (linked to /run?id={run_id} when the outcome carries one).
  'badge.strategyBacktest': 'Backtest {name}: {ret} (B&H {bh}) · {rt} trades{win}',
  'badge.strategyBacktestSaved': 'saved to Runs',

  // Strategy pages (/strategies, /strategy?id=X)
  'strategy.title': 'Strategy Center',
  'strategy.templatesTitle': 'Templates',
  'strategy.formTitle': 'Create Strategy',
  'strategy.name': 'Name',
  'strategy.ticker': 'Ticker',
  'strategy.template': 'Template',
  'strategy.templateCustom': 'Custom',
  'strategy.entryTitle': 'Entry conditions',
  'strategy.exitsTitle': 'Exits',
  'strategy.sizingTitle': 'Sizing',
  'strategy.addCondition': '+ Condition',
  'strategy.removeCondition': 'Remove',
  'strategy.create': 'Create Strategy',
  'strategy.creating': 'Creating…',
  'strategy.createFailed': 'Create failed',
  'strategy.errName': 'Enter a name (1–40 characters).',
  'strategy.errTicker': 'Enter a valid ticker.',
  'strategy.errValue': 'Enter a value for every condition that needs one.',
  'strategy.errValueRange': 'Condition value is out of range (price > 0, RSI 0–100, pullback > 0).',
  'strategy.errFastSlow': 'Fast period must be less than slow period.',
  'strategy.errExits':
    'Exit values must be greater than 0; max days must be a whole number between 1 and 120.',
  'strategy.errSizing': 'Enter a valid size: quantity > 0, or 1–100% of cash.',
  'strategy.listTitle': 'My Strategies',
  'strategy.listEmpty': 'No strategies yet. Start from a template above or build one from scratch.',
  'strategy.colName': 'Name',
  'strategy.colTicker': 'Ticker',
  'strategy.colStatus': 'Status',
  'strategy.colPnl': 'Realized P&L',
  'strategy.colRuns': 'Runs',
  'strategy.colActions': 'Actions',
  'strategy.details': 'Details',
  'strategy.empty': 'No strategy selected.',
  'strategy.notFound': 'Strategy not found.',
  'strategy.configTitle': 'Configuration',
  'strategy.performanceTitle': 'Performance',
  'strategy.backtestTitle': 'Backtests',
  'strategy.runBacktest': 'Run Backtest',
  'strategy.running': 'Running…',
  'strategy.compare': 'Compare',
  'strategy.compareHint': 'Select two runs to compare.',
  'strategy.openPosition': 'Open position',
  'strategy.noOpenPosition': 'No open position.',

  // Status chips
  'strategy.status.draft': 'Draft',
  'strategy.status.live': 'Live',
  'strategy.status.paused': 'Paused',
  'strategy.status.archived': 'Archived',

  // Lifecycle controls — soft deploy gate + pause/archive semantics (P2 §8)
  'strategy.deploy': 'Deploy',
  'strategy.pause': 'Pause',
  'strategy.resume': 'Resume',
  'strategy.archive': 'Archive',
  'strategy.confirmArchive': 'Confirm archive?',
  'strategy.deployConfirm': 'Confirm deploy?',
  'strategy.deployNoRunsWarning':
    'This strategy has never been backtested — click Deploy again to go live anyway.',
  'strategy.pauseHint':
    'Paused strategies are fully frozen — they stop managing any open position (no exits fire) until resumed.',
  'strategy.archiveHint':
    'Archiving stops the strategy for good; any open shares stay in your portfolio for manual handling.',

  // Condition builder — field names (dropdown)
  'strategy.cond.field.price': 'Price',
  'strategy.cond.field.day_change_pct': 'Day change %',
  'strategy.cond.field.ma': 'Price vs SMA',
  'strategy.cond.field.ma_cross': 'SMA cross',
  'strategy.cond.field.ema_cross': 'EMA cross',
  'strategy.cond.field.rsi': 'RSI',
  'strategy.cond.field.window_high': 'Rolling high breakout',
  'strategy.cond.field.window_low': 'Rolling low breakdown',
  'strategy.cond.field.pullback_from_high_pct': 'Pullback from high %',

  // Condition human-readable text (conditionText) — group joiners, op words,
  // and one sentence template per field
  'strategy.cond.all': 'all of',
  'strategy.cond.any': 'any of',
  'strategy.cond.above': '≥',
  'strategy.cond.below': '≤',
  'strategy.cond.price': 'price {op} {sym}{value}',
  'strategy.cond.day_change_pct': 'day change {op} {value}%',
  'strategy.cond.ma': 'price {op} SMA({period}) by {value}%',
  'strategy.cond.ma_cross.above': 'SMA({fast})/SMA({slow}) golden cross',
  'strategy.cond.ma_cross.below': 'SMA({fast})/SMA({slow}) death cross',
  'strategy.cond.ema_cross.above': 'EMA({fast})/EMA({slow}) golden cross',
  'strategy.cond.ema_cross.below': 'EMA({fast})/EMA({slow}) death cross',
  'strategy.cond.rsi': 'RSI({period}) {op} {value}',
  'strategy.cond.window_high': 'breaks the {minutes}-minute high',
  'strategy.cond.window_low': 'breaks the {minutes}-minute low',
  'strategy.cond.pullback_from_high_pct': 'pulls back ≥ {value}% from the {minutes}-minute high',

  // Exits — form labels + human-readable summary parts
  'strategy.exitTp': 'TP %',
  'strategy.exitSl': 'SL %',
  'strategy.exitTrailing': 'Trail %',
  'strategy.exitMaxDays': 'Max days',
  'strategy.exit.take_profit_pct': 'TP {value}%',
  'strategy.exit.stop_loss_pct': 'SL {value}%',
  'strategy.exit.trailing_stop_pct': 'Trailing stop {value}%',
  'strategy.exit.max_holding_days': 'Max hold {value}d',
  'strategy.exit.none': 'No exits',

  // Sizing — form labels + human-readable summary parts
  'strategy.sizingFixed': 'Fixed qty',
  'strategy.sizingCashPct': '% of cash',
  'strategy.sizing.fixed_qty': 'Fixed qty {qty}',
  'strategy.sizing.cash_pct': '{pct}% of cash',

  // Template registry — names/descriptions keyed by template key (P2 §6)
  'strategy.template.dip_buyer.name': 'Dip Buyer',
  'strategy.template.dip_buyer.desc':
    'Buys a sharp intraday dip (day change ≤ −3%); exits via TP 4% / SL 3%.',
  'strategy.template.momentum_breakout.name': 'Momentum Breakout',
  'strategy.template.momentum_breakout.desc':
    'Buys a break of the 60-minute high; rides it with a 2.5% trailing stop / SL 3%.',
  'strategy.template.ma_golden_cross.name': 'MA Golden Cross',
  'strategy.template.ma_golden_cross.desc':
    'Buys when SMA(5) crosses above SMA(20); exits via TP 5% / SL 3%.',
  'strategy.template.grid_lite.name': 'Grid Lite',
  'strategy.template.grid_lite.desc':
    'Simplified grid: buys a ≥2% pullback from the 60-minute high; TP 2% / SL 6%.',
  'strategy.template.rsi_rebound.name': 'RSI Rebound',
  'strategy.template.rsi_rebound.desc':
    'Buys oversold conditions when RSI(14) drops below 30; exits via TP 4% / SL 3%.',
  'strategy.template.trend_rider.name': 'Trend Rider',
  'strategy.template.trend_rider.desc':
    'Buys strength — price above SMA(30) with day change ≥ 0.5%; 3% trailing stop.',

  // Run Library (/runs, /run?id=X, Backtest-tab save)
  'runs.title': 'Run Library',
  'runs.filterTicker': 'Filter by ticker…',
  'runs.filterTickerAria': 'Filter runs by ticker',
  'runs.filterStrategy': 'Strategy',
  'runs.allStrategies': 'All strategies',
  'runs.colTime': 'Time',
  'runs.colTicker': 'Ticker',
  'runs.colStrategy': 'Strategy',
  'runs.colLabel': 'Label',
  'runs.colReturn': 'Return',
  'runs.colWinRate': 'Win rate',
  'runs.colMaxDd': 'Max DD',
  'runs.delete': 'Delete',
  'runs.confirmDelete': 'Confirm delete?',
  'runs.deleteFailed': 'Delete failed',
  'runs.empty':
    'No saved backtests yet. Save one from the Backtest tab or run one from a strategy page.',
  'runs.loading': 'Loading runs…',
  'runs.noneSelected': 'No run selected.',
  'runs.notFound': 'Run not found.',
  'runs.detailTitle': 'Backtest Run',
  'runs.backToRuns': 'Back to Runs',
  'runs.backToStrategy': 'Back to strategy',
  'runs.save': 'Save to Runs',
  'runs.saving': 'Saving…',
  'runs.saveLabelPlaceholder': 'Label (optional)…',
  'runs.saved': 'Saved — view in Runs →',
  'runs.saveFailed': 'Save failed',

  // --- P3 additions (developer portal, P3 §8) --------------------------------
  // Header navigation
  'nav.developers': 'Developers',

  // Key list
  'dev.keysTitle': 'API Keys',
  'dev.keysLoading': 'Loading keys…',
  'dev.keysEmpty': 'No API keys yet. Create one below to trade programmatically.',
  'dev.colLabel': 'Label',
  'dev.colPrefix': 'Prefix',
  'dev.colCreated': 'Created',
  'dev.colLastUsed': 'Last used',
  'dev.colConstraints': 'Constraints',
  'dev.colStatus': 'Status',
  'dev.colActions': 'Actions',
  'dev.neverUsed': 'Never',
  'dev.active': 'Active',
  'dev.frozen': 'Frozen',
  'dev.freeze': 'Freeze',
  'dev.unfreeze': 'Unfreeze',
  'dev.revoke': 'Revoke',
  'dev.confirmRevoke': 'Confirm revoke?',
  'dev.edit': 'Edit',
  'dev.cancel': 'Cancel',
  'dev.save': 'Save',
  'dev.saving': 'Saving…',
  'dev.updateFailed': 'Update failed',
  'dev.revokeFailed': 'Revoke failed',
  'dev.unrestricted': 'Unrestricted',
  'dev.constraintTickers': 'Tickers: {list}',
  'dev.constraintMaxQty': 'Max qty {qty}',
  'dev.constraintDailyCap': '{n} trades/day',
  'dev.editHint': 'Leave a field empty for no limit.',

  // Create form + one-time secret
  'dev.createTitle': 'Create Key',
  'dev.labelAria': 'Key label',
  'dev.labelPlaceholder': 'Key label…',
  'dev.tickersAria': 'Allowed tickers',
  'dev.tickersPlaceholder': 'Allowed tickers, comma-separated (empty = all)…',
  'dev.maxQtyAria': 'Max order quantity',
  'dev.maxQtyPlaceholder': 'Max qty per order…',
  'dev.dailyCapAria': 'Daily trade cap',
  'dev.dailyCapPlaceholder': 'Max trades per day…',
  'dev.create': 'Create Key',
  'dev.creating': 'Creating…',
  'dev.createFailed': 'Create failed',
  'dev.errLabel': 'Enter a label (1–40 characters).',
  'dev.errMaxQty': 'Max order quantity must be greater than 0 (or empty).',
  'dev.errDailyCap': 'Daily trade cap must be a whole number ≥ 1 (or empty).',
  'dev.secretTitle': 'Your new API key',
  'dev.secretWarning':
    'Shown only once — copy it now. FinAlly stores only a hash and cannot recover it.',
  'dev.copy': 'Copy',
  'dev.copied': 'Copied',
  'dev.copyFailed': 'Copy failed',
  'dev.dismiss': 'Dismiss',

  // Audit ledger
  'dev.auditTitle': 'Audit Log',
  'dev.auditKeyAria': 'Audit key',
  'dev.auditSelectKey': 'Select a key…',
  'dev.auditLoading': 'Loading audit…',
  'dev.auditEmpty': 'No audit entries for this key yet.',
  'dev.auditColTime': 'Time',
  'dev.auditColRequest': 'Request',
  'dev.auditColResult': 'Result',
  'dev.auditColDigest': 'Digest',
  'dev.loadMore': 'Load more',
  'dev.loadingMore': 'Loading…',

  // Quickstart
  'dev.quickstartTitle': 'Quickstart',
  'dev.quickstartIntro':
    'Authenticate every request with an Authorization: Bearer header. Rate limit: bursts of 10, refilled at 5 requests/second per key.',
  'dev.curlTitle': 'curl',
  'dev.pythonTitle': 'Python',
  'dev.swaggerLink': 'Full API reference (Swagger) →',
  'dev.botHint':
    'A complete example trading bot ships with the repo at examples/finally_bot.py — see examples/README.md for the walkthrough.',

  // --- P4 additions (sentiment · correlation · calendar · player) ------------
  // Market sentiment gauge (P4 §1)
  'market.sentimentTitle': 'Market Sentiment',
  'market.sentimentLoading': 'Measuring market temperature…',
  'market.sentimentLabel.frozen': 'Frozen',
  'market.sentimentLabel.cool': 'Cool',
  'market.sentimentLabel.neutral': 'Neutral',
  'market.sentimentLabel.active': 'Active',
  'market.sentimentLabel.hot': 'Hot',
  'market.sentimentBreadth': 'Breadth',
  'market.sentimentVolatility': 'Volatility',
  'market.sentimentVolume': 'Volume',

  // Correlation heatmap (P4 §2)
  'market.corrTitle': 'Correlation Matrix',
  'market.corrEmpty': 'Not enough bar history yet — the matrix fills in a few minutes after the open.',

  // Journal P&L calendar (P4 §3)
  'journal.calTitle': 'P&L Calendar',
  'journal.calPrevAria': 'Previous month',
  'journal.calNextAria': 'Next month',
  'journal.calClear': 'Clear day filter',

  // Player public profile (P4 §4)
  'player.empty': 'No player selected.',
  'player.loading': 'Loading player…',
  'player.notFound': 'Player not found.',
  'player.private': 'This trader keeps their profile private.',
  'player.since': 'since {date}',
  'player.rank': 'Rank',
  'player.totalValue': 'Total Value',
  'player.return': 'Return',
  'player.equityTitle': 'Equity Curve',
  'player.weightsTitle': 'Position Weights',
  'player.weightsEmpty': 'No open positions.',
  'player.privacyPublic': 'Public',
  'player.privacyPrivate': 'Private',
  'player.privacyFailed': 'Privacy update failed',
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

  // --- CN-4a additions -------------------------------------------------------
  'backtest.ariaTicker': '回测代码',
  'backtest.ariaTrigger': '触发类型',
  'backtest.ariaThreshold': '阈值',
  'backtest.ariaQty': '回测数量',
  'backtest.ariaTp': '止盈百分比',
  'backtest.ariaSl': '止损百分比',
  'backtest.ariaDays': '天数',
  'backtest.summaryRuns': '{n} 次',
  'backtest.summaryMedian': '中位数',
  'backtest.summaryP5': 'P5',
  'backtest.summaryP95': 'P95',
  'backtest.summaryPositive': '正收益',
  'backtest.summaryMedianDd': '中位回撤',
  'board.resetFailed': '重置失败',
  'board.resetFailedStatus': '重置失败（{status}）',
  'orders.cancelFailed': '撤单失败',
  'orders.cancelFailedStatus': '撤单失败（{status}）',
  'tradebar.wholeLotHint': '请输入整数手数。',

  // --- P1 additions ----------------------------------------------------------
  // Header navigation (P1 §2)
  'nav.desk': '交易台',
  'nav.market': '市场',
  'nav.journal': '复盘',
  'nav.arena': '竞技场',

  // Market page (P1 §4)
  'market.gridTitle': '全市场行情',
  'market.colCode': '代码',
  'market.colPrice': '现价',
  'market.colDayPct': '涨跌幅',
  'market.colHigh': '最高',
  'market.colLow': '最低',
  'market.colVolume': '成交量',
  'market.colSector': '板块',
  'market.loading': '正在等待实时行情…',
  'market.heatmapTitle': '板块热力图',
  'market.eventsTitle': '事件归档',
  'market.eventsEmpty': '暂无市场事件归档。',
  'market.loadMore': '加载更多',
  'market.loadingMore': '加载中…',

  // Symbol page (P1 §5)
  'symbol.empty': '未选择标的。',
  'symbol.statsTitle': '当日统计',
  'symbol.prevClose': '昨收',
  'symbol.high': '最高',
  'symbol.low': '最低',
  'symbol.amplitude': '振幅',
  'symbol.volume': '成交量',
  'symbol.bid': '买一',
  'symbol.ask': '卖一',
  'symbol.limitUp': '涨停价',
  'symbol.limitDown': '跌停价',
  'symbol.positionTitle': '我的持仓',
  'symbol.positionEmpty': '暂无 {ticker} 持仓。',
  'symbol.posQty': '数量',
  'symbol.posAvgCost': '成本',
  'symbol.posPnl': '浮动盈亏',
  'symbol.tradesTitle': '我的成交',
  'symbol.tradesEmpty': '暂无 {ticker} 成交。',
  'symbol.eventsTitle': '事件史',
  'symbol.eventsEmpty': '该标的暂无事件。',
  'symbol.aiAnalyze': 'AI 分析',
  'symbol.aiPrompt': '请帮我分析 {ticker}：结合我的当前持仓与今日走势，是否需要调整？',

  // Journal page (P1 §6)
  'journal.reviewsTitle': '复盘归档',
  'journal.runReview': '生成复盘',
  'journal.running': '生成中…',
  'journal.reviewFailed': '复盘失败',
  'journal.reviewsEmpty': '暂无复盘。点击「生成复盘」归档今日心得。',
  'journal.daysTitle': '按日成交',
  'journal.daysLoading': '正在加载成交…',
  'journal.daysEmpty': '暂无成交。',
  'journal.tradeCount': '{n} 笔',
  'journal.dayRealized': '已实现',
  'journal.filterPlaceholder': '按代码过滤…',
  'journal.filterAria': '按代码过滤成交',

  // Arena page (P1 §7)
  'arena.seasonsTitle': '赛季史',
  'arena.season': '第 {id} 赛季',
  'arena.inProgress': '进行中',
  'arena.seasonsEmpty': '暂无赛季。',
  'arena.colRank': '#',
  'arena.colTrader': '交易者',
  'arena.colFinalValue': '期末市值',
  'arena.colReturn': '收益',

  // --- P2 additions (strategy center + Run Library) --------------------------
  // Header navigation (P2 §8)
  'nav.strategies': '策略',
  'nav.runs': '回测库',

  // Chat kind + strategy action badges (P2 §7/§8)
  'chat.kind.strategy': '策略',
  'badge.strategyCreated': '策略已创建: {name}（{ticker}）',
  'badge.strategyDeployed': '策略已部署: {name}',
  'badge.strategyPaused': '策略已暂停: {name}',
  'badge.strategyFailed': '策略操作失败: {name} — {error}',
  'badge.strategyBacktest': '回测 {name}: {ret} (基准 {bh}) · {rt} 笔{win}',
  'badge.strategyBacktestSaved': '已存入回测库',

  // Strategy pages (/strategies, /strategy?id=X)
  'strategy.title': '策略中心',
  'strategy.templatesTitle': '策略模板',
  'strategy.formTitle': '创建策略',
  'strategy.name': '名称',
  'strategy.ticker': '代码',
  'strategy.template': '模板',
  'strategy.templateCustom': '自定义',
  'strategy.entryTitle': '入场条件',
  'strategy.exitsTitle': '出场',
  'strategy.sizingTitle': '仓位',
  'strategy.addCondition': '+ 条件',
  'strategy.removeCondition': '删除',
  'strategy.create': '创建策略',
  'strategy.creating': '创建中…',
  'strategy.createFailed': '创建失败',
  'strategy.errName': '请输入名称（1-40 字）。',
  'strategy.errTicker': '请输入有效代码。',
  'strategy.errValue': '请为需要数值的条件填写数值。',
  'strategy.errValueRange': '条件数值超出范围（价格 > 0，RSI 0–100，回撤 > 0）。',
  'strategy.errFastSlow': '快线周期必须小于慢线周期。',
  'strategy.errExits': '退出参数必须大于 0；最长持有天数须为 1–120 的整数。',
  'strategy.errSizing': '请输入有效仓位：数量 > 0，或资金占比 1–100%。',
  'strategy.listTitle': '我的策略',
  'strategy.listEmpty': '暂无策略。可从上方模板开始，或从零构建一条。',
  'strategy.colName': '名称',
  'strategy.colTicker': '代码',
  'strategy.colStatus': '状态',
  'strategy.colPnl': '已实现盈亏',
  'strategy.colRuns': '回测',
  'strategy.colActions': '操作',
  'strategy.details': '详情',
  'strategy.empty': '未选择策略。',
  'strategy.notFound': '未找到该策略。',
  'strategy.configTitle': '配置',
  'strategy.performanceTitle': '绩效',
  'strategy.backtestTitle': '回测',
  'strategy.runBacktest': '运行回测',
  'strategy.running': '运行中…',
  'strategy.compare': '对比',
  'strategy.compareHint': '勾选两条回测进行对比。',
  'strategy.openPosition': '当前持仓',
  'strategy.noOpenPosition': '暂无持仓。',

  // Status chips
  'strategy.status.draft': '草稿',
  'strategy.status.live': '运行中',
  'strategy.status.paused': '已暂停',
  'strategy.status.archived': '已归档',

  // Lifecycle controls — soft deploy gate + pause/archive semantics (P2 §8)
  'strategy.deploy': '部署',
  'strategy.pause': '暂停',
  'strategy.resume': '恢复',
  'strategy.archive': '归档',
  'strategy.confirmArchive': '确认归档？',
  'strategy.deployConfirm': '确认部署？',
  'strategy.deployNoRunsWarning': '该策略尚未回测 — 再次点击「部署」仍将强制上线。',
  'strategy.pauseHint': '暂停即完全冻结 — 策略将停止管理持仓（止盈止损等出场不再触发），直至恢复。',
  'strategy.archiveHint': '归档将永久停止该策略；已持有的份额留在组合中，由你手动处理。',

  // Condition builder — field names (dropdown)
  'strategy.cond.field.price': '价格',
  'strategy.cond.field.day_change_pct': '日涨跌幅',
  'strategy.cond.field.ma': '价格相对均线',
  'strategy.cond.field.ma_cross': '均线交叉',
  'strategy.cond.field.ema_cross': 'EMA 交叉',
  'strategy.cond.field.rsi': 'RSI',
  'strategy.cond.field.window_high': '滚动新高突破',
  'strategy.cond.field.window_low': '滚动新低破位',
  'strategy.cond.field.pullback_from_high_pct': '自高点回撤%',

  // Condition human-readable text (conditionText) — group joiners, op words,
  // and one sentence template per field
  'strategy.cond.all': '全部满足',
  'strategy.cond.any': '任一满足',
  'strategy.cond.above': '≥',
  'strategy.cond.below': '≤',
  'strategy.cond.price': '价格 {op} {sym}{value}',
  'strategy.cond.day_change_pct': '日涨跌幅 {op} {value}%',
  'strategy.cond.ma': '价格较 SMA({period}) {op} {value}%',
  'strategy.cond.ma_cross.above': 'SMA({fast})/SMA({slow}) 金叉',
  'strategy.cond.ma_cross.below': 'SMA({fast})/SMA({slow}) 死叉',
  'strategy.cond.ema_cross.above': 'EMA({fast})/EMA({slow}) 金叉',
  'strategy.cond.ema_cross.below': 'EMA({fast})/EMA({slow}) 死叉',
  'strategy.cond.rsi': 'RSI({period}) {op} {value}',
  'strategy.cond.window_high': '突破 {minutes} 分钟新高',
  'strategy.cond.window_low': '跌破 {minutes} 分钟新低',
  'strategy.cond.pullback_from_high_pct': '自 {minutes} 分钟高点回撤 ≥ {value}%',

  // Exits — form labels + human-readable summary parts
  'strategy.exitTp': '止盈%',
  'strategy.exitSl': '止损%',
  'strategy.exitTrailing': '移动止损%',
  'strategy.exitMaxDays': '最长持有天数',
  'strategy.exit.take_profit_pct': '止盈 {value}%',
  'strategy.exit.stop_loss_pct': '止损 {value}%',
  'strategy.exit.trailing_stop_pct': '移动止损 {value}%',
  'strategy.exit.max_holding_days': '最长持有 {value} 天',
  'strategy.exit.none': '无出场',

  // Sizing — form labels + human-readable summary parts
  'strategy.sizingFixed': '固定数量',
  'strategy.sizingCashPct': '现金占比%',
  'strategy.sizing.fixed_qty': '固定数量 {qty}',
  'strategy.sizing.cash_pct': '现金的 {pct}%',

  // Template registry — names/descriptions keyed by template key (P2 §6)
  'strategy.template.dip_buyer.name': '抄底',
  'strategy.template.dip_buyer.desc': '当日大跌（≤ −3%）时买入；止盈 4% / 止损 3% 出场。',
  'strategy.template.momentum_breakout.name': '动量突破',
  'strategy.template.momentum_breakout.desc':
    '突破 60 分钟新高时买入；以 2.5% 移动止损跟随 / 止损 3%。',
  'strategy.template.ma_golden_cross.name': '均线金叉',
  'strategy.template.ma_golden_cross.desc': 'SMA(5) 上穿 SMA(20) 金叉时买入；止盈 5% / 止损 3%。',
  'strategy.template.grid_lite.name': '简化网格',
  'strategy.template.grid_lite.desc':
    '简化版网格：自 60 分钟高点回撤 ≥2% 时买入；止盈 2% / 止损 6%。',
  'strategy.template.rsi_rebound.name': 'RSI 超卖反弹',
  'strategy.template.rsi_rebound.desc': 'RSI(14) 跌破 30 超卖时买入；止盈 4% / 止损 3%。',
  'strategy.template.trend_rider.name': '趋势跟随',
  'strategy.template.trend_rider.desc':
    '顺势买入 — 价格站上 SMA(30) 且日涨幅 ≥0.5%；3% 移动止损。',

  // Run Library (/runs, /run?id=X, Backtest-tab save)
  'runs.title': '回测库',
  'runs.filterTicker': '按代码过滤…',
  'runs.filterTickerAria': '按代码过滤回测',
  'runs.filterStrategy': '策略',
  'runs.allStrategies': '全部策略',
  'runs.colTime': '时间',
  'runs.colTicker': '代码',
  'runs.colStrategy': '策略',
  'runs.colLabel': '标签',
  'runs.colReturn': '收益',
  'runs.colWinRate': '胜率',
  'runs.colMaxDd': '最大回撤',
  'runs.delete': '删除',
  'runs.confirmDelete': '确认删除？',
  'runs.deleteFailed': '删除失败',
  'runs.empty': '暂无已保存回测。可在回测页签保存结果，或在策略详情页运行回测。',
  'runs.loading': '正在加载回测…',
  'runs.noneSelected': '未选择回测。',
  'runs.notFound': '未找到该回测。',
  'runs.detailTitle': '回测详情',
  'runs.backToRuns': '返回回测库',
  'runs.backToStrategy': '返回策略',
  'runs.save': '保存到回测库',
  'runs.saving': '保存中…',
  'runs.saveLabelPlaceholder': '标签（可选）…',
  'runs.saved': '已保存 — 前往回测库 →',
  'runs.saveFailed': '保存失败',

  // --- P3 additions (developer portal, P3 §8) --------------------------------
  // Header navigation
  'nav.developers': '开发者',

  // Key list
  'dev.keysTitle': 'API 密钥',
  'dev.keysLoading': '正在加载密钥…',
  'dev.keysEmpty': '暂无 API 密钥。在下方创建一个即可编程交易。',
  'dev.colLabel': '名称',
  'dev.colPrefix': '前缀',
  'dev.colCreated': '创建时间',
  'dev.colLastUsed': '最近使用',
  'dev.colConstraints': '约束',
  'dev.colStatus': '状态',
  'dev.colActions': '操作',
  'dev.neverUsed': '从未',
  'dev.active': '启用中',
  'dev.frozen': '已冻结',
  'dev.freeze': '冻结',
  'dev.unfreeze': '解冻',
  'dev.revoke': '吊销',
  'dev.confirmRevoke': '确认吊销？',
  'dev.edit': '编辑',
  'dev.cancel': '取消',
  'dev.save': '保存',
  'dev.saving': '保存中…',
  'dev.updateFailed': '更新失败',
  'dev.revokeFailed': '吊销失败',
  'dev.unrestricted': '无限制',
  'dev.constraintTickers': '标的: {list}',
  'dev.constraintMaxQty': '单笔上限 {qty}',
  'dev.constraintDailyCap': '每日 {n} 笔',
  'dev.editHint': '留空即不限。',

  // Create form + one-time secret
  'dev.createTitle': '创建密钥',
  'dev.labelAria': '密钥名称',
  'dev.labelPlaceholder': '密钥名称…',
  'dev.tickersAria': '允许标的',
  'dev.tickersPlaceholder': '允许标的，逗号分隔（留空 = 不限）…',
  'dev.maxQtyAria': '单笔数量上限',
  'dev.maxQtyPlaceholder': '单笔数量上限…',
  'dev.dailyCapAria': '每日下单上限',
  'dev.dailyCapPlaceholder': '每日下单上限…',
  'dev.create': '创建密钥',
  'dev.creating': '创建中…',
  'dev.createFailed': '创建失败',
  'dev.errLabel': '请输入名称（1-40 字）。',
  'dev.errMaxQty': '单笔数量上限必须大于 0（或留空）。',
  'dev.errDailyCap': '每日下单上限须为 ≥ 1 的整数（或留空）。',
  'dev.secretTitle': '你的新 API 密钥',
  'dev.secretWarning': '仅显示一次 — 请立即复制。FinAlly 只保存哈希，无法找回明文。',
  'dev.copy': '复制',
  'dev.copied': '已复制',
  'dev.copyFailed': '复制失败',
  'dev.dismiss': '关闭',

  // Audit ledger
  'dev.auditTitle': '审计台账',
  'dev.auditKeyAria': '审计密钥',
  'dev.auditSelectKey': '选择密钥…',
  'dev.auditLoading': '正在加载审计…',
  'dev.auditEmpty': '该密钥暂无审计记录。',
  'dev.auditColTime': '时间',
  'dev.auditColRequest': '请求',
  'dev.auditColResult': '结果',
  'dev.auditColDigest': '摘要',
  'dev.loadMore': '加载更多',
  'dev.loadingMore': '加载中…',

  // Quickstart
  'dev.quickstartTitle': '快速上手',
  'dev.quickstartIntro':
    '每个请求都需携带 Authorization: Bearer 头。限流：每个密钥突发 10 次、每秒补充 5 次。',
  'dev.curlTitle': 'curl',
  'dev.pythonTitle': 'Python',
  'dev.swaggerLink': '完整 API 文档（Swagger）→',
  'dev.botHint':
    '仓库内附完整示例交易机器人 examples/finally_bot.py — 教程见 examples/README.md。',

  // --- P4 additions (sentiment · correlation · calendar · player) ------------
  // Market sentiment gauge (P4 §1)
  'market.sentimentTitle': '市场情绪',
  'market.sentimentLoading': '正在测算市场温度…',
  'market.sentimentLabel.frozen': '冰点',
  'market.sentimentLabel.cool': '低迷',
  'market.sentimentLabel.neutral': '中性',
  'market.sentimentLabel.active': '活跃',
  'market.sentimentLabel.hot': '沸腾',
  'market.sentimentBreadth': '涨跌家数',
  'market.sentimentVolatility': '波动',
  'market.sentimentVolume': '量能',

  // Correlation heatmap (P4 §2)
  'market.corrTitle': '相关性热力图',
  'market.corrEmpty': '开市初期 K 线不足 — 矩阵将在开盘几分钟后自动填充。',

  // Journal P&L calendar (P4 §3)
  'journal.calTitle': '盈亏日历',
  'journal.calPrevAria': '上个月',
  'journal.calNextAria': '下个月',
  'journal.calClear': '清除日期过滤',

  // Player public profile (P4 §4)
  'player.empty': '未选择选手。',
  'player.loading': '正在加载选手…',
  'player.notFound': '未找到该选手。',
  'player.private': '该选手的主页未公开。',
  'player.since': '始于 {date}',
  'player.rank': '排名',
  'player.totalValue': '总资产',
  'player.return': '收益',
  'player.equityTitle': '权益曲线',
  'player.weightsTitle': '持仓权重',
  'player.weightsEmpty': '暂无持仓。',
  'player.privacyPublic': '公开',
  'player.privacyPrivate': '私密',
  'player.privacyFailed': '隐私设置更新失败',
};

const DICTS: Record<Lang, Dict> = { en, zh };

// Read-only view of the raw dictionaries — lets tests assert en/zh keyset
// alignment per namespace without duplicating the key lists (P2 §10).
export const DICTIONARIES: Readonly<Record<Lang, Readonly<Dict>>> = DICTS;

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
