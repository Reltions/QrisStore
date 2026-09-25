const test = require('node:test');
const assert = require('node:assert');
const { ema, rsi, computeIndicators, signalAt } = require('../src/strategy');
const { Trader } = require('../src/trader');
const { simulate } = require('../src/backtest');

const params = { emaFast: 3, emaSlow: 5, rsiPeriod: 3, rsiMax: 101 };

test('ema starts with SMA seed', () => {
  const out = ema([1, 2, 3, 4, 5], 3);
  assert.deepStrictEqual(out.slice(0, 2), [null, null]);
  assert.strictEqual(out[2], 2);
  assert.strictEqual(out[3], 3);
});

test('rsi is 100 when price only rises, 0 when it only falls', () => {
  assert.strictEqual(rsi([1, 2, 3, 4, 5], 3)[4], 100);
  assert.strictEqual(rsi([5, 4, 3, 2, 1], 3)[4], 0);
});

test('signalAt detects up and down crossings', () => {
  const closes = [10, 9, 8, 7, 6, 5, 4, 8, 12, 16, 12, 8, 4, 2];
  const ind = computeIndicators(closes, params);
  const signals = closes.map((_, i) => signalAt(ind, i, params));
  assert.ok(signals.includes('buy'));
  assert.ok(signals.includes('sell'));
  assert.ok(signals.indexOf('buy') < signals.lastIndexOf('sell'));
});

test('rsiMax filter blocks buys', () => {
  const closes = [10, 9, 8, 7, 6, 5, 4, 8, 12, 16];
  const ind = computeIndicators(closes, { ...params, rsiMax: 1 });
  assert.ok(!closes.some((_, i) => signalAt(ind, i, { ...params, rsiMax: 1 }) === 'buy'));
});

function fakeExchange(prices) {
  const ex = {
    price: prices[0],
    orders: [],
    balance: { BTC: { free: 0 }, USDT: { free: 1000 } },
    async fetchOHLCV() {
      // شمعة إضافية في الآخر تمثل الشمعة اللي لسا ما قفلت
      return [...prices, 0].map((p, i) => [i, p, p, p, p, 1]);
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

function makeTrader(exchange, overrides = {}) {
  return new Trader({
    exchange,
    config: {
      strategy: { symbol: 'BTC/USDT', timeframe: '15m', ...params },
      risk: { tradeAmountUsdt: 10, stopLossPct: 2, takeProfitPct: 4, maxDailyLossUsdt: 5, ...overrides },
    },
    state: { running: true, position: null, daily: { date: '', pnl: 0, limitNotified: false }, trades: [] },
    saveState: () => {},
  });
}

// أسعار تنتهي بتقاطع صاعد في آخر شمعة مقفلة
const BUY_SERIES = [10, 9, 8, 7, 6, 5, 4, 3, 10];

test('trader buys on signal, then stop loss sells', async () => {
  const ex = fakeExchange(BUY_SERIES);
  ex.price = 100;
  const trader = makeTrader(ex);
  await trader.tick();
  assert.ok(trader.state.position, 'should open a position');
  assert.strictEqual(ex.orders[0].side, 'buy');

  ex.price = 97; // -3% تحت وقف الخسارة 2%
  await trader.tick();
  assert.strictEqual(trader.state.position, null);
  assert.strictEqual(trader.state.trades.length, 1);
  assert.ok(trader.state.trades[0].pnl < 0);
  assert.ok(trader.state.daily.pnl < 0);
});

test('trader does not open when stopped', async () => {
  const ex = fakeExchange(BUY_SERIES);
  const trader = makeTrader(ex);
  trader.state.running = false;
  await trader.tick();
  assert.strictEqual(trader.state.position, null);
  assert.strictEqual(ex.orders.length, 0);
});

test('daily loss limit blocks new trades', async () => {
  const ex = fakeExchange(BUY_SERIES);
  const trader = makeTrader(ex);
  trader.state.daily = { date: new Date().toISOString().slice(0, 10), pnl: -6, limitNotified: false };
  const msgs = [];
  trader.notify = async (m) => msgs.push(m);
  await trader.tick();
  assert.strictEqual(ex.orders.length, 0);
  assert.strictEqual(msgs.length, 1);
});

test('backtest simulate takes profit and stops loss', () => {
  const up = [10, 9, 8, 7, 6, 5, 4, 3, 10, 10.1];
  const candles = up.map((p, i) => [i, p, i === 9 ? 100 : p, p, p, 1]);
  const trades = simulate(candles, params, { tradeAmountUsdt: 10, stopLossPct: 2, takeProfitPct: 4 });
  assert.strictEqual(trades.length, 1);
  assert.strictEqual(trades[0].reason, 'جني ربح');
  assert.ok(trades[0].pnl > 0);
});
