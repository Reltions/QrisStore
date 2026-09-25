// يختبر الاستراتيجية على أسعار حقيقية قديمة من Binance (ما يحتاج مفاتيح ولا فلوس)
// الاستخدام: npm run backtest -- --days 90
const config = require('./config');
const { createPublicExchange } = require('./exchange');
const { computeIndicators, signalAt } = require('./strategy');

const FEE_PCT = 0.1; // عمولة Binance لكل عملية

const TIMEFRAME_MS = {
  '1m': 60e3, '5m': 300e3, '15m': 900e3, '30m': 1800e3,
  '1h': 3600e3, '4h': 14400e3, '1d': 86400e3,
};

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
}

async function fetchHistory(exchange, symbol, timeframe, since) {
  const all = [];
  let cursor = since;
  for (;;) {
    const batch = await exchange.fetchOHLCV(symbol, timeframe, cursor, 1000);
    if (!batch.length) break;
    all.push(...batch);
    cursor = batch[batch.length - 1][0] + 1;
    if (batch.length < 1000) break;
  }
  return all;
}

// المحاكاة: نفس منطق trader.js، الشراء والبيع على سعر الإغلاق
// ووقف الخسارة/جني الربح يتفعل إذا لمس السعر المستوى داخل الشمعة
function simulate(candles, strategy, risk) {
  const closes = candles.map((c) => c[4]);
  const ind = computeIndicators(closes, strategy);
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
      else if (signalAt(ind, i, strategy) === 'sell') [exit, reason] = [close, 'إشارة بيع'];
      if (exit !== null) {
        const proceeds = pos.qty * exit * (1 - fee);
        trades.push({ openedAt: pos.ts, closedAt: ts, entry: pos.entry, exit, pnl: proceeds - pos.cost, reason });
        pos = null;
      }
    } else if (signalAt(ind, i, strategy) === 'buy') {
      const cost = risk.tradeAmountUsdt;
      pos = { ts, entry: close, cost, qty: (cost * (1 - fee)) / close };
    }
  }
  return trades;
}

function report(trades, candles, risk) {
  const wins = trades.filter((t) => t.pnl > 0);
  const losses = trades.filter((t) => t.pnl <= 0);
  const total = trades.reduce((a, t) => a + t.pnl, 0);
  const grossWin = wins.reduce((a, t) => a + t.pnl, 0);
  const grossLoss = -losses.reduce((a, t) => a + t.pnl, 0);

  let equity = 0;
  let peak = 0;
  let maxDd = 0;
  for (const t of trades) {
    equity += t.pnl;
    peak = Math.max(peak, equity);
    maxDd = Math.max(maxDd, peak - equity);
  }

  const first = candles[0][4];
  const last = candles[candles.length - 1][4];
  const holdPnl = risk.tradeAmountUsdt * (last / first) - risk.tradeAmountUsdt;
  const byReason = {};
  for (const t of trades) byReason[t.reason] = (byReason[t.reason] || 0) + 1;

  const f = (n) => n.toFixed(2);
  console.log('\n========== نتيجة الاختبار ==========');
  console.log(`عدد الصفقات:        ${trades.length}`);
  console.log(`رابحة / خاسرة:      ${wins.length} / ${losses.length}`);
  console.log(`نسبة الفوز:         ${trades.length ? f((wins.length / trades.length) * 100) : 0}%`);
  console.log(`متوسط الربح:        ${wins.length ? f(grossWin / wins.length) : 0} USDT`);
  console.log(`متوسط الخسارة:      ${losses.length ? f(grossLoss / losses.length) : 0} USDT`);
  console.log(`Profit factor:      ${grossLoss ? f(grossWin / grossLoss) : '∞'}  (فوق 1 = رابح)`);
  console.log(`أكبر نزول (DD):     ${f(maxDd)} USDT`);
  console.log(`أسباب الخروج:       ${JSON.stringify(byReason)}`);
  console.log(`----------------------------------`);
  console.log(`صافي البوت:         ${f(total)} USDT  (${f((total / risk.tradeAmountUsdt) * 100)}% من مبلغ الصفقة)`);
  console.log(`لو اشتريت وخليتها:  ${f(holdPnl)} USDT  (${f((last / first - 1) * 100)}%)`);
  console.log('===================================');
  console.log('تذكير: النتائج القديمة ما تضمن المستقبل.\n');
}

async function main() {
  const days = Number(arg('days', 90));
  const { symbol, timeframe } = config.strategy;
  if (!TIMEFRAME_MS[timeframe]) throw new Error(`TIMEFRAME غير مدعوم في الاختبار: ${timeframe}`);

  const exchange = createPublicExchange();
  const since = Date.now() - days * 86400e3;
  console.log(`جاري تحميل ${days} يوم من ${symbol} ${timeframe} ...`);
  const candles = await fetchHistory(exchange, symbol, timeframe, since);
  if (candles.length < config.strategy.emaSlow + 2) throw new Error('البيانات قليلة');
  console.log(`${candles.length} شمعة من ${new Date(candles[0][0]).toISOString().slice(0, 10)}`);

  const trades = simulate(candles, config.strategy, config.risk);
  report(trades, candles, config.risk);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('❌', err.message);
    process.exit(1);
  });
}

module.exports = { simulate };
