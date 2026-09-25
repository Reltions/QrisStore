const test = require('node:test');
const assert = require('node:assert');
const I = require('../src/indicators');
const { STRATEGIES, getStrategy, evaluate } = require('../src/strategies');
const { simulate, stats, optimize } = require('../src/optimizer');
const { Trader } = require('../src/trader');

const candle = (i, p, { high = p, low = p } = {}) => [i * 60e3, p, high, low, p, 1];
const toCandles = (prices) => prices.map((p, i) => candle(i, p));

// أسعار صناعية فيها ترندات صاعدة وهابطة وتذبذب
function wave(n) {
  return Array.from({ length: n }, (_, i) => 100 + 15 * Math.sin(i / 25) + 5 * Math.sin(i / 4));
}

test('ema starts with SMA seed', () => {
  const out = I.ema([1, 2, 3, 4, 5], 3);
  assert.deepStrictEqual(out.slice(0, 2), [null, null]);
  assert.strictEqual(out[2], 2);
  assert.strictEqual(out[3], 3);
});

test('ema skips leading nulls (used by MACD signal line)', () => {
  const out = I.ema([null, null, 1, 2, 3, 4], 3);
  assert.strictEqual(out[4], 2);
});

test('rsi is 100 when price only rises, 0 when it only falls', () => {
  assert.strictEqual(I.rsi([1, 2, 3, 4, 5], 3)[4], 100);
  assert.strictEqual(I.rsi([5, 4, 3, 2, 1], 3)[4], 0);
});

test('bollinger bands surround the mean', () => {
  const { mid, upper, lower } = I.bollinger([1, 2, 3, 4, 5], 5, 2);
  assert.strictEqual(mid[4], 3);
  assert.ok(upper[4] > 3 && lower[4] < 3);
});

test('there are 6 strategies with unique ids', () => {
  assert.strictEqual(STRATEGIES.length, 6);
  assert.strictEqual(new Set(STRATEGIES.map((s) => s.id)).size, 6);
});

test('every strategy produces both buy and sell signals on a wavy market', () => {
  const candles = wave(1500).map((p, i) => candle(i, p, { high: p * 1.002, low: p * 0.998 }));
  for (const s of STRATEGIES) {
    const trades = simulate(candles, s, { tradeAmountUsdt: 10, stopLossPct: 50, takeProfitPct: 50 });
    assert.ok(trades.length > 0, `${s.id} made no trades`);
    assert.ok(trades.some((t) => t.reason === 'إشارة بيع'), `${s.id} never sold on signal`);
  }
});

test('breakout buys when price breaks the 20-candle high', () => {
  const prices = [...Array(25).fill(100), 110];
  assert.strictEqual(evaluate(getStrategy('breakout'), toCandles(prices)), 'buy');
});

test('simulate assumes the worst when a candle hits both stop loss and take profit', () => {
  const prices = [...Array(25).fill(100), 110, 110];
  const candles = toCandles(prices);
  candles[26] = candle(26, 110, { high: 200, low: 1 });
  const trades = simulate(candles, getStrategy('breakout'), { tradeAmountUsdt: 10, stopLossPct: 2, takeProfitPct: 4 });
  assert.strictEqual(trades[0].reason, 'وقف خسارة');
  assert.ok(trades[0].pnl < 0);
});

test('stats computes win rate and drawdown', () => {
  const s = stats([{ pnl: 2 }, { pnl: -1 }, { pnl: -1 }, { pnl: 3 }], 10);
  assert.strictEqual(s.winRate, 50);
  assert.strictEqual(s.pnl, 3);
  assert.strictEqual(s.maxDrawdown, 2);
  assert.strictEqual(s.profitFactor, 2.5);
});

test('optimize ranks all 6 and only picks a strategy that wins in both periods', () => {
  const candles = wave(3000).map((p, i) => candle(i, p, { high: p * 1.002, low: p * 0.998 }));
  const opt = optimize(candles, { tradeAmountUsdt: 10, stopLossPct: 2, takeProfitPct: 4 });
  assert.strictEqual(opt.results.length, 6);
  if (opt.bestId) {
    const best = opt.results[0];
    assert.strictEqual(best.id, opt.bestId);
    assert.ok(best.train.pnl > 0 && best.test.pnl > 0);
  }
});

