/* MasteryLoop front end — hash router, no framework, no CDN, works offline. */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const md = (s) => esc(s)
  .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  .replace(/`([^`]+)`/g, '<code>$1</code>')
  .replace(/\*([^*]+)\*/g, '<em>$1</em>');
const pct = (v) => Math.round((v || 0) * 100) + '%';

let ME = null, SUM = null;
let SID = null, STATE = null, STEP = null, PENDING = null, HOT = new Set();
let CARDS = { due: [], stats: { total: 0, due: 0, reviews: 0 } };
let CARD_I = 0;
let NODE_SCREEN = {};          // concept id -> screen coords, for floating XP
let PREV_LOCKED = null;        // null until the first graph draw

/* -------------------------------------------------------------------- api */
async function api(path, body, method, silent401) {
  const res = await fetch(path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  });
  if (res.status === 401) {
    // the boot probe is expected to 401 for a logged-out visitor - don't bounce them
    if (!silent401) { ME = null; go('/login'); }
    throw new Error('Please sign in again.');
  }
  let data = null;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) {
    const d = data.detail;
    const msg = typeof d === 'string' ? d : (d && d.problems ? d.problems.join(' · ') : 'Request failed');
    const err = new Error(msg); err.payload = d; throw err;
  }
  return data;
}

function busy(btn, on, label) {
  if (!btn) return;
  if (on) { btn.dataset.l = btn.innerHTML; btn.innerHTML = `<span class="spinner"></span>${label || ''}`; btn.disabled = true; }
  else { btn.innerHTML = btn.dataset.l || btn.innerHTML; btn.disabled = false; }
}


/* ------------------------------------------------------------ celebrations */
function animateXp(from, to) {
  const el = $('pillXp') || $('pillXp2');
  if (!el) return;
  const t0 = performance.now(), dur = 720;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);
    el.innerHTML = `<b>${Math.round(from + (to - from) * e)}</b> XP`;
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function floatXp(amount, conceptId) {
  if (!amount || amount <= 0) return;
  const layer = $('fxLayer');
  if (!layer) return;
  let at = NODE_SCREEN[conceptId];
  if (!at) {
    const pill = $('pillXp2') || $('pillXp');
    if (!pill) return;
    const r = pill.getBoundingClientRect();
    at = { x: r.left + r.width / 2, y: r.top };
  }
  const el = document.createElement('div');
  el.className = 'float-xp';
  el.textContent = `+${amount} XP`;
  el.style.left = at.x + 'px';
  el.style.top = at.y + 'px';
  layer.appendChild(el);
  setTimeout(() => el.remove(), 1250);
}

function toast(name, desc) {
  const box = $('toasts');
  if (!box) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.innerHTML = `<div class="t-ic">★</div><div><b>${esc(name)}</b><small>${esc(desc)}</small></div>`;
  box.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 340); }, 4400);
}

function showLevelUp(g) {
  const el = $('levelup');
  if (!el) return;
  $('luNum').textContent = g.level;
  $('luTitle').textContent = g.title;
  $('luSub').textContent = g.next_title
    ? `${g.to_next} XP to ${g.next_title}.`
    : 'Top level reached.';
  el.hidden = false;
  const close = () => { el.hidden = true; };
  el.onclick = close;
  setTimeout(close, 3400);
}

/** Level-ups and achievement unlocks are detected by diffing against what this
 *  browser last saw, so they fire exactly once. First visit seeds silently. */
function syncCelebrations() {
  if (!ME || !SUM) return;
  const g = SUM.game, uid = ME.id;
  const lk = 'ml_level_' + uid, ak = 'ml_ach_' + uid;
  let prev = 0;
  try { prev = parseInt(localStorage.getItem(lk) || '0', 10) || 0; } catch {}
  const firstVisit = prev === 0;
  if (!firstVisit && g.level > prev) showLevelUp(g);
  try { localStorage.setItem(lk, String(g.level)); } catch {}

  let seen = [];
  try { seen = JSON.parse(localStorage.getItem(ak) || '[]') || []; } catch {}
  SUM.achievements.forEach(a => {
    if (a.earned && !seen.includes(a.id)) {
      if (!firstVisit) toast(a.name, a.desc);
      seen.push(a.id);
    }
  });
  try { localStorage.setItem(ak, JSON.stringify(seen)); } catch {}
}

function refreshMe() {
  return loadSummary().then(() => { renderXpPill(); }).catch(() => {});
}

function renderXpPill() {
  if (!SUM) return;
  const el = $('pillXp');
  if (el) el.innerHTML = `<b>${SUM.game.xp}</b> XP`;
  const el2 = $('pillXp2');
  if (el2) el2.innerHTML = `<b>${SUM.game.xp}</b> XP`;
}

/* ------------------------------------------------------------------- goals */
function goals() {
  const last = SUM.sessions[0];
  const m = last ? last.metrics : null;
  const tb = last && last.state ? (last.state.teachback || []).length : 0;
  const t0 = new Date(); t0.setHours(0, 0, 0, 0);
  const today = (SUM.game.recent || []).filter(e => (e.ts || 0) * 1000 >= t0.getTime());
  const reviews = today.filter(e => e.kind === 'flashcard').length;
  const clean = m ? (m.gaps.length === 0 && m.answers > 2) : false;
  return [
    { id: 'explain', name: 'Explain a concept back',
      sub: tb ? `${tb} graded so far` : 'A teach-back against the rubric',
      have: Math.min(tb, 1), need: 1 },
    { id: 'gap', name: 'Clear the red',
      sub: m && m.gaps.length ? `${m.gaps.length} concept still in the red`
                              : (m ? 'Nothing in the red' : 'Start a session'),
      have: clean ? 1 : 0, need: 1 },
    { id: 'cards', name: 'Review 5 flashcards',
      sub: `${Math.min(reviews, 5)} of 5 reviewed today`,
      have: Math.min(reviews, 5), need: 5 },
  ];
}

function renderGoals() {
  const box = $('questList');
  if (!box) return;
  const list = goals();
  box.innerHTML = list.map(q => {
    const done = q.have >= q.need;
    return `<div class="quest ${done ? 'done' : ''}">
      <span class="q-ic">${done ? '✓' : ''}</span>
      <div class="q-body"><div class="q-name">${esc(q.name)}</div>
        <div class="q-sub">${esc(q.sub)}</div></div>
      <span class="q-track"><i style="width:${Math.round(100 * q.have / q.need)}%"></i></span>
    </div>`;
  }).join('');
  const done = list.filter(q => q.have >= q.need).length;
  $('questNote').textContent = done === list.length
    ? 'All goals cleared. Mastery is the score — goals are just the nudge.'
    : `${done}/${list.length} done. Goals do not pay XP; only mastery movement does.`;
}

function renderRing() {
  const el = $('rgFill');
  if (!el) return;
  const last = SUM.sessions[0];
  const r = last ? last.readiness : 0;
  const C = 314.16;
  el.style.strokeDashoffset = C * (1 - Math.max(0, Math.min(1, r)));
  el.classList.remove('low', 'mid', 'high');
  el.classList.add(r >= 0.7 ? 'high' : r >= 0.4 ? 'mid' : 'low');
  $('rgNum').textContent = Math.round(r * 100) + '%';
}

/* ----------------------------------------------------------------- router */
const SHELL_VIEWS = { '/app': 'view-app', '/learn': 'view-learn', '/cards': 'view-cards',
                      '/profile': 'view-profile', '/settings': 'view-settings' };

function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }

function route() {
  const path = (location.hash.replace('#', '') || '/').split('?')[0];
  const needAuth = !!SHELL_VIEWS[path];
  if (needAuth && !ME) { location.hash = '#/login'; return; }

  $('landing').hidden = path !== '/';
  $('auth').hidden = !(path === '/login' || path === '/signup');
  $('shell').hidden = !needAuth;

  if (path === '/login' || path === '/signup') renderAuth(path === '/signup');
  if (!needAuth) return;

  Object.entries(SHELL_VIEWS).forEach(([p, id]) => { $(id).hidden = p !== path; });
  document.querySelectorAll('[data-nav]').forEach(a =>
    a.classList.toggle('active', `/${a.dataset.nav}` === path));
  $('sidebar').classList.remove('open');

  if (path === '/app') loadSummary().then(renderHome).catch(() => {});
  if (path === '/learn') enterLearn();
  if (path === '/cards') enterCards();
  if (path === '/profile') loadSummary().then(renderProfile).catch(() => {});
  if (path === '/settings') enterSettings();
}

window.addEventListener('hashchange', route);

/* ------------------------------------------------------------------- auth */
function renderAuth(isSignup) {
  $('authKicker').textContent = isSignup ? 'Create your account' : 'Log in to your account';
  $('authTitle').textContent = isSignup ? 'Let’s begin.' : 'Welcome back!';
  $('authSub').textContent = isSignup ? 'Free to start. No card needed.' : 'Enter your email and password.';
  $('authGo').textContent = isSignup ? 'Create account' : 'Sign in';
  $('nameWrap').hidden = !isSignup;
  $('authMsg').textContent = '';
  $('authSwitch').innerHTML = isSignup
    ? 'Already have an account? <a href="#/login">Log in</a>'
    : 'New here? <a href="#/signup">Create an account</a>';
  $('aPw').autocomplete = isSignup ? 'new-password' : 'current-password';
  window.__signup = isSignup;
}

$('pwToggle').onclick = () => {
  const p = $('aPw'), show = p.type === 'password';
  p.type = show ? 'text' : 'password';
  $('pwToggle').textContent = show ? 'Hide' : 'Show';
};

$('authForm').onsubmit = async (e) => {
  e.preventDefault();
  const btn = $('authGo'), msg = $('authMsg');
  msg.className = 'msg'; msg.textContent = '';
  const body = { email: $('aEmail').value.trim(), password: $('aPw').value };
  if (window.__signup) body.name = $('aName').value.trim();
  busy(btn, true, window.__signup ? 'Creating' : 'Signing in');
  try {
    const out = await api(window.__signup ? '/api/auth/signup' : '/api/auth/login', body);
    ME = out.user;
    location.hash = '#/app';
  } catch (err) {
    msg.className = 'msg bad'; msg.textContent = err.message;
  } finally { busy(btn, false); }
};

$('logoutBtn').onclick = async () => {
  try { await api('/api/auth/logout', {}); } catch {}
  ME = null; SUM = null; location.hash = '#/';
};

/* -------------------------------------------------------------- shell glue */
$('collapse').onclick = () => {
  const s = $('shell');
  s.classList.toggle('collapsed');
  try { localStorage.setItem('ml_collapsed', s.classList.contains('collapsed') ? '1' : '0'); } catch {}
};
if (localStorage.getItem('ml_collapsed') === '1') $('shell').classList.add('collapsed');

$('sbUser').onclick = () => { location.hash = '#/profile'; };

async function loadSummary() {
  SUM = await api('/api/me/summary');
  ME = SUM.user;
  $('avatar').textContent = (ME.name || '?').trim().charAt(0).toUpperCase();
  $('sbName').textContent = ME.name;
  $('sbLevel').textContent = `Level ${SUM.game.level} · ${SUM.game.title}`;
  const badge = $('dueBadge');
  badge.hidden = SUM.cards.due === 0;
  badge.textContent = SUM.cards.due;
  syncCelebrations();          // level-ups and unlocks are diffed on every load
  return SUM;
}

/* ------------------------------------------------------------------- home */
function renderHome() {
  const g = SUM.game, hr = new Date().getHours();
  const hi = hr < 12 ? 'Good morning' : hr < 17 ? 'Good afternoon' : 'Good evening';
  $('greet').textContent = `${hi}, ${(ME.name || '').split(' ')[0]}.`;
  const flame = '<svg class="flame" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2s4 4 4 8a4 4 0 0 1-8 0c0-1 .3-2 .8-2.8C8 9 6 11 6 14a6 6 0 0 0 12 0c0-6-6-12-6-12z"/></svg>';
  $('pillStreak').innerHTML = `${g.streak ? flame : ''}<b>${g.streak}</b>-day streak`;
  $('pillStreak').classList.toggle('hot', g.streak >= 2);
  $('pillLevel').innerHTML = `Level <b>${g.level}</b> · ${esc(g.title)}`;
  $('pillXp').innerHTML = `<b>${g.xp}</b> XP`;
  $('lvNum').textContent = g.level;
  $('lvTitle').textContent = g.title;
  $('lvBar').style.width = Math.round(g.progress * 100) + '%';
  $('lvNext').textContent = g.next_title
    ? `${g.to_next} XP to ${g.next_title}`
    : 'Top level reached.';
  renderRing();
  renderGoals();
  $('dueNum').textContent = SUM.cards.due;
  $('cardMeta').textContent = SUM.cards.total
    ? `${SUM.cards.total} cards · ${SUM.cards.reviews} reviews done`
    : 'No deck yet — generate one from your gaps.';

  const last = SUM.sessions[0];
  $('continueBox').innerHTML = last ? `
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
      <div style="flex:1;min-width:180px">
        <b style="font-size:17px">${esc(last.title)}</b>
        <div class="small muted">${last.mastered}/${last.concepts} concepts mastered · ${last.answers} answers</div>
        <div class="bar-track" style="max-width:280px"><div class="bar-fill" style="width:${Math.round(last.readiness * 100)}%"></div></div>
      </div>
      <button class="btn light" id="resumeBtn">Resume</button>
    </div>` : `<div class="empty">No session yet. Pick a topic to begin.</div>`;
  if (last) $('resumeBtn').onclick = () => { location.hash = `#/learn?sid=${last.session_id}`; };

  api('/api/topics').then(({ topics }) => {
    $('topicList').innerHTML = topics.map(t => {
      const s = SUM.sessions.find(x => x.topic === t.id);
      const p = s ? Math.round(s.readiness * 100) : 0;
      return `<div class="topic-row">
        <div class="meta"><b>${esc(t.title)} ${t.generated ? '<span class="chip mastered" style="margin-left:6px">yours</span>' : ''}</b>
          <small>${esc(t.subject)} · ${t.concepts} concepts · ${t.items} items</small></div>
        <div class="mini-bar"><i style="width:${p}%"></i></div>
        <button class="btn sm ghost" data-start="${esc(t.id)}">${s ? 'Continue' : 'Start'}</button>
      </div>`;
    }).join('') || '<div class="empty">No topics yet.</div>';
    $('topicList').querySelectorAll('[data-start]').forEach(b => {
      b.onclick = () => startTopic(b.dataset.start);
    });
  }).catch(() => {});

  const ach = SUM.achievements.map(a => `
    <div class="ach-item ${a.earned ? 'on' : ''}" title="${esc(a.desc)}">
      <span class="dot"></span><div><b>${esc(a.name)}</b><small>${esc(a.desc)}</small></div>
    </div>`).join('');
  $('achList').innerHTML = ach;
}

