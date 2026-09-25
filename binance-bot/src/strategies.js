// الست استراتيجيات اللي يختبرها البوت ويختار أفضلها
// كل وحدة: prepare(series) تحسب المؤشرات مرة وحدة، و signal(ctx, i) ترجع 'buy' أو 'sell' أو 'hold'
// series = { open, high, low, close } مصفوفات
const I = require('./indicators');

const STRATEGIES = [
  {
    id: 'ema_cross',
    name: 'تقاطع EMA 9/21 مع فلتر RSI',
    idea: 'يدخل مع بداية الترند الصاعد، ويتجنب الشراء إذا السعر متضخم',
    prepare: (s) => ({ fast: I.ema(s.close, 9), slow: I.ema(s.close, 21), rsi: I.rsi(s.close, 14) }),
    signal: (x, i) => {
      if (I.crossUp(x.fast, x.slow, i) && x.rsi[i] < 70) return 'buy';
      if (I.crossDown(x.fast, x.slow, i)) return 'sell';
      return 'hold';
    },
  },
  {
    id: 'trend_follow',
    name: 'ترند طويل EMA 20/50',
    idea: 'أبطأ من الأولى، يمسك الترندات الكبيرة ويتجاهل الحركات الصغيرة',
    prepare: (s) => ({ fast: I.ema(s.close, 20), slow: I.ema(s.close, 50) }),
    signal: (x, i) => {
      if (I.crossUp(x.fast, x.slow, i)) return 'buy';
      if (I.crossDown(x.fast, x.slow, i)) return 'sell';
      return 'hold';
    },
  },
  {
    id: 'rsi_bounce',
    name: 'ارتداد RSI من التشبع البيعي',
    idea: 'يشتري لما السعر ينزل بقوة ويبدأ يرتد، ويبيع بعد ما يتعافى',
    prepare: (s) => ({ rsi: I.rsi(s.close, 14) }),
    signal: (x, i) => {
      const r = x.rsi;
      if (i < 1 || r[i] === null || r[i - 1] === null) return 'hold';
      if (r[i - 1] < 30 && r[i] >= 30) return 'buy';
      if (r[i] >= 60) return 'sell';
      return 'hold';
    },
  },
  {
    id: 'bollinger',
    name: 'ارتداد من حد Bollinger السفلي',
    idea: 'يشتري لما السعر يطلع تحت النطاق الطبيعي ويرجع له، ويبيع عند المتوسط',
    prepare: (s) => ({ close: s.close, bb: I.bollinger(s.close, 20, 2) }),
    signal: (x, i) => {
      const { lower, mid } = x.bb;
      if (i < 1 || lower[i] === null || lower[i - 1] === null) return 'hold';
      if (x.close[i - 1] < lower[i - 1] && x.close[i] > lower[i]) return 'buy';
      if (x.close[i] >= mid[i]) return 'sell';
      return 'hold';
    },
  },
  {
    id: 'breakout',
    name: 'اختراق أعلى سعر (Donchian 20)',
    idea: 'يشتري لما السعر يكسر أعلى سعر في آخر 20 شمعة، ويبيع لو كسر أقل سعر في آخر 10',
    prepare: (s) => s,
    signal: (s, i) => {
      const hi = I.highestBefore(s.high, 20, i);
      const lo = I.lowestBefore(s.low, 10, i);
      if (hi === null || lo === null) return 'hold';
      if (s.close[i] > hi) return 'buy';
      if (s.close[i] < lo) return 'sell';
      return 'hold';
    },
  },
  {
    id: 'macd_trend',
    name: 'MACD مع فلتر الترند (EMA 100)',
    idea: 'يشتري على زخم صاعد بس إذا السوق أصلًا فوق متوسطه الطويل',
    prepare: (s) => ({ close: s.close, m: I.macd(s.close, 12, 26, 9), trend: I.ema(s.close, 100) }),
    signal: (x, i) => {
      if (x.trend[i] === null) return 'hold';
      if (I.crossUp(x.m.line, x.m.signal, i) && x.close[i] > x.trend[i]) return 'buy';
      if (I.crossDown(x.m.line, x.m.signal, i)) return 'sell';
      return 'hold';
    },
  },
];

// أكبر عدد شموع تحتاجه أي استراتيجية عشان تكتمل مؤشراتها
const WARMUP = 150;

function getStrategy(id) {
  return STRATEGIES.find((s) => s.id === id) || null;
}

// candles من ccxt: [time, open, high, low, close, volume]
function toSeries(candles) {
  return {
    time: candles.map((c) => c[0]),
    open: candles.map((c) => c[1]),
    high: candles.map((c) => c[2]),
    low: candles.map((c) => c[3]),
    close: candles.map((c) => c[4]),
  };
}

// الإشارة على آخر شمعة
function evaluate(strategy, candles) {
  const ctx = strategy.prepare(toSeries(candles));
  return strategy.signal(ctx, candles.length - 1);
}

module.exports = { STRATEGIES, WARMUP, getStrategy, toSeries, evaluate };
