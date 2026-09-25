const {
  Client,
  GatewayIntentBits,
  SlashCommandBuilder,
  REST,
  Routes,
  MessageFlags,
} = require('discord.js');
const { fmt } = require('./trader');
const { getStrategy } = require('./strategies');

const commands = [
  new SlashCommandBuilder().setName('status').setDescription('حالة البوت والصفقة المفتوحة'),
  new SlashCommandBuilder().setName('price').setDescription('السعر الحالي'),
  new SlashCommandBuilder().setName('balance').setDescription('رصيدك'),
  new SlashCommandBuilder().setName('start').setDescription('تشغيل التداول التلقائي'),
  new SlashCommandBuilder().setName('stop').setDescription('إيقاف فتح صفقات جديدة'),
  new SlashCommandBuilder().setName('close').setDescription('بيع الصفقة المفتوحة الحين'),
  new SlashCommandBuilder().setName('trades').setDescription('آخر 10 صفقات'),
  new SlashCommandBuilder().setName('strategies').setDescription('نتيجة اختبار الست استراتيجيات'),
  new SlashCommandBuilder().setName('optimize').setDescription('أعد اختبار الست استراتيجيات الحين واختر الأفضل'),
].map((c) => c.setDMPermission(false).toJSON());

function strategiesTable(opt) {
  if (!opt) return 'لسا ما صار اختبار.';
  const lines = [
    `**آخر اختبار:** ${opt.from.slice(0, 10)} → ${opt.to.slice(0, 10)} | لو اشتريت وخليتها: ${fmt(opt.buyAndHoldPct)}%`,
    '',
  ];
  opt.results.forEach((r, i) => {
    const tag = r.id === opt.bestId ? '⭐' : r.qualified ? '✅' : '❌';
    lines.push(
      `${tag} **${i + 1}. ${r.name}**\n   تدريب ${fmt(r.train.pnlPct)}% | تحقق ${fmt(r.test.pnlPct)}% | ${r.test.trades} صفقة | فوز ${fmt(r.test.winRate)}%`
    );
  });
  if (!opt.bestId) lines.push('', '⛔ ولا وحدة نجحت، البوت ما يفتح صفقات جديدة.');
  return lines.join('\n');
}