/* ------------------------------------------------------------------ learn */
async function enterLearn() {
  const q = new URLSearchParams((location.hash.split('?')[1] || ''));
  const sid = q.get('sid');
  $('learnPicker').hidden = true; $('learnStage').hidden = true;
  if (sid) { await resumeSession(sid); return; }
  if (SUM && SUM.sessions[0]) { await resumeSession(SUM.sessions[0].session_id); return; }
  $('learnPicker').hidden = false;
  $('learnTitle').textContent = 'Learn';
  const { topics } = await api('/api/topics');
  $('learnTopics').innerHTML = topics.map(t => `
    <div class="topic-row"><div class="meta"><b>${esc(t.title)}</b>
      <small>${esc(t.subject)} · ${t.concepts} concepts</small></div>
      <button class="btn sm light" data-s="${esc(t.id)}">Start</button></div>`).join('');
  $('learnTopics').querySelectorAll('[data-s]').forEach(b => {
    b.onclick = () => startTopic(b.dataset.s);
  });
}

async function startTopic(topic) {
  location.hash = '#/learn';
  const r = await api('/api/session', { topic });
  SID = r.session_id;
  renderStep(r);
}

async function resumeSession(sid) {
  try {
    const r = await api(`/api/session/${sid}`);
    SID = sid;
    if (r.step && r.step.type) renderStep(r);
    else { $('learnPicker').hidden = false; }
  } catch { $('learnPicker').hidden = false; }
}

