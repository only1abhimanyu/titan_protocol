/* Unit tests for the celebration + goals logic.
   Runs the real functions out of web/app.js against a stubbed DOM and localStorage. */
const fs = require('fs');
const src = fs.readFileSync('web/app.js', 'utf8');
const block = src.slice(src.indexOf('/* ------------------------------------------------------------ celebrations */'),
                        src.indexOf('/* ----------------------------------------------------------------- router */'));

let fails = 0;
const check = (name, cond, extra) => {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + name + (!cond && extra ? '  ' + extra : ''));
  if (!cond) fails++;
};

function harness(stored) {
  const store = Object.assign({}, stored);
  const els = {};
  const mk = (id) => (els[id] = els[id] || {
    id, hidden: true, textContent: '', innerHTML: '', style: {}, onclick: null,
    classList: { add() {}, remove() {}, toggle() {} },
    appendChild(c) { (this.children = this.children || []).push(c); },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
  });
  ['levelup', 'luNum', 'luTitle', 'luSub', 'toasts', 'fxLayer', 'pillXp', 'pillXp2',
   'questList', 'questNote', 'rgFill', 'rgNum'].forEach(mk);

  const fn = new Function(
    'localStorage', '$', 'esc', 'document', 'setTimeout', 'performance',
    'requestAnimationFrame', 'loadSummary',
    `var ME = null, SUM = null;\n${block}\n
     return { syncCelebrations, showLevelUp, toast, goals,
              set: function (me, sum) { ME = me; SUM = sum; } };`);

  const api = fn(
    { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    mk, (s) => String(s == null ? '' : s),
    { createElement: () => ({ classList: { add() {}, remove() {} }, style: {},
                              remove() {}, set innerHTML(v) {}, set textContent(v) {} }) },
    () => {}, { now: () => 0 }, () => {}, () => Promise.resolve());
  return { api, els, store };
}

const mkSum = (level, xp, ach, extra) => Object.assign({
  game: { level, xp, title: 'T' + level, next_title: 'T' + (level + 1), to_next: 100, recent: [] },
  achievements: ach || [],
  sessions: [],
}, extra || {});

console.log('\n--- level-up fires exactly once');
{
  const h = harness({});
  h.api.set({ id: 'u_test' }, mkSum(2, 100));
  h.api.syncCelebrations();
  check('first visit records the level silently', h.store['ml_level_u_test'] === '2', h.store['ml_level_u_test']);
  check('first visit shows no overlay', h.els.levelup.hidden === true);

  h.api.set({ id: 'u_test' }, mkSum(3, 250));
  h.api.syncCelebrations();
  check('2 -> 3 opens the overlay', h.els.levelup.hidden === false);
  check('overlay shows the new level', String(h.els.luNum.textContent) === '3');
  check('overlay shows the new title', String(h.els.luTitle.textContent) === 'T3');
  check('stored level advanced', h.store['ml_level_u_test'] === '3');

  h.els.levelup.hidden = true;
  h.api.syncCelebrations();
  check('same level does not re-fire', h.els.levelup.hidden === true);

  h.api.set({ id: 'u_test' }, mkSum(5, 1000));
  h.api.syncCelebrations();
  check('a multi-level jump still fires once', h.els.levelup.hidden === false);
}

console.log('\n--- achievements toast once each');
{
  const h = harness({ 'ml_level_u_test': '2' });
  h.api.set({ id: 'u_test' }, mkSum(2, 100, [
    { id: 'first_blood', name: 'First diagnosis', desc: 'd', earned: true },
    { id: 'deck', name: 'Deck started', desc: 'd', earned: false }]));
  h.api.syncCelebrations();
  const seen = JSON.parse(h.store['ml_ach_u_test']);
  check('earned achievement recorded', seen.includes('first_blood'));
  check('unearned achievement not recorded', !seen.includes('deck'));
  const before = h.store['ml_ach_u_test'];
  h.api.syncCelebrations();
  check('recorded set stable across calls', h.store['ml_ach_u_test'] === before);

  const h2 = harness({ 'ml_level_u_test': '2', 'ml_ach_u_test': '["first_blood"]' });
  h2.api.set({ id: 'u_test' }, mkSum(2, 100, [
    { id: 'deck', name: 'Deck started', desc: 'd', earned: true }]));
  h2.api.syncCelebrations();
  check('newly earned achievement is added', JSON.parse(h2.store['ml_ach_u_test']).includes('deck'));
}

console.log('\n--- goals derive from live state');
{
  const h = harness({ 'ml_level_u_test': '2' });
  const now = Date.now() / 1000;
  h.api.set({ id: 'u_test' }, mkSum(2, 100, [], {
    game: { level: 2, xp: 100, title: 'T2', next_title: 'T3', to_next: 100,
            recent: [{ kind: 'flashcard', ts: now }, { kind: 'flashcard', ts: now },
                     { kind: 'flashcard', ts: 0 }] },
    sessions: [{ metrics: { gaps: [], answers: 9, mastered: ['a', 'b'] },
                 state: { teachback: [{ score: 0.8 }] } }],
  }));
  const g = h.api.goals();
  check('three goals', g.length === 3);
  check('teach-back goal complete', g[0].have >= g[0].need, JSON.stringify(g[0]));
  check('clear-the-red goal complete', g[1].have >= g[1].need, JSON.stringify(g[1]));
  check('card goal counts only today', g[2].have === 2, JSON.stringify(g[2]));

  const h2 = harness({ 'ml_level_u_test': '2' });
  h2.api.set({ id: 'u_test' }, mkSum(2, 0, [], {
    game: { level: 2, xp: 0, title: 'T2', next_title: 'T3', to_next: 100, recent: [] },
    sessions: [{ metrics: { gaps: ['x'], answers: 4, mastered: [] }, state: { teachback: [] } }],
  }));
  const g2 = h2.api.goals();
  check('incomplete goals report zero', g2.every(q => q.have === 0), JSON.stringify(g2.map(q => q.have)));
}

console.log(fails ? `\nFAILURES: ${fails}` : '\nALL UI CHECKS PASSED');
process.exit(fails ? 1 : 0);
