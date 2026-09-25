const { evaluate, getStrategy, WARMUP } = require('./strategies');

const MAX_TRADES_KEPT = 50;

function today() {
  return new Date().toISOString().slice(0, 10);
}

function fmt(n, digits = 2) {
  return Number(n).toFixed(digits);
}

class Trader {
  constructor({ exchange, config, state, saveState, notify }) {
    this.exchange = exchange;
    this.config = config;
    this.state = state;
    this.saveState = saveState;
    this.notify = notify || (async () => {});
    this.busy = false;
    this.queue = Promise.resolve();
    this.tickPending = false;
    this.lastSignal = null;
    this.lastPrice = null;
    this.lastPriceAt = 0;
    this.lastErrorNotifyAt = 0;
    // الاستراتيجية الحالية. null = ما فيه استراتيجية ناجحة، فما يفتح صفقات جديدة
    this.strategy = null;
    if (config.strategy.mode !== 'auto') this.strategy = getStrategy(config.strategy.mode);
    else if (state.optimization?.bestId) this.strategy = getStrategy(state.optimization.bestId);
  }

  // يستقبل نتيجة الـ optimizer ويغير الاستراتيجية لو لزم
  async applyOptimization(opt) {
    const prevId = this.strategy?.id ?? null;
    this.state.optimization = opt;
    if (this.config.strategy.mode === 'auto') this.strategy = opt.bestId ? getStrategy(opt.bestId) : null;
    this.saveState(this.state);

    const newId = this.strategy?.id ?? null;
    if (newId === prevId) return;
    if (this.strategy) {
      const r = opt.results.find((x) => x.id === newId);
      await this.notify(
        `🧠 **غيرت الاستراتيجية إلى:** ${this.strategy.name}\n${this.strategy.idea}\nنتيجتها في فترة التحقق: ${fmt(r.test.pnlPct)}% | فوز ${fmt(r.test.winRate)}% | ${r.test.trades} صفقة`
      );
    } else {
      await this.notify('⛔ ولا استراتيجية من الست نجحت على البيانات الأخيرة. وقفت فتح صفقات جديدة لين تتحسن الظروف.');
    }
  }

  get symbol() {
    return this.config.strategy.symbol;
  }

  get baseAsset() {
    return this.symbol.split('/')[0];
  }

  resetDailyIfNeeded() {
    const d = today();
    if (this.state.daily.date !== d) {
      this.state.daily = { date: d, pnl: 0, limitNotified: false };
    }
  }

  dailyLimitHit() {
    return this.state.daily.pnl <= -this.config.risk.maxDailyLossUsdt;
  }

  // كل العمليات اللي تشتري أو تبيع تمشي وحدة وحدة، عشان ما يصير بيع مرتين لنفس الصفقة
  exclusive(fn) {
    const run = this.queue.then(async () => {
      this.busy = true;
      try {
        return await fn();
      } finally {
        this.busy = false;
      }
    });
    this.queue = run.catch(() => {});
    return run;
  }

  // يرسل الأخطاء لديسكورد مرة كل 5 دقايق بالكثير (عشان ما يزعجك لو النت فصل)
  async reportError(where, err) {
    console.error(`[${where}]`, err.message);
    if (Date.now() - this.lastErrorNotifyAt < 5 * 60e3) return;
    this.lastErrorNotifyAt = Date.now();
    await this.notify(`⚠️ خطأ (${where}): ${err.message}`);
  }

  // يتنادى مع كل تحديث سعر لحظي (كل ثانية تقريبًا من WebSocket)
  // يفحص وقف الخسارة وجني الربح بس، لأن إشارات الاستراتيجية تنحسب على الشموع المقفلة
  async onPrice(price) {
    this.lastPrice = price;
    this.lastPriceAt = Date.now();
    if (this.busy || !this.state.position) return;
    try {
      await this.exclusive(() => this.checkExits(price, 'hold'));
    } catch (err) {
      await this.reportError('price', err);
    }
  }

  // يرجع true إذا باع
  async checkExits(price, signal) {
    const pos = this.state.position;
    if (!pos) return false;
    const changePct = ((price - pos.entryPrice) / pos.entryPrice) * 100;
    if (changePct <= -this.config.risk.stopLossPct) {
      await this.closePosition(`وقف خسارة (${fmt(changePct)}%)`);
    } else if (changePct >= this.config.risk.takeProfitPct) {
      await this.closePosition(`جني ربح (+${fmt(changePct)}%)`);
    } else if (signal === 'sell') {
      await this.closePosition('إشارة بيع');
    } else {
      return false;
    }
    return true;
  }

