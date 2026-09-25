// يختبر الست استراتيجيات على أسعار حقيقية قديمة ويختار الأفضل
//
// عشان ما نخدع نفسنا (overfitting)، نقسم البيانات قسمين:
//   - تدريب: أول 70% من الفترة
//   - تحقق: آخر 30%، وهي فترة ما "شافتها" عملية الاختيار
// الاستراتيجية لازم تربح في القسمين، ويكون عندها عدد صفقات كافي في فترة التحقق.
// الترتيب حسب ربح فترة التحقق. إذا ما فيه ولا وحدة تنجح، البوت ما يفتح صفقات.
const { STRATEGIES, toSeries } = require('./strategies');

const FEE_PCT = 0.1; // عمولة Binance لكل عملية

// محاكاة صفقات استراتيجية وحدة على الشموع
// الدخول على سعر إغلاق شمعة الإشارة. وقف الخسارة/جني الربح يتفعل لو السعر لمسه داخل الشمعة،
// ولو لمس الاثنين بنفس الشمعة نفترض الأسوأ (وقف الخسارة)
function simulate(candles, strategy, risk) {
  const series = toSeries(candles);
  const ctx = strategy.prepare(series);
  const fee = FEE_PCT / 100;
  const trades = [];
  let pos = null;

  for (let i = 1; i < candles.length; i++) {
    const [ts, , high, low, close] = candles[i];
    if (pos) {
      const sl = pos.entry * (1 - risk.stopLossPct / 100);
      const tp = pos.entry * (1 + risk.takeProfitPct / 100);
      let exit = null;
      let reason = null;
      if (low <= sl) [exit, reason] = [sl, 'وقف خسارة'];
      else if (high >= tp) [exit, reason] = [tp, 'جني ربح'];
      else if (strategy.signal(ctx, i) === 'sell') [exit, reason] = [close, 'إشارة بيع'];
      if (exit !== null) {
        const proceeds = pos.qty * exit * (1 - fee);
        trades.push({ openedAt: pos.ts, closedAt: ts, entry: pos.entry, exit, pnl: proceeds - pos.cost, reason });
        pos = null;
      }
    } else if (strategy.signal(ctx, i) === 'buy') {
      const cost = risk.tradeAmountUsdt;
      pos = { ts, entry: close, cost, qty: (cost * (1 - fee)) / close };
    }
  }
  return trades;
}

function stats(trades, tradeAmount) {
  const wins = trades.filter((t) => t.pnl > 0);
  const grossWin = wins.reduce((a, t) => a + t.pnl, 0);
  const grossLoss = -trades.filter((t) => t.pnl <= 0).reduce((a, t) => a + t.pnl, 0);
  const pnl = grossWin - grossLoss;

  let equity = 0;
  let peak = 0;
  let maxDrawdown = 0;
  for (const t of trades) {
    equity += t.pnl;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak - equity);
  }

  return {
    trades: trades.length,
    wins: wins.length,
    winRate: trades.length ? (wins.length / trades.length) * 100 : 0,
    pnl,
    pnlPct: (pnl / tradeAmount) * 100,
    profitFactor: grossLoss ? grossWin / grossLoss : grossWin ? Infinity : 0,
    maxDrawdown,
  };
}

function optimize(candles, risk, { trainRatio = 0.7, minTrades = 3 } = {}) {
  const splitTime = candles[Math.floor(candles.length * trainRatio)][0];

  const results = STRATEGIES.map((strategy) => {
    const trades = simulate(candles, strategy, risk);
    const train = stats(trades.filter((t) => t.openedAt < splitTime), risk.tradeAmountUsdt);
    const test = stats(trades.filter((t) => t.openedAt >= splitTime), risk.tradeAmountUsdt);
    const qualified = train.pnl > 0 && test.pnl > 0 && test.trades >= minTrades;
    return { id: strategy.id, name: strategy.name, train, test, qualified };
  });

  results.sort((a, b) => Number(b.qualified) - Number(a.qualified) || b.test.pnl - a.test.pnl);
  const best = results[0].qualified ? results[0] : null;

  const first = candles[0][4];
  const last = candles[candles.length - 1][4];
  return {
    at: new Date().toISOString(),
    from: new Date(candles[0][0]).toISOString(),
    to: new Date(candles[candles.length - 1][0]).toISOString(),
    candles: candles.length,
    buyAndHoldPct: (last / first - 1) * 100,
    bestId: best ? best.id : null,
    results,
  };
}

async function fetchHistory(exchange, symbol, timeframe, days) {
  const all = [];
  let cursor = Date.now() - days * 86400e3;
  for (;;) {
    const batch = await exchange.fetchOHLCV(symbol, timeframe, cursor, 1000);
    if (!batch.length) break;
    all.push(...batch);
    cursor = batch[batch.length - 1][0] + 1;
    if (batch.length < 1000) break;
  }
  return all.slice(0, -1); // آخر شمعة لسا ما قفلت
}

module.exports = { simulate, stats, optimize, fetchHistory, FEE_PCT };
