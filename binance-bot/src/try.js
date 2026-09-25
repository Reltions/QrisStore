// تجربة سريعة على Testnet: يتأكد من الاتصال، ويعرض السعر والرصيد والإشارة الحالية
// ومع --trade يشتري بـ 10 دولار وهمية ويبيعها بعد 10 ثواني
// الاستخدام: npm run try          أو   npm run try -- --trade
const config = require('./config');
const { createExchange } = require('./exchange');
const { evaluate } = require('./strategy');

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

  const candles = await exchange.fetchOHLCV(symbol, timeframe, undefined, 200);
  const closes = candles.slice(0, -1).map((c) => c[4]);
  const s = evaluate(closes, config.strategy);
  console.log(
    `4) الإشارة الحين: ${s.signal} | EMA ${s.fast.toFixed(2)} / ${s.slow.toFixed(2)} | RSI ${s.rsi.toFixed(1)}`
  );

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