function renderStep(resp) {
  PENDING = null;
  STEP = resp.step; STATE = resp.state;
  $('learnPicker').hidden = true; $('learnStage').hidden = false;
  $('learnTitle').textContent = STATE.title;
  $('pillReadiness').innerHTML = `Readiness <b>${pct(STATE.metrics.readiness)}</b>`;
  $('pillMastered').innerHTML = `<b>${STATE.metrics.mastered.length}</b>/${STATE.metrics.concepts.length} mastered`;
  $('pillXp2').innerHTML = `<b>${SUM ? SUM.game.xp : 0}</b> XP`;
  paintSide(); paintTrace();
  const t = STEP.type;
  if (t === 'quiz') renderQuiz();
  else if (t === 'teach') renderTeach();
  else if (t === 'verify') renderVerify();
  else renderReport();
}

function paintSide() {
  if (!STATE) return;
  drawGraph();
  paintDx();
}

function paintTrace() {
  if (!STATE) return;
  const rows = [];
  for (const e of (STATE.log || []).slice().reverse()) {
    if (e.type === 'answer') rows.push(`<div><span class="t">·</span><span class="a">${esc(e.item)} ${e.correct ? 'ok' : 'MISS'} · ${esc(e.concept)} ${e.mastery_before.toFixed(2)}→${e.mastery_after.toFixed(2)}${e.misconception ? ` · <span style="color:#EDEDF0">${esc(e.misconception)}</span>` : ''}</span></div>`);
    else if (e.type === 'teachback') rows.push(`<div><span class="t">·</span><span class="a">teachback ${esc(e.concept)} ${e.score} ${e.mastery_before.toFixed(2)}→${e.mastery_after.toFixed(2)}</span></div>`);
    else if (e.type === 'step') rows.push(`<div><span class="t">${e.step}</span><span class="a ${esc(e.action)}">→ ${esc(e.action)} <span style="color:#71717A">(${esc(e.reason)})</span></span></div>`);
  }
  $('trace').innerHTML = rows.slice(0, 40).join('') || '<div class="muted">no events yet</div>';
}

