const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'data', 'state.json');

const DEFAULT_STATE = {
  running: false,
  position: null, // { amount, entryPrice, cost, openedAt }
  daily: { date: '', pnl: 0, limitNotified: false },
  trades: [], // آخر الصفقات المقفلة
  optimization: null, // آخر نتيجة لاختبار الست استراتيجيات
};

function load() {
  try {
    return { ...DEFAULT_STATE, ...JSON.parse(fs.readFileSync(FILE, 'utf8')) };
  } catch {
    return structuredClone(DEFAULT_STATE);
  }
}

function save(state) {
  fs.mkdirSync(path.dirname(FILE), { recursive: true });
  const tmp = `${FILE}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2));
  fs.renameSync(tmp, FILE);
}

module.exports = { load, save };
