// تجربة سريعة على Testnet: يتأكد من الاتصال، ويعرض السعر والرصيد وإشارة كل استراتيجية
// ومع --trade يشتري بـ 10 دولار وهمية ويبيعها بعد 10 ثواني
// الاستخدام: npm run try          أو   npm run try -- --trade
const config = require('./config');
const { createExchange } = require('./exchange');
const { STRATEGIES, WARMUP, evaluate } = require('./strategies');

async function main() {
  if (!config.binance.testnet) throw new Error('هذي التجربة للـ Testnet بس. خل TESTNET=true');

  const exchange = createExchange(config.binance);
  const { symbol, timeframe } = config.strategy;
  const [base, quote] = symbol.split('/');

  console.log('1) الاتصال بـ Binance Testnet ...');
  await exchange.loadMarkets();
  console.log('   ✅ متصل');

  const ticker = await exchange.fetchTicker(symbol);
  console.log(`2) سعر ${symbol}: ${ticker.last}`);

  const balance = await exchange.fetchBalance();
  console.log(`3) رصيدك: ${balance[quote]?.free ?? 0} ${quote} | ${balance[base]?.free ?? 0} ${base}`);

  const candles = (await exchange.fetchOHLCV(symbol, timeframe, undefined, WARMUP * 2)).slice(0, -1);
  console.log('4) إشارة كل استراتيجية الحين:');
  for (const st of STRATEGIES) console.log(`   ${evaluate(st, candles).padEnd(5)} ${st.name}`);

  if (!process.argv.includes('--trade')) {
    console.log('\nكل شي تمام. جرب: npm run try -- --trade  عشان يسوي صفقة تجريبية.');
    return;
  }

  const cost = config.risk.tradeAmountUsdt;
  console.log(`\n5) شراء بـ ${cost} ${quote} (وهمية) ...`);
  const buy = await exchange.createMarketBuyOrderWithCost(symbol, cost);
  console.log(`   ✅ اشترى ${buy.filled} ${base} بسعر ${buy.average}`);

  console.log('   ننتظر 10 ثواني ...');
  await new Promise((r) => setTimeout(r, 10_000));

  const free = (await exchange.fetchBalance())[base]?.free ?? buy.filled;
  const amount = Number(exchange.amountToPrecision(symbol, Math.min(buy.filled, free)));
  const sell = await exchange.createMarketSellOrder(symbol, amount);
  const pnl = sell.cost - buy.cost;
  console.log(`6) ✅ باع بسعر ${sell.average} | الربح/الخسارة: ${pnl.toFixed(4)} ${quote}`);
  console.log('\nالتجربة نجحت. البوت يقدر يشتري ويبيع.');
}

main().catch((err) => {
  console.error('❌', err.message);
  process.exit(1);
});