/* --- quiz --- */
function renderQuiz() {
  const s = STEP;
  $('stepCard').innerHTML = `
    <div class="qmeta">
      <span class="qconcept">${esc(s.concept_name)}</span>
      <span class="chip">mastery ${pct(s.mastery)}</span>
      <span class="chip why" style="margin-left:auto">${esc(s.reason || 'probe')}</span>
    </div>
    <div class="stem">${md(s.item.stem)}</div>
    <div class="opts" id="opts">
      ${s.item.options.map((o, n) => `<button class="opt" data-i="${o.i}">
        <span class="key">${'ABCD'[n]}</span><span>${md(o.text)}</span></button>`).join('')}
    </div>
    <div id="fbslot"></div>`;
  $('opts').querySelectorAll('.opt').forEach(b => {
    b.onclick = () => answer(Number(b.dataset.i), b);
  });
  window.__keys = (n) => {
    const b = $('opts').querySelectorAll('.opt')[n];
    if (b && !b.disabled) b.click();
  };
}

async function answer(choice, btn) {
  const s = STEP;
  $('opts').querySelectorAll('.opt').forEach(b => b.disabled = true);
  busy(btn, true, '');
  let resp;
  try { resp = await api('/api/answer', { session_id: SID, item_id: s.item.id, choice }); }
  catch (e) { busy(btn, false); $('opts').querySelectorAll('.opt').forEach(b => b.disabled = false); return; }
  const fb = resp.feedback;
  const chosen = $('opts').querySelector(`.opt[data-i="${choice}"]`);
  chosen.classList.add(fb.correct ? 'good' : 'bad');
  busy(btn, false);
  $('opts').querySelectorAll('.opt').forEach(b => { if (b !== chosen) b.classList.add('dim'); });
  if (!fb.correct) {
    const right = [...$('opts').querySelectorAll('.opt')].find(b => {
      const o = s.item.options.find(x => x.i === Number(b.dataset.i));
      return o && o.text === fb.correct_text;
    });
    if (right) { right.classList.remove('dim'); right.classList.add('good'); }
  }
  HOT = new Set((fb.propagated || []).map(p => p.concept));
  const d = fb.mastery_after - fb.mastery_before;
  $('fbslot').innerHTML = `
    <div class="fb ${fb.correct ? 'ok' : 'no'}">
      <div class="head"><span>${fb.correct ? 'Correct' : 'Not quite'}</span>
        <span class="delta ${d > 0 ? 'up' : d < 0 ? 'down' : ''}" style="margin-left:auto">
          mastery ${pct(fb.mastery_before)} → ${pct(fb.mastery_after)}</span></div>
      <div class="small">${md(fb.explain)}</div>
      <div class="actions">
        ${fb.misconception ? `<span class="chip gap">tagged: ${esc(fb.misconception.name)}</span>` : ''}
        ${(fb.propagated || []).map(p => `<span class="chip why">${esc(p.concept)} ${p.delta > 0 ? '+' : ''}${p.delta}</span>`).join('')}
        ${resp.xp_earned ? `<span class="delta up">+${resp.xp_earned} XP</span>` : ''}
      </div>
      <div class="actions"><button class="btn" id="cont">Continue</button></div>
    </div>`;
  PENDING = resp; STATE = resp.state; paintSide(); paintTrace();
  if (resp.xp_earned) {
    const before = (SUM && SUM.game ? SUM.game.xp : 0);
    floatXp(resp.xp_earned, fb.concept);
    if (SUM && SUM.game) { animateXp(before, resp.xp_total); SUM.game.xp = resp.xp_total; }
    refreshMe();
  }
  $('cont').onclick = () => renderStep(PENDING);
  window.__keys = (n) => { if (n === 5 || n === 0) renderStep(PENDING); };
}

/* --- teach --- */
function renderTeach() {
  const s = STEP, e = s.explanation || {};
  const pr = e.practice;
  $('stepCard').innerHTML = `
    <div class="qmeta">
      <span class="qconcept">${esc(s.concept_name)}</span>
      <span class="chip">mastery ${pct(s.mastery)}</span>
      <span class="chip ${e.source === 'llm' ? 'mastered' : ''}" style="margin-left:auto">
        ${e.source === 'llm' ? 'personalised' : 'authored'}</span>
    </div>
    <div class="teach-h">${md(e.headline || s.concept_name)}</div>
    ${e.hook ? `<div class="hook">${md(e.hook)}</div>` : ''}
    <div class="body">${(e.body || []).map(p => `<p>${md(p)}</p>`).join('')}</div>
    ${(e.worked || []).length ? `<div style="margin-top:14px"><div class="sec-label">Worked through</div>
      <div class="worked">${e.worked.map((w, i) => `<div class="w"><span class="n">${i + 1}</span><span>${md(w)}</span></div>`).join('')}</div></div>` : ''}
    ${pr && pr.problem ? `<div style="margin-top:14px"><div class="sec-label">Your turn</div>
      <div class="practice"><div class="pr-q">${md(pr.problem)}</div>
        <details><summary>Show a hint</summary><div class="pr-a">${md(pr.hint || '')}</div></details>
        <details><summary>Show the model answer</summary><div class="pr-a">${md(pr.solution || '')}</div></details>
        <div class="pr-note">Practice is rehearsal — it does not move your mastery. The quiz and the teach-back do.</div>
      </div></div>` : ''}
    <div style="margin-top:14px">
      <details class="rubric"><summary>The rubric your explanation will be graded against</summary>
        <ul class="ideas">${(e.key_ideas || []).map(k => `<li>${md(k)}</li>`).join('')}</ul></details>
    </div>
    <div class="actions"><button class="btn" id="cont">Got it — let me explain it back</button></div>`;
  $('cont').onclick = async () => {
    busy($('cont'), true, 'Preparing');
    try { renderStep(await api('/api/next', { session_id: SID })); }
    catch { busy($('cont'), false); }
  };
  window.__keys = (n) => { if (n === 5 || n === 0) $('cont').click(); };
}