async function startDiscord({ config, trader, runOptimization }) {
  const { token, guildId, ownerId, notifyChannelId } = config.discord;
  if (!ownerId) throw new Error('حط OWNER_ID في ملف .env عشان الأوامر تكون لك إنت بس');

  const client = new Client({ intents: [GatewayIntentBits.Guilds] });
  let channel = null;

  async function notify(text) {
    console.log(text.replace(/\*\*/g, ''));
    if (channel) await channel.send(text).catch((e) => console.error('[discord send]', e.message));
  }

  const handlers = {
    async status() {
      const s = trader.state;
      const sig = trader.lastSignal;
      const lines = [
        `**الوضع:** ${config.binance.testnet ? '🧪 Testnet (فلوس وهمية)' : '💰 حقيقي'}`,
        `**التداول التلقائي:** ${s.running ? '▶️ شغال' : '⏸️ موقف'}`,
        `**الزوج:** ${config.strategy.symbol} | ${config.strategy.timeframe}`,
        `**ربح/خسارة اليوم:** ${fmt(s.daily.pnl)} USDT`,
      ];
      lines.push(`**الاستراتيجية:** ${trader.strategy ? `${trader.strategy.name} ${config.strategy.mode === 'auto' ? '(مختارة تلقائيًا)' : '(ثابتة)'}` : '⛔ ما فيه استراتيجية ناجحة حاليًا'}`);
      if (sig) lines.push(`**آخر إشارة:** ${sig.signal} (شمعة ${sig.candle?.slice(11, 16) ?? '-'} UTC)`);
      if (trader.lastPrice) {
        const age = Math.round((Date.now() - trader.lastPriceAt) / 1000);
        lines.push(`**آخر سعر:** ${trader.lastPrice} (قبل ${age} ثانية)`);
      }
      if (s.position) {
        const ticker = await trader.exchange.fetchTicker(config.strategy.symbol);
        const change = ((ticker.last - s.position.entryPrice) / s.position.entryPrice) * 100;
        lines.push(
          `**صفقة مفتوحة:** ${s.position.amount} @ ${fmt(s.position.entryPrice, 4)} (الحين ${fmt(ticker.last, 4)}, ${fmt(change)}%)`
        );
      } else {
        lines.push('**صفقة مفتوحة:** لا');
      }
      return lines.join('\n');
    },

    async price() {
      const t = await trader.exchange.fetchTicker(config.strategy.symbol);
      return `${config.strategy.symbol}: **${t.last}** (24 ساعة: ${fmt(t.percentage ?? 0)}%)`;
    },

    async balance() {
      const b = await trader.exchange.fetchBalance();
      const [base, quote] = config.strategy.symbol.split('/');
      return `${quote}: **${fmt(b[quote]?.free ?? 0)}**\n${base}: **${b[base]?.free ?? 0}**`;
    },

    async start() {
      trader.setRunning(true);
      return '▶️ التداول التلقائي شغال.';
    },

    async stop() {
      trader.setRunning(false);
      return '⏸️ وقفت فتح صفقات جديدة. (لو فيه صفقة مفتوحة، وقف الخسارة وجني الربح شغالين عليها. استخدم /close لو تبي تبيعها الحين)';
    },

    async close() {
      if (!trader.state.position) return 'ما فيه صفقة مفتوحة.';
      const pnl = await trader.closeNow('بيع يدوي');
      return `تم البيع. الربح/الخسارة: ${fmt(pnl)} USDT`;
    },

    async strategies() {
      return strategiesTable(trader.state.optimization);
    },

    async optimize() {
      if (config.strategy.mode !== 'auto') {
        return `الاستراتيجية ثابتة (${getStrategy(config.strategy.mode).name}). خل STRATEGY=auto عشان يختار تلقائيًا.`;
      }
      return strategiesTable(await runOptimization());
    },

    async trades() {
      const list = trader.state.trades.slice(0, 10);
      if (!list.length) return 'ما فيه صفقات لسا.';
      return list
        .map(
          (t) =>
            `${t.pnl >= 0 ? '✅' : '🔴'} ${t.closedAt.slice(0, 16).replace('T', ' ')} | ${fmt(t.entryPrice, 4)} → ${fmt(t.exitPrice, 4)} | ${fmt(t.pnl)} USDT | ${t.reason}`
        )
        .join('\n');
    },
  };

  client.on('interactionCreate', async (interaction) => {
    if (!interaction.isChatInputCommand()) return;
    const handler = handlers[interaction.commandName];
    if (!handler) return;

    // حماية: الأوامر لصاحب البوت بس
    if (interaction.user.id !== ownerId) {
      await interaction.reply({ content: '⛔ هذا البوت خاص.', flags: MessageFlags.Ephemeral });
      return;
    }

    await interaction.deferReply({ flags: MessageFlags.Ephemeral });
    try {
      await interaction.editReply(await handler());
    } catch (err) {
      console.error(`[/${interaction.commandName}]`, err);
      await interaction.editReply(`⚠️ خطأ: ${err.message}`);
    }
  });

  client.once('clientReady', async () => {
    console.log(`Discord: متصل باسم ${client.user.tag}`);
    const rest = new REST().setToken(token);
    const route = guildId
      ? Routes.applicationGuildCommands(client.user.id, guildId)
      : Routes.applicationCommands(client.user.id);
    await rest.put(route, { body: commands });

    if (notifyChannelId) {
      channel = await client.channels.fetch(notifyChannelId).catch(() => null);
      if (!channel) console.warn('ما قدرت ألقى قناة NOTIFY_CHANNEL_ID');
    }
    await notify(
      `🤖 البوت اشتغل (${config.binance.testnet ? 'Testnet' : 'حقيقي'}). التداول التلقائي: ${trader.state.running ? 'شغال' : 'موقف، استخدم /start'}`
    );
  });

  await client.login(token);
  return { client, notify };
}

module.exports = { startDiscord };
