// مؤشرات فنية. كل دالة ترجع مصفوفة بنفس طول المدخلات، و null قبل ما يكتمل المؤشر

function sma(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function ema(values, period) {
  const out = new Array(values.length).fill(null);
  const start = values.findIndex((v) => v !== null);
  if (start < 0 || values.length - start < period) return out;
  const k = 2 / (period + 1);
  let prev = 0;
  for (let i = start; i < start + period; i++) prev += values[i];
  prev /= period;
  out[start + period - 1] = prev;
  for (let i = start + period; i < values.length; i++) {
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
  const calc = () => (loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
  out[period] = calc();
  for (let i = period + 1; i < values.length; i++) {
    const diff = values[i] - values[i - 1];
    gain = (gain * (period - 1) + Math.max(diff, 0)) / period;
    loss = (loss * (period - 1) + Math.max(-diff, 0)) / period;
    out[i] = calc();
  }
  return out;
}

function bollinger(values, period, mult) {
  const mid = sma(values, period);
  const upper = new Array(values.length).fill(null);
  const lower = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) sq += (values[j] - mid[i]) ** 2;
    const sd = Math.sqrt(sq / period);
    upper[i] = mid[i] + mult * sd;
    lower[i] = mid[i] - mult * sd;
  }
  return { mid, upper, lower };
}

function macd(values, fast, slow, signalPeriod) {
  const f = ema(values, fast);
  const s = ema(values, slow);
  const line = values.map((_, i) => (f[i] === null || s[i] === null ? null : f[i] - s[i]));
  return { line, signal: ema(line, signalPeriod) };
}

// أعلى قيمة في آخر period شمعة قبل i (بدون i نفسها)
function highestBefore(values, period, i) {
  if (i < period) return null;
  let m = -Infinity;
  for (let j = i - period; j < i; j++) m = Math.max(m, values[j]);
  return m;
}

function lowestBefore(values, period, i) {
  if (i < period) return null;
  let m = Infinity;
  for (let j = i - period; j < i; j++) m = Math.min(m, values[j]);
  return m;
}

function crossUp(a, b, i) {
  if (i < 1 || [a[i], b[i], a[i - 1], b[i - 1]].some((v) => v === null)) return false;
  return a[i - 1] <= b[i - 1] && a[i] > b[i];
}

function crossDown(a, b, i) {
  if (i < 1 || [a[i], b[i], a[i - 1], b[i - 1]].some((v) => v === null)) return false;
  return a[i - 1] >= b[i - 1] && a[i] < b[i];
}

module.exports = { sma, ema, rsi, bollinger, macd, highestBefore, lowestBefore, crossUp, crossDown };