/* --- verify --- */
function renderVerify() {
  const s = STEP;
  $('stepCard').innerHTML = `
    <div class="qmeta"><span class="qconcept">${esc(s.concept_name)}</span>
      <span class="chip dev">explain it yourself</span></div>
    <div class="stem">${md(s.prompt)}</div>
    <p class="small muted" style="margin:0 0 12px">Graded against the rubric. If it does not land,
      mastery does not move — a right answer you cannot explain is not mastery.</p>
    <textarea class="answer" id="tb" placeholder="Start with the mechanism, not the definition. What actually happens, and in what order?"></textarea>
    <div class="actions"><button class="btn" id="sub">Submit explanation</button>
      <span class="small muted" id="wc">0 words</span></div>
    <div id="gradeslot"></div>`;
  const ta = $('tb'); ta.focus();
  ta.oninput = () => { $('wc').textContent = (ta.value.trim().match(/\S+/g) || []).length + ' words'; };
  $('sub').onclick = () => submitTeachback(ta.value, $('sub'));
  window.__keys = null;
}

async function submitTeachback(text, btn) {
  if (!text.trim()) { $('tb').focus(); return; }
  busy(btn, true, 'Grading');
  let resp;
  try { resp = await api('/api/teachback', { session_id: SID, text }); }
  catch { busy(btn, false); return; }
  const g = resp.grade, circ = 2 * Math.PI * 20;
  $('gradeslot').innerHTML = `
    <div class="fb ${g.score >= 0.6 ? 'ok' : 'no'}" style="margin-top:14px">
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <svg class="ring" width="54" height="54" viewBox="0 0 54 54">
          <circle cx="27" cy="27" r="20" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="4"/>
          <circle cx="27" cy="27" r="20" fill="none" stroke="${g.score >= 0.6 ? '#FFFFFF' : 'rgba(255,255,255,.28)'}"
            stroke-width="4" stroke-linecap="round" transform="rotate(-90 27 27)"
            stroke-dasharray="${circ}" stroke-dashoffset="${circ * (1 - g.score)}"/></svg>
        <div><div style="font-size:24px;font-weight:600">${Math.round(g.score * 100)}<span class="small muted">/100</span></div>
          <div class="small muted">${esc(g.concept_name)} · ${(g.hits || []).length} key ideas</div></div>
        <span class="delta ${g.delta > 0 ? 'up' : 'down'}" style="margin-left:auto">
          mastery ${pct(g.mastery_before)} → ${pct(g.mastery_after)}</span>
      </div>
      <div class="small" style="margin-top:10px">${md(g.feedback)}</div>
      <div class="hits">${(g.hits || []).map(h => `<div class="hit ${h.hit ? 'y' : 'n'}">
        <span class="ic">${h.hit ? '✓' : '–'}</span><span>${md(h.idea)}</span></div>`).join('')}</div>
      <div class="actions">
        <span class="chip">grader: ${esc(g.source)}</span>
        ${resp.xp_earned ? `<span class="delta up">+${resp.xp_earned} XP</span>` : ''}
        <button class="btn" id="cont" style="margin-left:auto">Continue</button></div>
    </div>`;
  PENDING = resp; STATE = resp.state; paintSide(); paintTrace();
  if (resp.xp_earned) {
    const before = (SUM && SUM.game ? SUM.game.xp : 0);
    floatXp(resp.xp_earned, fb.concept);
    if (SUM && SUM.game) { animateXp(before, resp.xp_total); SUM.game.xp = resp.xp_total; }
    refreshMe();
  }
  $('cont').onclick = () => renderStep(PENDING);
  window.__keys = (n) => { if (n === 5 || n === 0) renderStep(PENDING); };
}

/* --- report --- */
function renderReport() {
  const m = STATE.metrics;
  $('stepCard').innerHTML = `
    <div class="teach-h">Every reachable concept is at mastery.</div>
    <div class="hook">Readiness ${pct(m.readiness)} · accuracy ${pct(m.accuracy)} · ${m.answers} answers</div>
    <div class="worked">${m.concepts.map(c => `<div class="w">
      <span class="n">${pct(c.mastery)}</span>
      <span style="flex:1">${esc(c.name)} <span class="small muted">· ${c.correct}/${c.attempts}</span></span>
      <span class="chip ${c.status}">${c.status}</span></div>`).join('')}</div>
    <div class="actions"><button class="btn" id="again">Back to home</button>
      <button class="btn ghost" id="mkdeck">Make flashcards from this</button></div>`;
  $('again').onclick = () => { location.hash = '#/app'; };
  $('mkdeck').onclick = async () => {
    busy($('mkdeck'), true, 'Building');
    try {
      await api('/api/flashcards/generate', { topic: STATE.topic });
      location.hash = '#/cards';
    } catch (e) { busy($('mkdeck'), false); }
  };
  window.__keys = null;
}

/* ------------------------------------------------------------- diagnosis */
function paintDx() {
  const d = STATE.diagnosis, el = $('dx');
  if (!d) {
    el.innerHTML = `<div class="empty">Answer a few questions. Diagnosis needs at least two
      answers pointing at the same wrong idea — one miss is noise, two is a pattern.</div>`;
    return;
  }
  el.innerHTML = `
    <div class="dx-title">${esc(d.name)}</div>
    <div class="dx-state">“${esc(d.statement)}”</div>
    <div class="dx-row"><span>confidence</span>
      <span class="conf"><i style="width:${Math.round(d.confidence * 100)}%"></i></span>
      <span class="mono">${Math.round(d.confidence * 100)}%</span></div>
    <div class="dx-row"><span>root cause</span>
      <span class="chip gap">${esc(d.root_cause_name || d.root_cause_concept)}</span>
      <span style="margin-left:auto">${d.evidence_count} answers</span></div>
    <div class="sec-label" style="margin-top:10px">Evidence</div>
    <div class="evidence">${d.evidence_items.map(e => `<div class="e">${md(e.stem)}</div>`).join('')}</div>`;
}

