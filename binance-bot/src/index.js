const config = require('./config');
const { createExchange } = require('./exchange');
const { Trader } = require('./trader');
const stateStore = require('./state');
const { startDiscord } = require('./discord');

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

  if (config.discord.token) {
    const { notify } = await startDiscord({ config, trader });
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
  await trader.tick();
  setInterval(() => trader.tick(), config.loopSeconds * 1000);
}

main().catch((err) => {
  console.error('❌', err.message);
  process.exit(1);
});
