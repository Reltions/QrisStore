const ccxt = require('ccxt');

function createExchange(cfg) {
  if (!cfg.testnet && !cfg.confirmLive) {
    throw new Error(
      'TESTNET=false بدون CONFIRM_LIVE=YES. للتداول بفلوس حقيقية لازم تحط CONFIRM_LIVE=YES في ملف .env'
    );
  }
  if (!cfg.apiKey || !cfg.secret) {
    throw new Error('حط BINANCE_API_KEY و BINANCE_API_SECRET في ملف .env');
  }

  const exchange = new ccxt.binance({
    apiKey: cfg.apiKey,
    secret: cfg.secret,
    enableRateLimit: true,
    options: { defaultType: 'spot' },
  });
  if (cfg.testnet) exchange.setSandboxMode(true);
  return exchange;
}

// اتصال بدون مفاتيح لقراءة الأسعار فقط (للـ backtest)
function createPublicExchange() {
  return new ccxt.binance({ enableRateLimit: true, options: { defaultType: 'spot' } });
}

module.exports = { createExchange, createPublicExchange };
