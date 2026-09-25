// سعر لحظي من Binance عن طريق WebSocket (تحديث كل ثانية تقريبًا)
// لو انقطع الاتصال يعيد يتصل تلقائيًا
const ccxt = require('ccxt');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function startPriceFeed({ config, onPrice, onStatus = () => {} }) {
  const ws = new ccxt.pro.binance({ enableRateLimit: true, options: { defaultType: 'spot' } });
  if (config.binance.testnet) ws.setSandboxMode(true);

  const { symbol } = config.strategy;
  let stopped = false;
  let failures = 0;

  (async () => {
    while (!stopped) {
      try {
        const ticker = await ws.watchTicker(symbol);
        if (failures) onStatus(`✅ رجع الاتصال بالسعر اللحظي`);
        failures = 0;
        if (ticker.last) await onPrice(ticker.last);
      } catch (err) {
        if (stopped) break;
        failures++;
        if (failures === 1) onStatus(`⚠️ انقطع السعر اللحظي (${err.message})، أحاول أرجع أتصل ...`);
        await sleep(Math.min(30_000, 1000 * 2 ** failures));
      }
    }
  })();

  return {
    async stop() {
      stopped = true;
      await ws.close();
    },
  };
}

// ينادي fn بعد ما تقفل كل شمعة بثانيتين (مثلًا 12:15:02، 12:30:02 ...)
// عشان الإشارة تنحسب فورًا على الشمعة الجديدة المقفلة
function onEveryCandleClose(timeframeMs, fn, delayMs = 2000) {
  const schedule = () => {
    const next = Math.floor(Date.now() / timeframeMs) * timeframeMs + timeframeMs + delayMs;
    setTimeout(async () => {
      await fn();
      schedule();
    }, next - Date.now());
  };
  schedule();
}

module.exports = { startPriceFeed, onEveryCandleClose };
