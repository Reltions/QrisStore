// الاستراتيجية: تقاطع متوسطين متحركين (EMA) مع فلتر RSI
// شراء: المتوسط السريع يقطع البطيء لفوق، و RSI تحت الحد (السعر مو متضخم)
// بيع: المتوسط السريع يقطع البطيء لتحت
// (وقف الخسارة وجني الربح يتعامل معها trader.js)

function ema(values, period) {
  const out = new Array(values.length).fill(null);
  if (values.length < period) return out;
  const k = 2 / (period + 1);
  let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period;
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

// RSI بطريقة Wilder
function rsi(values, period) {
  const out = new Array(values.length).fill(null);
  if (values.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = values[i] - values[i - 1];
    if (diff >= 0) gain += diff;
    else loss -= diff;
  }
  gain /= period;
  loss /= period;
  out[period] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  for (let i = period + 1; i < values.length; i++) {
    const diff = values[i] - values[i - 1];
    gain = (gain * (period - 1) + Math.max(diff, 0)) / period;
    loss = (loss * (period - 1) + Math.max(-diff, 0)) / period;
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  return out;
}

function computeIndicators(closes, params) {
  return {
    fast: ema(closes, params.emaFast),
    slow: ema(closes, params.emaSlow),
    rsi: rsi(closes, params.rsiPeriod),
  };
}

// الإشارة عند الشمعة رقم i: 'buy' أو 'sell' أو 'hold'
function signalAt(ind, i, params) {
  if (i < 1) return 'hold';
  const { fast, slow, rsi: r } = ind;
  if ([fast[i], slow[i], fast[i - 1], slow[i - 1], r[i]].some((v) => v === null)) return 'hold';
  const crossedUp = fast[i - 1] <= slow[i - 1] && fast[i] > slow[i];
  const crossedDown = fast[i - 1] >= slow[i - 1] && fast[i] < slow[i];
  if (crossedUp && r[i] < params.rsiMax) return 'buy';
  if (crossedDown) return 'sell';
  return 'hold';
}

function evaluate(closes, params) {
  const ind = computeIndicators(closes, params);
  const i = closes.length - 1;
  return {
    signal: signalAt(ind, i, params),
    fast: ind.fast[i],
    slow: ind.slow[i],
    rsi: ind.rsi[i],
  };
}

module.exports = { ema, rsi, computeIndicators, signalAt, evaluate };
