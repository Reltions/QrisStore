// يختبر الست استراتيجيات على أسعار Binance الحقيقية ويطلع جدول مقارنة
// الاستخدام: npm run backtest -- --days 60
const config = require('./config');
const { createPublicExchange } = require('./exchange');
const { fetchHistory, optimize } = require('./optimizer');
const { getStrategy } = require('./strategies');

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : fallback;
}

const f = (n) => (Number.isFinite(n) ? n.toFixed(2) : '∞');
const pad = (s, n) => String(s).padEnd(n);

function printReport(opt) {
  console.log(`\nالفترة: ${opt.from.slice(0, 10)} → ${opt.to.slice(0, 10)} (${opt.candles} شمعة)`);
  console.log(`لو اشتريت وخليتها: ${f(opt.buyAndHoldPct)}%\n`);
  console.log(
    `${pad('#', 3)}${pad('الاستراتيجية', 36)}${pad('تدريب %', 10)}${pad('تحقق %', 10)}${pad('صفقات', 8)}${pad('فوز %', 8)}${pad('PF', 7)}نتيجة`
  );
  console.log('-'.repeat(92));
  opt.results.forEach((r, i) => {
    const tag = r.id === opt.bestId ? '⭐ المختارة' : r.qualified ? '✅' : '❌';
    console.log(
      `${pad(i + 1, 3)}${pad(r.name, 36)}${pad(f(r.train.pnlPct), 10)}${pad(f(r.test.pnlPct), 10)}${pad(r.test.trades, 8)}${pad(f(r.test.winRate), 8)}${pad(f(r.test.profitFactor), 7)}${tag}`
    );
  });
  console.log('-'.repeat(92));
  console.log('تدريب/تحقق %: الربح كنسبة من مبلغ الصفقة. الأعمدة الباقية لفترة التحقق (آخر 30%).');
  console.log('PF = مجموع الأرباح ÷ مجموع الخسائر (فوق 1 = رابح).\n');

  if (opt.bestId) {
    const s = getStrategy(opt.bestId);
    console.log(`⭐ الأفضل: ${s.name}\n   ${s.idea}`);
  } else {
    console.log('⛔ ولا استراتيجية ربحت في الفترتين. البوت بيوقف عن فتح صفقات (وهذا أحسن من الخسارة).');
  }
  console.log('\nتذكير: النتائج القديمة ما تضمن المستقبل.\n');
}

async function main() {
  const days = Number(arg('days', config.optimize.days));
  const { symbol, timeframe } = config.strategy;
  const exchange = createPublicExchange();
  console.log(`جاري تحميل ${days} يوم من ${symbol} ${timeframe} ...`);
  const candles = await fetchHistory(exchange, symbol, timeframe, days);
  if (candles.length < 300) throw new Error(`البيانات قليلة (${candles.length} شمعة). زود --days`);
  printReport(optimize(candles, config.risk));
}

if (require.main === module) {
  main().catch((err) => {
    console.error('❌', err.message);
    process.exit(1);
  });
}

module.exports = { printReport };