/* ------------------------------------------------------------------ graph */
const STC = {
  untested:   { s: 'rgba(255,255,255,.16)', f: 'rgba(255,255,255,.02)', t: '#71717A', b: 'rgba(255,255,255,.22)' },
  gap:        { s: '#FFFFFF',               f: 'rgba(255,255,255,.05)', t: '#FFFFFF', b: '#FFFFFF' },
  developing: { s: 'rgba(255,255,255,.55)', f: 'rgba(255,255,255,.10)', t: '#EDEDF0', b: 'rgba(255,255,255,.6)' },
  mastered:   { s: '#FFFFFF',               f: '#FFFFFF',               t: '#060608', b: '#060608' },
};

function drawGraph() {
  const svg = $('graph');
  if (!svg || !STATE) return;
  const nodes = STATE.graph.nodes;
  if (!nodes.length) return;

  // which concepts are reachable, straight from the metrics the engine sends
  const unlocked = {};
  (STATE.metrics.concepts || []).forEach(c => { unlocked[c.id] = c.unlocked; });

  const focus = STEP && STEP.concept ? STEP.concept : null;
  const byDepth = {};
  nodes.forEach(n => (byDepth[n.depth] = byDepth[n.depth] || []).push(n));
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);

  const H = 30, GAPY = 34, GAPX = 20, PAD = 18;
  const widthOf = n => Math.max(92, Math.min(186, n.name.length * 5.9 + 28));
  const pos = {}; let maxW = 0;
  depths.forEach(d => {
    const row = byDepth[d], ws = row.map(widthOf);
    const total = ws.reduce((a, b) => a + b, 0) + GAPX * (row.length - 1);
    maxW = Math.max(maxW, total);
    let x = -total / 2;
    row.forEach((n, i) => {
      pos[n.id] = { x: x + ws[i] / 2, y: PAD + d * (H + GAPY), w: ws[i] };
      x += ws[i] + GAPX;
    });
  });
  const vw = maxW + PAD * 2;
  const vh = PAD * 2 + depths.length * (H + GAPY) - GAPY;
  svg.setAttribute('viewBox', `${-vw / 2} ${-PAD} ${vw} ${vh + PAD}`);

  const defs = `<defs>
    <marker id="arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,1 L7,4 L0,7 z" fill="rgba(120,160,255,.45)"/></marker>
    <marker id="arwHot" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M0,1 L7,4 L0,7 z" fill="#FFFFFF"/></marker></defs>`;

  const edges = [];
  nodes.forEach(n => (n.prereqs || []).forEach(p => {
    if (!pos[p] || !pos[n.id]) return;
    const a = pos[p], b = pos[n.id];
    const y1 = a.y + H / 2, y2 = b.y - H / 2, dy = Math.max(12, (y2 - y1) * 0.55);
    const hot = HOT.has(n.id) || HOT.has(p) || (focus && (p === focus || n.id === focus));
    const dim = n.status === 'untested' && n.attempts === 0;
    edges.push(`<path class="edge ${hot ? 'hot' : ''} ${dim && !hot ? 'dim' : ''}"
      d="M${a.x},${y1} C${a.x},${y1 + dy} ${b.x},${y2 - dy} ${b.x},${y2}"
      marker-end="url(#${hot ? 'arwHot' : 'arw'})"/>`);
  }));

  // a concept that just became reachable gets a burst, once
  const lockedNow = new Set(nodes.filter(n => unlocked[n.id] === false).map(n => n.id));
  const justOpened = PREV_LOCKED
    ? [...PREV_LOCKED].filter(id => !lockedNow.has(id) && pos[id]) : [];

  const pills = nodes.map(n => {
    const p = pos[n.id];
    const isLocked = unlocked[n.id] === false;
    const c = STC[n.status] || STC.untested;
    const x = p.x - p.w / 2;
    const bar = isLocked ? 0 : Math.max(0, Math.min(1, n.mastery)) * p.w;
    const isF = n.id === focus;
    const pad = isLocked ? 26 : 14;
    const fits = Math.floor((p.w - pad) / 5.6);
    const label = n.name.length > fits ? n.name.slice(0, Math.max(4, fits - 1)) + '…' : n.name;
    const cls = ['node', isLocked ? 'locked' : '', n.status === 'mastered' ? 'mastered' : ''].join(' ');
    const lock = isLocked
      ? `<g class="lock" transform="translate(${x + 11},${p.y - 5}) scale(0.5)" fill="none"
            stroke="${c.t}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
           <rect x="5" y="11" width="14" height="10" rx="2"/>
           <path d="M8 11V7a4 4 0 0 1 8 0v4"/></g>` : '';
    return `<g class="${cls}">
      ${isF ? `<rect class="pulse" x="${x - 4}" y="${p.y - H / 2 - 4}" width="${p.w + 8}" height="${H + 8}" rx="11"/>` : ''}
      <rect class="box" x="${x}" y="${p.y - H / 2}" width="${p.w}" height="${H}" rx="8"
        fill="${isLocked ? 'rgba(120,160,255,.04)' : c.f}" stroke="${isLocked ? 'rgba(120,160,255,.22)' : c.s}"
        stroke-width="${isF ? 2 : 1.2}" ${isLocked ? 'stroke-dasharray="3 3"' : ''}/>
      ${bar > 0 ? `<rect x="${x}" y="${p.y + H / 2 - 3}" width="${bar}" height="3" rx="1.5"
        fill="${c.b}" opacity=".9"/>` : ''}
      ${lock}
      <text x="${isLocked ? x + pad : p.x}" y="${p.y + 4}"
        text-anchor="${isLocked ? 'start' : 'middle'}" fill="${c.t}">${esc(label)}</text>
      ${justOpened.includes(n.id) ? `<circle class="burst" cx="${p.x}" cy="${p.y}" r="18"/>` : ''}
    </g>`;
  });

  svg.innerHTML = defs + edges.join('') + pills.join('');
  PREV_LOCKED = lockedNow;

  // remember where each node sits on screen so XP can float up from it
  NODE_SCREEN = {};
  const ctm = svg.getScreenCTM && svg.getScreenCTM();
  if (ctm) {
    Object.entries(pos).forEach(([id, q]) => {
      const pt = svg.createSVGPoint();
      pt.x = q.x; pt.y = q.y - 6;
      const t = pt.matrixTransform(ctm);
      NODE_SCREEN[id] = { x: t.x, y: t.y };
    });
  }
}

