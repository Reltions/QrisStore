const config = require('./config');
const { createExchange, createPublicExchange } = require('./exchange');
const { Trader } = require('./trader');
const stateStore = require('./state');
const { startDiscord } = require('./discord');
const { fetchHistory, optimize } = require('./optimizer');

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

  console.log(
    `الوضع: ${config.binance.testnet ? 'Testnet' : 'حقيقي'} | ${config.strategy.symbol} ${config.strategy.timeframe} | كل ${config.loopSeconds} ثانية`
  );

  const safeOptimize = () =>
    runOptimization().catch((err) => trader.notify(`⚠️ فشل اختبار الاستراتيجيات: ${err.message}`));

  if (config.strategy.mode === 'auto') {
    console.log(`جاري اختبار الست استراتيجيات على آخر ${config.optimize.days} يوم ...`);
    await safeOptimize();
    setInterval(safeOptimize, config.optimize.everyHours * 3600e3);
  }

  await trader.tick();
  setInterval(() => trader.tick(), config.loopSeconds * 1000);
}

main().catch((err) => {
  console.error('❌', err.message);
  process.exit(1);
});