test('optimize picks nothing in a market that only falls', () => {
  const prices = Array.from({ length: 2000 }, (_, i) => 1000 * 0.999 ** i + 3 * Math.sin(i / 3));
  const opt = optimize(toCandles(prices), { tradeAmountUsdt: 10, stopLossPct: 2, takeProfitPct: 4 });
  assert.strictEqual(opt.bestId, null);
});

// ---------- Trader ----------

function fakeExchange(closes) {
  const ex = {
    price: closes[closes.length - 1],
    orders: [],
    balance: { BTC: { free: 0 }, USDT: { free: 1000 } },
    async fetchOHLCV() {
      // شمعة إضافية في الآخر تمثل الشمعة اللي لسا ما قفلت
      return [...toCandles(closes), candle(closes.length, 0)];
    },
    async fetchTicker() {
      return { last: ex.price };
    },
    async fetchBalance() {
      return ex.balance;
    },
    amountToPrecision(_s, a) {
      return String(a);
    },
    async createMarketBuyOrderWithCost(_s, cost) {
      const filled = cost / ex.price;
      ex.balance.BTC.free += filled;
      ex.orders.push({ side: 'buy', cost });
      return { filled, average: ex.price, cost };
    },
    async createMarketSellOrder(_s, amount) {
      ex.balance.BTC.free -= amount;
      ex.orders.push({ side: 'sell', amount });
      return { filled: amount, average: ex.price, cost: amount * ex.price };
    },
  };
  return ex;
}

function makeTrader(exchange, mode = 'breakout') {
  return new Trader({
    exchange,
    config: {
      strategy: { symbol: 'BTC/USDT', timeframe: '15m', mode },
      risk: { tradeAmountUsdt: 10, stopLossPct: 2, takeProfitPct: 4, maxDailyLossUsdt: 5 },
    },
    state: { running: true, position: null, daily: { date: '', pnl: 0, limitNotified: false }, trades: [], optimization: null },
    saveState: () => {},
  });
}

// آخر شمعة مقفلة تكسر أعلى سعر، يعني إشارة شراء لاستراتيجية الاختراق
const BREAKOUT = [...Array(25).fill(100), 110];

test('trader buys on signal, then stop loss sells', async () => {
  const ex = fakeExchange(BREAKOUT);
  const trader = makeTrader(ex);
  await trader.tick();
  assert.ok(trader.state.position, 'should open a position');
  assert.strictEqual(ex.orders[0].side, 'buy');

  ex.price = 106; // -3.6% تحت وقف الخسارة 2%
  await trader.tick();
  assert.strictEqual(trader.state.position, null);
  assert.strictEqual(trader.state.trades.length, 1);
  assert.ok(trader.state.trades[0].pnl < 0);
  assert.ok(trader.state.daily.pnl < 0);
});

test('trader does not open when stopped', async () => {
  const ex = fakeExchange(BREAKOUT);
  const trader = makeTrader(ex);
  trader.state.running = false;
  await trader.tick();
  assert.strictEqual(ex.orders.length, 0);
});

test('daily loss limit blocks new trades', async () => {
  const ex = fakeExchange(BREAKOUT);
  const trader = makeTrader(ex);
  trader.state.daily = { date: new Date().toISOString().slice(0, 10), pnl: -6, limitNotified: false };
  const msgs = [];
  trader.notify = async (m) => msgs.push(m);
  await trader.tick();
  assert.strictEqual(ex.orders.length, 0);
  assert.strictEqual(msgs.length, 1);
});

test('auto mode: no winning strategy means no new trades', async () => {
  const ex = fakeExchange(BREAKOUT);
  const trader = makeTrader(ex, 'auto');
  const msgs = [];
  trader.notify = async (m) => msgs.push(m);
  await trader.applyOptimization({ bestId: null, results: [] });
  await trader.tick();
  assert.strictEqual(ex.orders.length, 0);
});

test('auto mode switches to the best strategy and trades with it', async () => {
  const ex = fakeExchange(BREAKOUT);
  const trader = makeTrader(ex, 'auto');
  const msgs = [];
  trader.notify = async (m) => msgs.push(m);
  const r = { id: 'breakout', test: { pnlPct: 5, winRate: 60, trades: 5 } };
  await trader.applyOptimization({ bestId: 'breakout', results: [r] });
  assert.strictEqual(trader.strategy.id, 'breakout');
  assert.ok(msgs[0].includes('غيرت الاستراتيجية'));
  await trader.tick();
  assert.strictEqual(ex.orders[0].side, 'buy');
});