/* ------------------------------------------------------------- flashcards */
async function enterCards() {
  const { topics } = await api('/api/topics');
  $('deckTopics').innerHTML = topics.map(t => `<div class="topic-row">
    <div class="meta"><b>${esc(t.title)}</b><small>${t.concepts} concepts</small></div>
    <button class="btn sm ghost" data-g="${esc(t.id)}">Generate</button></div>`).join('');
  $('deckTopics').querySelectorAll('[data-g]').forEach(b => {
    b.onclick = async () => {
      busy(b, true, '');
      try {
        const r = await api('/api/flashcards/generate', { topic: b.dataset.g });
        b.innerHTML = `+${r.added}`;
        await loadCards();
      } catch (e) { b.innerHTML = 'failed'; }
    };
  });
  await loadCards();
}

async function loadCards() {
  CARDS = await api('/api/flashcards');
  $('stTotal').textContent = CARDS.stats.total;
  $('stDue').textContent = CARDS.stats.due;
  $('stReps').textContent = CARDS.stats.reviews;
  $('cardPill').innerHTML = `<b>${CARDS.stats.due}</b> due · ${CARDS.stats.total} cards`;
  CARD_I = 0;
  renderCard();
}

function renderCard() {
  const stage = $('cardStage');
  if (!CARDS.due.length || CARD_I >= CARDS.due.length) {
    stage.innerHTML = `<div class="empty">${CARDS.stats.total
      ? 'Nothing due right now. Come back later — the schedule decides.'
      : 'No cards yet. Generate a deck from a topic on the right.'}</div>`;
    return;
  }
  const c = CARDS.due[CARD_I];
  const pv = previewIntervals(c);
  stage.innerHTML = `
    <div class="qmeta"><span class="qconcept">${esc(c.topic)}</span>
      <span class="chip why" style="margin-left:auto">revision</span></div>
    <div class="flash" id="flash">
      <div class="flash-inner">
        <div class="flash-face">
          <div class="kind">Revise</div>
          <div class="txt">${md(c.front)}</div>
          <div class="hintline">Click to reveal</div>
        </div>
        <div class="flash-face back">
          <div class="kind">${esc(c.concept)}</div>
          <div class="sub">${md(c.back)}</div>
        </div>
      </div>
    </div>
    <div class="grade-row" id="grades" hidden>
      <button class="btn ghost" data-g="again">Again <em>${pv.again}</em></button>
      <button class="btn ghost" data-g="hard">Hard <em>${pv.hard}</em></button>
      <button class="btn light" data-g="good">Good <em>${pv.good}</em></button>
      <button class="btn" data-g="easy">Easy <em>${pv.easy}</em></button>
    </div>
    <div class="small muted" id="cardMsg" style="margin-top:10px">Card ${CARD_I + 1} of ${CARDS.due.length}</div>`;
  $('flash').onclick = () => {
    $('flash').classList.add('flipped');
    $('grades').hidden = false;
    $('grades').querySelectorAll('[data-g]').forEach(b => {
      b.onclick = (ev) => { ev.stopPropagation(); gradeCard(c.id, b.dataset.g, b); };
    });
  };
}

/** Mirrors engine/flashcards.review so the buttons can promise a real interval.
 *  If you change the scheduling there, change it here too. */
function previewIntervals(card) {
  const Q = { again: 1, hard: 3, good: 4, easy: 5 };
  const out = {};
  for (const k in Q) {
    const q = Q[k];
    let ease = card.ease || 2.5, interval = card.interval || 0, reps = card.reps || 0;
    if (q < 3) { reps = 0; interval = 600; }
    else if (q === 3) {
      reps += 1;
      interval = reps === 1 ? 600 : Math.max(interval * 1.2, 86400);
    } else if (q === 4) {
      reps += 1;
      interval = reps === 1 ? 86400 : reps === 2 ? 259200 : Math.max(interval * ease, 86400);
    } else {
      reps += 1;
      interval = reps === 1 ? 345600 : Math.max(interval * ease * 1.3, 345600);
    }
    ease = Math.max(1.3, Math.min(3.0, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))));
    out[k] = interval < 3600 ? Math.max(1, Math.round(interval / 60)) + 'm'
           : interval < 129600 ? Math.round(interval / 3600) + 'h'
           : Math.round(interval / 86400) + 'd';
  }
  return out;
}

async function gradeCard(id, grade, btn) {
  busy(btn, true, '');
  try {
    const r = await api(`/api/flashcards/${id}/review`, { grade });
    $('cardMsg').innerHTML = `Scheduled again in <b>${esc(r.next_in)}</b>${r.xp_earned ? ` · +${r.xp_earned} XP` : ''}`;
    CARD_I++;
    setTimeout(() => { loadCards(); }, 420);
  } catch { busy(btn, false); }
}

/* ---------------------------------------------------------------- profile */
function renderProfile() {
  const g = SUM.game;
  $('profName').textContent = ME.name;
  $('profLevel').innerHTML = `Level <b>${g.level}</b> · ${esc(g.title)}`;
  $('profStreak').innerHTML = `<b>${g.streak}</b>-day streak`;
  $('profEmail').textContent = ME.email;
  $('profKey').textContent = ME.has_key ? `${ME.key_provider} · ${ME.key_hint}` : 'project default';
  $('profModel').textContent = SUM.llm.available ? SUM.llm.provider : 'offline (authored content)';

  const bd = g.breakdown.length ? g.breakdown : [];
  $('xpBreak').innerHTML = bd.length ? bd.map(b => `<div class="kv">
      <span>${esc(b.kind)} <span class="small muted">· ${b.n} events</span></span>
      <span class="mono">${b.amount > 0 ? '+' : ''}${b.amount}</span></div>`).join('')
    : `<div class="empty">No XP yet. XP is paid per point of mastery gained.</div>`;

  $('xpRecent').innerHTML = (g.recent || []).length
    ? g.recent.map(e => `<div><span class="t">·</span><span class="a">${esc(e.kind)} ${e.amount > 0 ? '+' : ''}${e.amount} <span style="color:#71717A">${esc(e.detail || '')}</span></span></div>`).join('')
    : '<div class="muted">Nothing yet.</div>';

  $('achList2').innerHTML = SUM.achievements.map(a => `
    <div class="ach-item ${a.earned ? 'on' : ''}"><span class="dot"></span>
      <div><b>${esc(a.name)}</b><small>${esc(a.desc)}</small></div></div>`).join('');
}