  // دورة كاملة: يحسب إشارة الاستراتيجية من الشموع ويقرر يشتري أو يبيع
  async tick() {
    if (this.tickPending) return;
    this.tickPending = true;
    try {
      await this.exclusive(() => this.runTick());
    } catch (err) {
      await this.reportError('tick', err);
    } finally {
      this.tickPending = false;
    }
  }

  async runTick() {
    this.resetDailyIfNeeded();
    const { symbol } = this;
    const { timeframe } = this.config.strategy;

    const candles = await this.exchange.fetchOHLCV(symbol, timeframe, undefined, WARMUP * 2);
    // آخر شمعة لسا ما قفلت، نتجاهلها عشان الإشارة ما تتغير
    const closed = candles.slice(0, -1);
    const signal = this.strategy ? evaluate(this.strategy, closed) : 'hold';
    this.lastSignal = {
      signal,
      strategyId: this.strategy?.id ?? null,
      candle: closed.length ? new Date(closed[closed.length - 1][0]).toISOString() : null,
      at: new Date().toISOString(),
    };

    const ticker = await this.exchange.fetchTicker(symbol);
    const price = ticker.last;
    this.lastPrice = price;
    this.lastPriceAt = Date.now();

    if (this.state.position) {
      await this.checkExits(price, signal);
    } else if (this.state.running && signal === 'buy') {
      // ما نشتري مرتين على نفس الشمعة
      if (this.state.lastBuyCandle === this.lastSignal.candle) return;
      if (this.dailyLimitHit()) {
        if (!this.state.daily.limitNotified) {
          this.state.daily.limitNotified = true;
          this.saveState(this.state);
          await this.notify(
            `⛔ وصلت حد الخسارة اليومي (${fmt(this.state.daily.pnl)} USDT). ما راح أفتح صفقات جديدة لين بكرة.`
          );
        }
      } else {
        this.state.lastBuyCandle = this.lastSignal.candle;
        await this.openPosition(price);
      }
    }
  }

  // بيع يدوي (من ديسكورد)
  closeNow(reason) {
    return this.exclusive(() => this.closePosition(reason));
  }

  async openPosition(price) {
    const cost = this.config.risk.tradeAmountUsdt;
    const order = await this.exchange.createMarketBuyOrderWithCost(this.symbol, cost);
    const amount = order.filled;
    const entryPrice = order.average || price;
    if (!amount) throw new Error('أمر الشراء ما تنفذ');

    this.state.position = {
      amount,
      entryPrice,
      cost: order.cost || amount * entryPrice,
      openedAt: new Date().toISOString(),
    };
    this.saveState(this.state);
    await this.notify(
      `🟢 **شراء** ${this.symbol}\nالكمية: ${amount}\nالسعر: ${fmt(entryPrice, 4)}\nالمبلغ: ${fmt(this.state.position.cost)} USDT`
    );
  }

  async closePosition(reason) {
    const pos = this.state.position;
    if (!pos) return null;

    // نبيع الأقل بين كمية الصفقة والرصيد المتاح (بسبب العمولة ممكن يكون أقل شوي)
    const balance = await this.exchange.fetchBalance();
    const free = balance[this.baseAsset]?.free ?? pos.amount;
    const amount = Number(this.exchange.amountToPrecision(this.symbol, Math.min(pos.amount, free)));
    const order = await this.exchange.createMarketSellOrder(this.symbol, amount);

    const exitPrice = order.average || order.price;
    const proceeds = order.cost || amount * exitPrice;
    const pnl = proceeds - pos.cost;

    this.resetDailyIfNeeded();
    this.state.daily.pnl += pnl;
    this.state.trades.unshift({
      entryPrice: pos.entryPrice,
      exitPrice,
      amount,
      pnl,
      reason,
      openedAt: pos.openedAt,
      closedAt: new Date().toISOString(),
    });
    this.state.trades = this.state.trades.slice(0, MAX_TRADES_KEPT);
    this.state.position = null;
    this.saveState(this.state);

    const icon = pnl >= 0 ? '✅' : '🔴';
    await this.notify(
      `${icon} **بيع** ${this.symbol} (${reason})\nسعر الدخول: ${fmt(pos.entryPrice, 4)}\nسعر الخروج: ${fmt(exitPrice, 4)}\nالربح/الخسارة: ${fmt(pnl)} USDT\nمجموع اليوم: ${fmt(this.state.daily.pnl)} USDT`
    );
    return pnl;
  }

  setRunning(running) {
    this.state.running = running;
    this.saveState(this.state);
  }
}

module.exports = { Trader, fmt };
