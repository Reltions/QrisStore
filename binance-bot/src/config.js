require('dotenv').config();
const { getStrategy } = require('./strategies');

function num(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error(`${name} لازم يكون رقم، القيمة الحالية: ${raw}`);
  return value;
}

const config = {
  binance: {
    apiKey: process.env.BINANCE_API_KEY || '',
    secret: process.env.BINANCE_API_SECRET || '',
    testnet: (process.env.TESTNET || 'true').toLowerCase() !== 'false',
    confirmLive: process.env.CONFIRM_LIVE === 'YES',
  },
  discord: {
    token: process.env.DISCORD_TOKEN || '',
    guildId: process.env.GUILD_ID || '',
    ownerId: process.env.OWNER_ID || '',
    notifyChannelId: process.env.NOTIFY_CHANNEL_ID || '',
  },
  strategy: {
    symbol: process.env.SYMBOL || 'BTC/USDT',
    timeframe: process.env.TIMEFRAME || '15m',
    // auto = يختبر الست ويختار الأفضل، أو اكتب id وحدة معينة
    mode: process.env.STRATEGY || 'auto',
  },
  optimize: {
    days: num('OPTIMIZE_DAYS', 60),
    everyHours: num('OPTIMIZE_EVERY_HOURS', 24),
  },
  risk: {
    tradeAmountUsdt: num('TRADE_AMOUNT_USDT', 10),
    stopLossPct: num('STOP_LOSS_PCT', 2),
    takeProfitPct: num('TAKE_PROFIT_PCT', 4),
    maxDailyLossUsdt: num('MAX_DAILY_LOSS_USDT', 5),
  },
  loopSeconds: num('LOOP_SECONDS', 60),
};

if (config.strategy.mode !== 'auto' && !getStrategy(config.strategy.mode)) {
  throw new Error(`STRATEGY غير معروفة: ${config.strategy.mode}`);
}

module.exports = config;
