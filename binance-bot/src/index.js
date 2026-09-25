const config = require('./config');
const { createExchange, createPublicExchange } = require('./exchange');
const { Trader } = require('./trader');
const stateStore = require('./state');
const { startDiscord } = require('./discord');
const { fetchHistory, optimize } = require('./optimizer');
const { startPriceFeed, onEveryCandleClose } = require('./priceFeed');

async function main() {
  const exchange = createExchange(config.binance);
  await exchange.loadMarkets();
  if (!exchange.markets[config.strategy.symbol]) {
    throw new Error(`الزوج ${config.strategy.symbol} مو موجود في Binance`);
  }

  const trader = new Trader({
    exchange,
    config,
    state: stateStore.load(),
    saveState: stateStore.save,
  });

  // الاختبار يكون على أسعار السوق الحقيقي حتى لو التداول على Testnet،
  // لأن أسعار Testnet ما تمثل السوق
  const market = createPublicExchange();
  async function runOptimization() {
    const { symbol, timeframe } = config.strategy;
    const candles = await fetchHistory(market, symbol, timeframe, config.optimize.days);
    const opt = optimize(candles, config.risk);
    await trader.applyOptimization(opt);
    return opt;
  }

  if (config.discord.token) {
    const { notify } = await startDiscord({ config, trader, runOptimization });
    trader.notify = notify;
  } else {
    // بدون ديسكورد: يشتغل ويطبع كل شي في الـ console
    console.log('DISCORD_TOKEN فاضي، البوت بيشتغل بدون ديسكورد والتداول التلقائي شغال.');
    trader.notify = async (text) => console.log(text.replace(/\*\*/g, ''));
    trader.setRunning(true);
  }

  const { symbol, timeframe } = config.strategy;
  console.log(
    `الوضع: ${config.binance.testnet ? 'Testnet' : 'حقيقي'} | ${symbol} ${timeframe}\n` +
      `  - السعر اللحظي (WebSocket): كل ثانية تقريبًا، يفحص وقف الخسارة وجني الربح\n` +
      `  - إشارة الاستراتيجية: فور ما تقفل كل شمعة ${timeframe}، ومعها فحص احتياطي كل ${config.loopSeconds} ثانية\n` +
      `  - اختيار أفضل استراتيجية: كل ${config.optimize.everyHours} ساعة`
  );

  const safeOptimize = () =>
    runOptimization().catch((err) => trader.reportError('optimize', err));

  if (config.strategy.mode === 'auto') {
    console.log(`جاري اختبار الست استراتيجيات على آخر ${config.optimize.days} يوم ...`);
    await safeOptimize();
    setInterval(safeOptimize, config.optimize.everyHours * 3600e3);
  }

  await trader.tick();
  onEveryCandleClose(exchange.parseTimeframe(timeframe) * 1000, () => trader.tick());
  setInterval(() => trader.tick(), config.loopSeconds * 1000);

  startPriceFeed({
    config,
    onPrice: (price) => trader.onPrice(price),
    onStatus: (msg) => trader.notify(msg),
  });
}

main().catch((err) => {
  console.error('❌', err.message);
  process.exit(1);
});
