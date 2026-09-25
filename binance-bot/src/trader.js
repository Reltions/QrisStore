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
    this.lastSignal = null;
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

  // دورة وحدة: يقرأ السوق ويقرر
  async tick() {
    if (this.busy) return;
    this.busy = true;
    try {
      this.resetDailyIfNeeded();
      const { symbol } = this;
      const { timeframe } = this.config.strategy;

      const candles = await this.exchange.fetchOHLCV(symbol, timeframe, undefined, WARMUP * 2);
      // آخر شمعة لسا ما قفلت، نتجاهلها عشان الإشارة ما تتغير
      const closed = candles.slice(0, -1);
      const signal = this.strategy ? evaluate(this.strategy, closed) : 'hold';
      this.lastSignal = { signal, strategyId: this.strategy?.id ?? null, at: new Date().toISOString() };

      const ticker = await this.exchange.fetchTicker(symbol);
      const price = ticker.last;
      const pos = this.state.position;

      if (pos) {
        const changePct = ((price - pos.entryPrice) / pos.entryPrice) * 100;
        if (changePct <= -this.config.risk.stopLossPct) {
          await this.closePosition(`وقف خسارة (${fmt(changePct)}%)`);
        } else if (changePct >= this.config.risk.takeProfitPct) {
          await this.closePosition(`جني ربح (+${fmt(changePct)}%)`);
        } else if (signal === 'sell') {
          await this.closePosition('إشارة بيع');
        }
      } else if (this.state.running && signal === 'buy') {
        if (this.dailyLimitHit()) {
          if (!this.state.daily.limitNotified) {
            this.state.daily.limitNotified = true;
            this.saveState(this.state);
            await this.notify(
              `⛔ وصلت حد الخسارة اليومي (${fmt(this.state.daily.pnl)} USDT). ما راح أفتح صفقات جديدة لين بكرة.`
            );
          }
        } else {
          await this.openPosition(price);
        }
      }
    } catch (err) {
      console.error('[tick]', err);
      await this.notify(`⚠️ خطأ: ${err.message}`);
    } finally {
      this.busy = false;
    }
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