/* --------------------------------------------------------------- settings */
async function enterSettings() {
  const s = await api('/api/settings');
  $('setModel').textContent = s.default_available ? s.default_provider : 'offline';
  $('setOwn').textContent = s.has_key ? 'yes' : 'no';
  $('setMasked').textContent = s.masked || 'none';
  $('setEnc').textContent = 'Fernet';
  $('keyState').innerHTML = s.has_key
    ? `<div class="note ok-note">A key is saved: <b>${esc(s.masked)}</b> (${esc(s.provider)}). It is used for
       your requests only, and is never sent back to this page.</div>`
    : `<div class="note">No personal key. Requests use the project default
       (${esc(s.default_provider)}).</div>`;
}

$('keySave').onclick = async () => {
  const k = $('keyInput').value.trim();
  const msg = $('keyMsg'); msg.className = 'msg'; msg.textContent = '';
  busy($('keySave'), true, 'Saving');
  try {
    const r = await api('/api/settings/key', { key: k });
    msg.textContent = `Saved ${r.provider} key ${r.masked}.`;
    $('keyInput').value = '';
    await enterSettings();
    await loadSummary();
  } catch (e) { msg.className = 'msg bad'; msg.textContent = e.message; }
  finally { busy($('keySave'), false); }
};

$('keyTest').onclick = async () => {
  const msg = $('keyMsg'); msg.className = 'msg'; msg.textContent = '';
  busy($('keyTest'), true, 'Testing');
  try {
    const r = await api('/api/settings/key/test', {});
    if (r.works) msg.textContent = `Key works (${r.provider}, ${r.source}).`;
    else { msg.className = 'msg bad'; msg.textContent = `Key did not work: ${r.error || r.source}`; }
  } catch (e) { msg.className = 'msg bad'; msg.textContent = e.message; }
  finally { busy($('keyTest'), false); }
};

$('keyClear').onclick = async () => {
  const msg = $('keyMsg'); msg.className = 'msg'; msg.textContent = '';
  try {
    await api('/api/settings/key', null, 'DELETE');
    msg.textContent = 'Key removed. Back to the project default.';
    await enterSettings(); await loadSummary();
  } catch (e) { msg.className = 'msg bad'; msg.textContent = e.message; }
};

/* ------------------------------------------------------- add syllabus modal */
function openSyl() {
  const m = $('sylModal');
  if (!m) return;
  m.hidden = false;
  $('sylMsg').textContent = '';
  setTimeout(() => $('sylText').focus(), 60);
}
function closeSyl() {
  const m = $('sylModal');
  if (m) m.hidden = true;
}

if ($('openSyl')) $('openSyl').onclick = openSyl;
if ($('sylClose')) $('sylClose').onclick = closeSyl;
if ($('sylModal')) {
  $('sylModal').onclick = (e) => { if (e.target.id === 'sylModal') closeSyl(); };
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && $('sylModal') && !$('sylModal').hidden) closeSyl();
});

async function uploadPdf(file) {
  const msg = $('sylMsg');
  if (file.size > 12 * 1024 * 1024) { msg.textContent = 'That PDF is over 12 MB.'; return; }
  msg.textContent = `Reading ${file.name}…`;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/api/syllabus/pdf', { method: 'POST', body: fd, credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not read that PDF.');
    $('sylText').value = data.text;
    msg.textContent = `${data.pages} page(s) read. Edit the text if you like, then generate.`;
  } catch (err) {
    msg.textContent = err.message;
  }
}

const dropZone = $('dropZone');
if (dropZone) {
  ['dragenter', 'dragover'].forEach(ev => dropZone.addEventListener(ev, (e) => {
    e.preventDefault(); e.stopPropagation(); dropZone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => dropZone.addEventListener(ev, (e) => {
    e.preventDefault(); e.stopPropagation(); dropZone.classList.remove('over');
  }));
  dropZone.addEventListener('drop', (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    if (!/\.pdf$/i.test(f.name)) { $('sylMsg').textContent = 'Drop a PDF, or paste the text.'; return; }
    uploadPdf(f);
  });
}

if ($('sylGo')) $('sylGo').onclick = async () => {
  const text = $('sylText').value.trim();
  const msg = $('sylMsg');
  if (text.length < 12) { msg.textContent = 'Paste a syllabus or drop a PDF first.'; return; }
  busy($('sylGo'), true, 'Generating');
  msg.textContent = 'Building the concept graph and question bank…';
  try {
    const r = await api('/api/syllabus', { text });
    msg.innerHTML = `Created <b>${esc(r.topic.title)}</b> — ${r.topic.concepts} concepts, ` +
                    `${r.topic.items} items. Opening it now…`;
    $('sylText').value = '';
    await loadSummary();
    setTimeout(() => { closeSyl(); location.hash = '#/learn'; }, 1100);
  } catch (e) {
    const p = e.payload && e.payload.problems ? e.payload.problems.slice(0, 3).join(' · ') : e.message;
    msg.textContent = p;
  } finally { busy($('sylGo'), false); }
};

/* -------------------------------------------------------------- shortcuts */
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toUpperCase();
  if (tag === 'TEXTAREA' || tag === 'INPUT') {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && $('sub')) $('sub').click();
    return;
  }
  if (!window.__keys) return;
  if (['1', '2', '3', '4'].includes(e.key)) window.__keys(Number(e.key) - 1);
  if (e.key === 'Enter') window.__keys(5);
});

window.addEventListener('resize', () => STATE && drawGraph());

/* ------------------------------------------------------------------- boot */
(async function boot() {
  try {
    const r = await api('/api/auth/me', null, 'GET', true);
    ME = r.user;
    await loadSummary();
  } catch { ME = null; }
  if (!location.hash) location.hash = ME ? '#/app' : '#/';
  route();
})();
