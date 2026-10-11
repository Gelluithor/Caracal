'use strict';
/* CARACAL web administration: one screen - what it plays, its playlist, logins of web pages and on-screen
   notifications. The same look as CARACAL Fleet (style.css). Czech and English; every text is written as T(cs, en). */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
};
const S = {
  lang: store.get('caracal-language') === 'en' ? 'en' : 'cs', setup: false,
  view: 'dash', tab: '', system: null, status: null, assets: [], options: [], profiles: [], ok: true,
  open: new Set(), openScale: new Set(), newToken: null, editor: null, picture: null,
};
const T = (cs, en) => (S.lang === 'en' ? en : cs);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const when = t => (t ? new Date(t * 1000).toLocaleString(S.lang === 'en' ? 'en-GB' : 'cs-CZ') : T('nikdy', 'never'));
const fmtSize = n => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' kB');
const pct = v => Math.round(Number(v || 1) * 100);
const parseScale = v => {
  const s = Number(String(v ?? '').trim().replace(',', '.'));
  if (!Number.isFinite(s) || s < .5 || s > 3) throw new Error(T('Měřítko musí být číslo od 0,5 do 3,0.', 'The scale must be a number from 0.5 to 3.0.'));
  return s;
};

// ------------------------------------------------------------------ icons (the set of CARACAL Fleet)
const ICONS = {
  dash: 'M3 13h8V3H3zm0 8h8v-6H3zm10 0h8V11h-8zm0-18v6h8V3z',
  playlist: 'M4 6h12M4 12h12M4 18h8M18 15v6l4-3z',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4M12 15v2',
  bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0',
  web: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18',
  image: 'M4 4h16v16H4zM4 16l5-5 4 4 3-3 4 4M15 9h.01',
  video: 'M3 6h13v12H3zM16 10l5-3v10l-5-3z',
  grafana: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  play: 'M7 4l13 8-13 8z',
  next: 'M5 4l10 8-10 8zM19 5v14',
  snow: 'M12 2v20M4 6l16 12M20 6 4 18',
  restart: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5',
  power: 'M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0',
  refresh: 'M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  plus: 'M12 5v14M5 12h14',
  upload: 'M12 21V9M7 14l5-5 5 5M5 3h14',
  drag: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01',
  chev: 'M7 10l5 5 5-5',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  menu: 'M3 6h18M3 12h18M3 18h18',
  x: 'M18 6 6 18M6 6l12 12',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  auto: 'M12 21a9 9 0 1 0 0-18v18z',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4.5M12 17h.01',
  logout: 'M9 21H5V3h4M16 17l5-5-5-5M21 12H9',
  key: 'M15 7a4 4 0 1 1-3.9 5H9v3H6v3H3v-3l6.1-6.1A4 4 0 0 1 15 7zM16 8h.01',
  sound: 'M11 5 6 9H3v6h3l5 4zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13',
  check: 'M20 6 9 17l-5-5',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7h.01',
  warn: 'M12 3 2 21h20zM12 10v5M12 18h.01',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
  screen: 'M4 5h16v11H4zM8 20h8M12 16v4',
};
const icon = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[n] || ICONS.web}"/></svg>`;

// ------------------------------------------------------------------ API, toasts, dialogs

async function api(url, o = {}) {
  const opts = { method: o.method || (o.json || o.form ? 'POST' : 'GET'), headers: {} };
  if (o.json !== undefined) { opts.body = JSON.stringify(o.json); opts.headers['Content-Type'] = 'application/json'; }
  if (o.form) opts.body = o.form;
  const r = await fetch(url, opts);
  let j = {};
  try { j = await r.json(); } catch (e) { /* no body */ }
  if (r.status === 401 && !url.startsWith('/api/login') && !url.startsWith('/api/me')) { showLogin(); throw new Error(T('Přihlášení vypršelo', 'Session expired')); }
  if (!r.ok) {
    const d = j.detail;
    throw new Error(Array.isArray(d) ? d.map(x => x.msg || JSON.stringify(x)).join(', ') : (d && typeof d === 'object' ? JSON.stringify(d) : d) || T('Požadavek selhal', 'Request failed'));
  }
  return j;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  const close = () => { el.classList.add('out'); setTimeout(() => el.remove(), 320); };
  el.onclick = close;
  $('#toasts').append(el);
  setTimeout(close, kind === 'err' ? 6000 : 3200);
}
const fail = e => toast(e.message || String(e), 'err');

const dlg = () => $('#modal');
function closeModal() {
  const d = dlg();
  if (!d.open) return;
  d.classList.add('closing');
  setTimeout(() => { d.classList.remove('closing'); d.close(); }, 150);
}
// a sheet with a form; onSubmit(data, form) may return false to keep it open
function modal({ title, body, submit = T('Uložit', 'Save'), danger = false, wide = false, onSubmit, onOpen, close = T('Zavřít', 'Close') }) {
  const d = dlg();
  d.className = wide ? 'wide' : '';
  d.innerHTML = `<form class="modal-form" novalidate><header><h2>${title}</h2><button type="button" class="icon-btn" data-close aria-label="${T('Zavřít', 'Close')}">${icon('x')}</button></header>
    <div class="modal-body">${body}</div><div class="form-error" id="mErr"></div>
    <footer>${onSubmit ? `<button type="button" class="btn ghost" data-close>${T('Zrušit', 'Cancel')}</button><button class="btn ${danger ? 'danger' : 'primary'}" id="mSubmit">${submit}</button>` : `<button type="button" class="btn" data-close>${close}</button>`}</footer></form>`;
  const form = $('form', d);
  $$('[data-close]', d).forEach(b => { b.onclick = closeModal; });
  form.onsubmit = async e => {
    e.preventDefault();
    if (!onSubmit) return closeModal();
    if (!form.checkValidity()) { form.reportValidity(); return; }
    const btn = $('#mSubmit', d);
    btn.disabled = true; btn.classList.add('busy'); $('#mErr', d).textContent = '';
    try {
      if (await onSubmit(new FormData(form), form) !== false) closeModal();
    } catch (err) { $('#mErr', d).textContent = err.message; }
    finally { btn.disabled = false; btn.classList.remove('busy'); }
  };
  if (!d.open) d.showModal();
  onOpen && onOpen(form);
  return d;
}
function confirmBox(text, { danger = true, submit = T('Potvrdit', 'Confirm'), title = T('Opravdu?', 'Are you sure?') } = {}) {
  return new Promise(resolve => {
    let ok = false;
    const d = modal({ title, body: `<p>${text}</p>`, submit, danger, onSubmit: () => { ok = true; } });
    d.addEventListener('close', () => resolve(ok), { once: true });
  });
}
const emptyState = (ic, title, text, action = '') => `<div class="empty-state">${icon(ic)}<b>${title}</b>${text ? `<p>${text}</p>` : ''}${action ? `<div>${action}</div>` : ''}</div>`;
const codeBlock = text => `<pre class="code"><code>${esc(text)}</code><button type="button" class="icon-btn" data-do="copy" title="${T('Kopírovat', 'Copy')}">${icon('copy')}</button></pre>`;
const opts = (list, sel) => list.map(([v, cs, en]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(T(cs, en))}</option>`).join('');

// ------------------------------------------------------------------ language and colour scheme

function applyStatic() {
  document.documentElement.lang = S.lang;
  $$('[data-t]').forEach(el => { const [cs, en] = el.dataset.t.split('|'); el.textContent = T(cs, en); });
  $$('[data-lang]').forEach(b => b.classList.toggle('active', b.dataset.lang === S.lang));
}
function setLang(l) {
  S.lang = l === 'en' ? 'en' : 'cs';
  store.set('caracal-language', S.lang);
  applyStatic();
  if (!$('#app').hidden) { buildChrome(); render(true); } else loginTexts();
}
const themePref = () => { const t = store.get('caracal-theme'); return t === 'light' || t === 'dark' ? t : 'auto'; };
function applyTheme() {
  const pref = themePref();
  document.documentElement.dataset.theme = pref === 'auto' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : pref;
  const b = $('#themeBtn');
  if (b) { b.innerHTML = icon(pref === 'light' ? 'sun' : pref === 'dark' ? 'moon' : 'auto'); b.title = T({ light: 'Světlý režim', dark: 'Tmavý režim', auto: 'Podle systému' }[pref], { light: 'Light mode', dark: 'Dark mode', auto: 'Follow the system' }[pref]); }
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// ------------------------------------------------------------------ sign-in

async function loginTexts() {
  $('#loginTitle').textContent = S.setup ? T('Vytvořte účet správce', 'Create the administrator') : T('Přihlášení', 'Sign in');
  $('#loginSub').textContent = S.setup ? T('První spuštění: tento účet spravuje obrazovku. Heslo musí mít nejméně 10 znaků.', 'First start: this account manages the screen. The password needs at least 10 characters.')
    : T('Správa obrazovky CARACAL', 'Administration of this CARACAL screen');
}
async function showLogin() {
  try { S.setup = (await api('/api/setup-status')).needed; } catch (e) { S.setup = false; }
  $('#app').hidden = true;
  $('#login').hidden = false;
  loginTexts();
  applyStatic();
  $('#loginForm [name=username]').focus();
}
$('#loginForm').onsubmit = async e => {
  e.preventDefault();
  const btn = $('#loginBtn');
  btn.classList.add('busy');
  $('#loginError').textContent = '';
  try {
    await api(S.setup ? '/api/setup' : '/api/login', { form: new FormData(e.target) });
    await showApp();
  } catch (err) { $('#loginError').textContent = err.message; }
  finally { btn.classList.remove('busy'); }
};

// ------------------------------------------------------------------ the shell: navigation, toolbar, routing

const NAV = [['dash', 'dash', 'Přehled', 'Overview'], ['playlist', 'playlist', 'Playlist', 'Playlist'], ['logins', 'lock', 'Přihlášení webů', 'Website logins'], ['notify', 'bell', 'Oznámení', 'Notifications']];
const NOTIFY_TABS = [['queue', 'Fronta', 'Queue'], ['look', 'Vzhled', 'Look'], ['settings', 'Nastavení', 'Settings'], ['apps', 'Aplikace a tokeny', 'Apps and tokens'], ['watchers', 'Hlídače', 'Watchers'], ['audit', 'Audit', 'Audit log']];

function buildChrome() {
  $('#nav').innerHTML = NAV.map(([v, ic, cs, en]) => `<a href="#/${v}" data-nav="${v}">${icon(ic)}<span>${T(cs, en)}</span>${v === 'notify' ? '<em id="ntfBadge" hidden></em>' : ''}</a>`).join('');
  $('#menuBtn').innerHTML = icon('menu');
  $('#menuBtn').setAttribute('aria-label', T('Menu', 'Menu'));
  $('#topActions').innerHTML = `<span class="live-dot ${S.ok ? 'ok' : 'bad'}" id="liveDot" title="${S.ok ? T('Zařízení odpovídá', 'The device answers') : T('Zařízení neodpovídá', 'The device does not answer')}"></span>
    <button class="icon-btn" data-do="refresh" title="${T('Obnovit', 'Refresh')}">${icon('refresh')}</button>
    <button class="btn" data-do="restartPlayer">${icon('restart')}<span>${T('Restart přehrávače', 'Restart player')}</span></button>
    <div class="menu"><button class="icon-btn" data-do="menu" aria-haspopup="menu" title="${T('Další', 'More')}">${icon('more')}</button>
      <div class="menu-pop" role="menu" hidden>
        <button class="mi" data-do="help">${icon('help')}${T('Nápověda', 'Help')}</button>
        <button class="mi" data-do="logout">${icon('logout')}${T('Odhlásit', 'Sign out')}</button><hr>
        <button class="mi danger" data-do="reboot">${icon('power')}${T('Restartovat zařízení', 'Restart the device')}</button>
      </div></div>`;
  applyTheme();
  applyStatic();
  updateNav();
}
function updateNav() {
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.nav === S.view));
  const w = S.queueWaiting;
  const b = $('#ntfBadge');
  if (b) { b.hidden = !w; b.textContent = w || ''; }
}
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  const view = NAV.some(n => n[0] === parts[0]) ? parts[0] : 'dash';
  return { view, tab: view === 'notify' ? (NOTIFY_TABS.some(t => t[0] === parts[1]) ? parts[1] : 'queue') : '' };
}
function setTitle(title, crumb = '') {
  $('#title').textContent = title;
  $('#crumb').textContent = crumb;
  document.title = `${title} · CARACAL`;
}
async function render(full = false) {
  const r = parseRoute();
  const changed = full || r.view !== S.view || r.tab !== S.tab;
  S.view = r.view; S.tab = r.tab;
  updateNav();
  $('#aside').classList.remove('open');
  const view = $('#view');
  try {
    await VIEWS[S.view](view, changed);
    if (changed) { view.classList.remove('enter'); void view.offsetWidth; view.classList.add('enter'); }
  } catch (e) {
    view.innerHTML = `<section class="card">${emptyState('warn', T('Stránku se nepodařilo načíst', 'The page could not be loaded'), esc(e.message))}</section>`;
  }
}
window.addEventListener('hashchange', () => render());
window.addEventListener('scroll', () => $('#topbar').classList.toggle('scrolled', scrollY > 8), { passive: true });

async function showApp() {
  $('#login').hidden = true;
  $('#app').hidden = false;
  buildChrome();
  await loadSystem().catch(() => {});
  await render(true);
}

async function loadSystem() {
  try {
    S.system = await api('/api/system');
    S.ok = true;
  } catch (e) { S.ok = false; throw e; }
  finally {
    const dot = $('#liveDot');
    if (dot) dot.className = 'live-dot ' + (S.ok ? 'ok' : 'bad');
  }
  $('#brandHost').textContent = S.system.hostname || '';
}
async function loadPlayer() {
  const [status, assets, options] = await Promise.all([api('/api/v2/player/status'), api('/api/assets'), api('/api/v5/player/live-options').catch(() => [])]);
  S.status = status; S.assets = assets; S.options = options;
}

// ------------------------------------------------------------------ actions (buttons with data-do)

const ACT = {
  refresh() { loadSystem().catch(() => {}); render(true); },
  menu(b) { const pop = b.nextElementSibling; const open = pop.hidden; closeMenus(); pop.hidden = !open; },
  async restartPlayer() {
    try { await api('/api/v3/player/restart', { method: 'POST' }); toast(T('Přehrávač se restartuje', 'The player restarts')); } catch (e) { fail(e); }
  },
  async reboot() {
    closeMenus();
    if (!await confirmBox(T('Restartovat celé zařízení? Administrace bude asi minutu nedostupná.', 'Restart the whole device? The administration is unavailable for about a minute.'), { submit: T('Restartovat', 'Restart') })) return;
    try {
      await api('/api/system/reboot', { json: { confirm: 'REBOOT' } });
      toast(T('Zařízení se restartuje…', 'The device restarts…'));
      setTimeout(() => location.reload(), 65000);
    } catch (e) { fail(e); }
  },
  async logout() { await api('/api/logout', { method: 'POST' }).catch(() => {}); location.hash = ''; location.reload(); },
  help() { closeMenus(); helpDialog(); },
  copy(b) {
    const text = b.parentElement.querySelector('code').textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).catch(() => {
      const a = document.createElement('textarea'); a.value = text; document.body.append(a); a.select(); document.execCommand('copy'); a.remove();
    }).finally(() => toast(T('Zkopírováno', 'Copied')));
  },
};
function closeMenus() { $$('.menu-pop').forEach(p => { p.hidden = true; }); }
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-do]');
  if (!e.target.closest('.menu')) closeMenus();
  if (b && ACT[b.dataset.do] && !b.disabled) {
    e.preventDefault();
    try { await ACT[b.dataset.do](b, e); } catch (err) { fail(err); }
  }
  const lang = e.target.closest('[data-lang]');
  if (lang) setLang(lang.dataset.lang);
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenus(); });
$('#menuBtn').onclick = () => $('#aside').classList.toggle('open');
$('#themeBtn').onclick = () => {
  const next = { auto: 'light', light: 'dark', dark: 'auto' }[themePref()];
  store.set('caracal-theme', next === 'auto' ? null : next);
  applyTheme();
};

function helpDialog() {
  const card = (cs, en, pcs, pen, cls = '') => `<div class="${cls}"><b>${T(cs, en)}</b><p>${T(pcs, pen)}</p></div>`;
  modal({
    title: T('Nápověda', 'Help'), wide: true,
    body: `<div class="help-grid">
      ${card('Playlist', 'Playlist', 'Položky se přehrávají postupně; doba zobrazení je počet sekund na obrazovce. Pořadí změníte přetažením za úchyt. Velikost 100 % je původní velikost stránky, televizním dashboardům obvykle sedí 110 až 150 %.', 'Items play one after another; the display time is the number of seconds on the screen. Drag the handle to change the order. A size of 100 % is the original size of the page; TV dashboards usually look best at 110 to 150 %.')}
      ${card('Kolekce Grafany', 'Grafana collections', 'Kolekce přehraje všechny dashboardy Grafany s vybraným tagem, které vidí účet Guest. Nové dashboardy s tagem se přidají samy.', 'A collection plays every Grafana dashboard with the chosen tag that the Guest account can see. New dashboards with the tag are added on their own.')}
      ${card('Přihlášení webů', 'Website logins', 'Profil uloží šifrované jméno a heslo. Buď vyplní přihlašovací formulář na stránce, nebo odpoví na vyskakovací okno prohlížeče (HTTP přihlášení). Profil přiřadíte položce playlistu.', 'A profile stores an encrypted user name and password. It either fills in the log-in form on the page or answers the browser pop-up (HTTP log-in). Assign the profile to a playlist item.')}
      ${card('Oznámení', 'Notifications', 'Jiné aplikace posílají oznámení na obrazovku přes API s vlastním tokenem; hlídače se samy ptají jiných aplikací. Oznámení se zobrazují po jednom, důležitější mají přednost.', 'Other apps send notifications to the screen through the API with their own token; watchers ask other apps on their own. Notifications are shown one at a time, more important ones first.')}
      ${card('Restart přehrávače', 'Restart player', 'Restartuje jen prohlížeč na obrazovce. Pomůže, když stránka zamrzne.', 'Restarts only the browser on the screen. It helps when a page freezes.')}
      ${card('Restart zařízení', 'Restart the device', 'Restartuje celé zařízení. Administrace bude asi minutu nedostupná.', 'Restarts the whole device. The administration is unavailable for about a minute.', 'warn')}
    </div>`,
  });
}

// ------------------------------------------------------------------ overview

const VIEWS = {};
const live = { timer: null, lastId: null, overlay: null };

function liveLabel(s) {
  if (!s || !s.player_online) return ['bad', T('Přehrávač offline', 'Player offline')];
  if (s.collection_frozen) return ['frozen', T('Kolekce zamražena', 'Collection frozen')];
  if (s.frozen) return ['frozen', T('Zamraženo', 'Frozen')];
  return ['ok', T('Přehrává se', 'Playing')];
}
function kindOf(id) {
  const a = S.assets.find(x => String(x.id) === String(id));
  if (a) return a.kind;
  return Number(id) >= 100000 ? 'grafana-tag' : 'web';
}
const kindIcon = k => icon(k === 'grafana-tag' ? 'grafana' : k === 'image' ? 'image' : k === 'video' ? 'video' : 'web');
const kindLabel = k => ({ web: T('Webová stránka', 'Web page'), image: T('Obrázek', 'Picture'), video: 'Video', 'grafana-tag': T('Kolekce Grafany', 'Grafana collection') }[k] || k);

function liveSelectHtml() {
  let html = '', open = false;
  for (const o of S.options) {
    if (o.type === 'collection') {
      if (open) html += '</optgroup>';
      html += `<optgroup label="${esc(T('Kolekce Grafany', 'Grafana collection'))}: ${esc(o.name)} (${o.count})"><option value="collection:${o.id}">${esc(T('Celá kolekce', 'Whole collection'))} · ${esc(o.name)}</option>`;
      open = true;
    } else if (o.type === 'dashboard') {
      html += `<option value="dashboard:${o.id}">↳ ${esc(o.name.replace(/^.*? · /, ''))}</option>`;
    } else {
      if (open) { html += '</optgroup>'; open = false; }
      html += `<option value="asset:${o.id}">${esc(o.name)}</option>`;
    }
  }
  return html + (open ? '</optgroup>' : '');
}

function drawLive() {
  const s = S.status, scr = $('#nowScreen');
  if (!scr) return;
  const [cls, label] = liveLabel(s);
  const id = s ? s.current_id : null;
  scr.classList.toggle('off', !s || !s.player_online);
  $('.screen-state', scr).innerHTML = `<span class="badge dot ${cls === 'ok' ? '' : cls}">${esc(label)}</span>`;
  $('.screen-name', scr).textContent = (s && s.current_name) || T('Žádná položka', 'No item');
  $('.screen-sub', scr).textContent = s && s.player_online && id != null ? kindLabel(kindOf(id)) : T('Čeká se na přehrávač', 'Waiting for the player');
  const kind = id != null ? kindOf(id) : 'web';
  if (scr.dataset.kind !== kind) { scr.querySelector(':scope > .ic').outerHTML = kindIcon(kind); scr.dataset.kind = kind; }
  const p = s && s.duration && s.remaining != null && !s.frozen ? Math.max(0, Math.min(100, (1 - s.remaining / s.duration) * 100)) : (s && s.frozen ? 100 : 0);
  $('.prog i', scr).style.width = p + '%';
  if (live.lastId !== null && id !== live.lastId) { scr.classList.remove('switched'); void scr.offsetWidth; scr.classList.add('switched'); }
  live.lastId = id;
  const frozen = s && (s.frozen || s.collection_frozen);
  $('#nowCount').textContent = !s || !s.player_online ? '—' : frozen ? T('Zamraženo', 'Frozen') : s.remaining == null ? '—' : `${s.remaining} s`;
  $('#nowCountLabel').textContent = !s || !s.player_online ? T('Přehrávač neodpovídá', 'The player does not answer') : frozen ? T('až do odmrazení', 'until resumed') : T('do další položky', 'to the next item');
  $('#liveResume').hidden = !frozen;
  const sel = $('#liveSelect');
  if (sel && id != null && document.activeElement !== sel && !sel.dataset.touched) {
    const v = (Number(id) >= 100000 ? 'dashboard:' : 'asset:') + id;
    if (sel.value !== v && [...sel.options].some(o => o.value === v)) { sel.value = v; liveSelectChanged(); }
  }
}
function liveSelectChanged() {
  const sel = $('#liveSelect');
  if (!sel) return;
  const col = sel.value.startsWith('collection:');
  $('#liveShow').innerHTML = icon('play') + `<span>${col ? T('Přehrát kolekci', 'Play the collection') : T('Zobrazit teď', 'Show now')}</span>`;
  $('#liveFreeze').innerHTML = icon('snow') + `<span>${col ? T('Zamrazit kolekci', 'Freeze the collection') : T('Zamrazit', 'Freeze')}</span>`;
  $('#liveHint').textContent = col ? T('Přehrání kolekci projede jednou a vrátí se do playlistu, zamrazení ji otáčí bez konce.', 'Playing goes through the collection once and returns to the playlist; freezing keeps rotating it.')
    : T('Zobrazit teď ukáže položku hned a playlist pak pokračuje; zamrazení ji nechá na obrazovce, dokud ji neodmrazíte.', 'Show now shows the item right away and the playlist continues; freezing keeps it on the screen until you resume.');
}
async function liveCommand(action, extra = {}) {
  await api('/api/v6/player/control', { json: { action, ...extra } });
  const msg = { show: T('Přepínám obrazovku', 'Switching the screen'), freeze: T('Položka zamražena', 'Item frozen'), show_collection: T('Spouštím kolekci', 'Playing the collection'),
    freeze_collection: T('Kolekce zamražena', 'Collection frozen'), unfreeze: T('Přehrávání pokračuje', 'Playback resumed'), next: T('Další položka', 'Next item') }[action];
  toast(msg || T('Odesláno', 'Sent'));
  setTimeout(pollLive, 300);
}
Object.assign(ACT, {
  next: () => liveCommand('next'),
  resume: () => liveCommand('unfreeze'),
  liveShow() { const [t, id] = $('#liveSelect').value.split(':'); return t === 'collection' ? liveCommand('show_collection', { collection_id: Number(id) }) : liveCommand('show', { item_id: Number(id) }); },
  liveFreeze() { const [t, id] = $('#liveSelect').value.split(':'); return t === 'collection' ? liveCommand('freeze_collection', { collection_id: Number(id) }) : liveCommand('freeze', { item_id: Number(id) }); },
  showItem: b => liveCommand('show', { item_id: Number(b.dataset.id) }),
  freezeItem: b => liveCommand('freeze', { item_id: Number(b.dataset.id) }),
});
async function pollLive() {
  if (document.hidden || $('#app').hidden || !['dash', 'playlist'].includes(S.view)) return;
  try {
    S.status = await api('/api/v2/player/status');
    if (S.view === 'dash') drawLive();
    if (S.view === 'playlist') markPlaying();
  } catch (e) { /* the next poll tries again */ }
}
setInterval(pollLive, 1000);
setInterval(() => { if (!document.hidden && !$('#app').hidden) loadSystem().then(() => { if (S.view === 'dash') drawMetrics(); }).catch(() => {}); }, 15000);

function meter(label, value, unit, warn, crit) {
  const none = value == null || !Number.isFinite(Number(value));
  const v = none ? 0 : Number(value);
  const cls = none ? 'none' : v >= crit ? 'crit' : v >= warn ? 'warn' : '';
  return `<div class="metric"><span>${label}</span><div class="meter ${cls}"><span>${none ? '—' : (unit === '°C' ? v.toFixed(1) : Math.round(v * 10) / 10) + (unit === '%' ? ' %' : ' ' + unit)}</span><i style="width:${none ? 0 : Math.min(100, unit === '°C' ? v : v)}%"></i></div></div>`;
}
function drawMetrics() {
  const s = S.system, el = $('#metrics');
  if (!s || !el) return;
  el.innerHTML = meter('CPU', s.cpu, '%', 70, 90) + meter('RAM', s.ram, '%', 80, 92) + meter(T('Disk', 'Disk'), s.disk, '%', 80, 92) + meter(T('Teplota', 'Temperature'), s.temperature, '°C', 70, 80);
  const d = Math.floor(s.uptime / 86400), h = Math.floor((s.uptime % 86400) / 3600);
  const cols = S.assets.filter(a => a.kind === 'grafana-tag').length;
  $('#devInfo').innerHTML = `<dt>${T('Název zařízení', 'Device name')}</dt><dd>${esc(s.hostname)}</dd>
    <dt>CARACAL</dt><dd>${esc(s.version || '—')}</dd>
    <dt>${T('Běží', 'Runs')}</dt><dd>${s.runtime === 'docker' ? T('v Dockeru', 'in Docker') : T('přímo v systému', 'directly on the system')}</dd>
    <dt>${T('Doba běhu', 'Uptime')}</dt><dd>${d ? `${d} ${T('d', 'd')} ` : ''}${h} h</dd>
    <dt>Playlist</dt><dd>${S.assets.length} ${T('položek', 'items')}${cols ? ` · ${cols} ${T('kolekcí Grafany', 'Grafana collections')}` : ''}</dd>`;
}

VIEWS.dash = async (root, changed) => {
  setTitle(T('Přehled', 'Overview'));
  await loadPlayer();
  if (changed || !$('#nowScreen')) {
    root.innerHTML = `
      <section class="card now-card">
        <div class="screen" id="nowScreen">${icon('web')}<div class="screen-state"></div><div class="screen-name"></div><div class="screen-sub"></div><div class="prog"><i></i></div></div>
        <div class="now-side">
          <div class="now-count"><b id="nowCount">—</b><span class="muted" id="nowCountLabel"></span></div>
          <div class="action-group"><button class="btn" id="liveResume" data-do="resume" hidden>${icon('play')}<span>${T('Pokračovat', 'Resume')}</span></button><button class="btn" data-do="next">${icon('next')}<span>${T('Další položka', 'Next item')}</span></button></div>
          <div class="pick"><span class="pick-label">${T('Zobrazit na obrazovce', 'Show on the screen')}</span>
            <div class="pick-row"><select id="liveSelect"></select><button class="btn primary" id="liveShow" data-do="liveShow"></button><button class="btn" id="liveFreeze" data-do="liveFreeze"></button></div>
            <small class="muted" id="liveHint"></small></div>
        </div>
      </section>
      <div class="metrics4" id="metrics"></div>
      <div class="grid two">
        <section class="card" id="overlayCard"><div class="card-head"><h2>${icon('screen')}${T('Odpočet na obrazovce', 'Countdown on the screen')}</h2></div>
          <p class="card-intro">${T('Tenká lišta na spodním okraji obrazovky se plní zleva doprava a ukazuje, za jak dlouho se přepne na další položku.', 'A thin bar at the bottom edge of the screen fills from left to right and shows how soon the next item comes.')}</p>
          <div class="setting"><span><b>${T('Zobrazovat odpočet', 'Show the countdown')}</b></span><input type="checkbox" class="switch" id="ovEnabled"></div>
          <div class="setting"><div class="slider-row" style="width:100%"><span>${T('Výška lišty', 'Bar height')}</span><input type="range" id="ovRange" min="2" max="40" step="1"><input type="number" id="ovSize" min="1" step="1"></div></div>
        </section>
        <section class="card"><div class="card-head"><h2>${icon('info')}${T('Zařízení', 'Device')}</h2></div><dl class="kv" id="devInfo"></dl></section>
      </div>
      <section class="card screen-card" id="screenCard"></section>`;
    $('#liveSelect').innerHTML = liveSelectHtml();
    $('#liveSelect').onchange = () => { $('#liveSelect').dataset.touched = '1'; liveSelectChanged(); };
    liveSelectChanged();
    bindOverlay();
    drawScreenCard();
  }
  drawLive();
  drawMetrics();
};

async function bindOverlay() {
  try { live.overlay = await api('/api/v4/player/overlay-settings'); } catch (e) { $('#overlayCard').hidden = true; return; }
  const en = $('#ovEnabled'), range = $('#ovRange'), num = $('#ovSize');
  const show = () => { en.checked = !!live.overlay.enabled; range.value = Math.min(40, live.overlay.size); num.value = live.overlay.size; range.disabled = num.disabled = !en.checked; };
  const save = async (patch, msg) => {
    try { live.overlay = await api('/api/v4/player/overlay-settings', { method: 'PUT', json: { ...live.overlay, ...patch } }); show(); toast(msg); } catch (e) { fail(e); show(); }
  };
  show();
  en.onchange = () => save({ enabled: en.checked }, en.checked ? T('Odpočet zapnut', 'Countdown on') : T('Odpočet vypnut', 'Countdown off'));
  range.oninput = () => { num.value = range.value; };
  range.onchange = () => save({ size: Number(range.value) }, T(`Výška lišty ${range.value} px`, `Bar height ${range.value} px`));
  num.onchange = () => { const v = Math.max(1, Math.round(Number(num.value) || 1)); save({ size: v }, T(`Výška lišty ${v} px`, `Bar height ${v} px`)); };
}

// ------------------------------------------------------------------ what the screen shows (a picture taken by the overlay)

window.caracalScreenshot = async function () {
  const { id } = await api('/api/screenshot', { method: 'POST' });
  const until = Date.now() + 20000;
  while (Date.now() < until) {
    await new Promise(r => setTimeout(r, 700));
    const s = await api('/api/screenshot');
    if (s.id === id) { if (s.error) throw new Error(s.error); return '/api/screenshot.jpg?t=' + Math.round(s.taken * 1000); }
  }
  throw new Error(T('Obrazovka neodpověděla. Běží overlay (služba caracal-overlay)?', 'The screen did not answer. Is the overlay running (caracal-overlay service)?'));
};
const ago = t => { const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 60 ? T('před chvílí', 'just now') : s < 3600 ? T(`před ${Math.round(s / 60)} min`, `${Math.round(s / 60)} min ago`) : when(t); };
const shot = { auto: null };
const screenHidden = () => store.get('caracal-screen-hidden') === '1';
async function drawScreenCard() {
  const card = $('#screenCard');
  if (!card) return;
  let info = {};
  try { info = await api('/api/screenshot'); } catch (e) { /* no picture yet */ }
  const has = info.taken && info.id, hidden = screenHidden();
  card.classList.toggle('collapsed', hidden);
  card.innerHTML = `<div class="card-head"><h2>${icon('eye')}${T('Na obrazovce', 'On screen')}</h2>
      <div class="row-actions">${hidden ? '' : `<span class="muted" id="shotNote">${has ? T('Snímek ', 'Captured ') + ago(info.taken) : ''}</span>
        <label class="check-inline"><input type="checkbox" class="switch" id="shotAuto"> ${T('Každých 10 s', 'Every 10 s')}</label>
        <button class="btn sm" data-do="shotRefresh" id="shotBtn">${icon('refresh')}${has ? T('Obnovit', 'Refresh') : T('Zobrazit obrazovku', 'Show the screen')}</button>`}
        <button class="btn sm ghost" data-do="shotToggle">${hidden ? T('Zobrazit', 'Show') : T('Skrýt', 'Hide')}</button></div></div>
    ${hidden ? '' : `<div class="shot" id="shotBox">${has ? `<img src="/api/screenshot.jpg?t=${Math.round(info.taken * 1000)}" alt="${T('Obrazovka zařízení', 'Device screen')}">` : `<span>${T('Snímek toho, co televize právě ukazuje – stránka, odpočet i oznámení.', 'A picture of what the TV shows right now – the page, the countdown and notifications.')}</span>`}</div>`}`;
  const auto = $('#shotAuto');
  if (auto) {
    auto.checked = !!shot.auto;
    auto.onchange = () => { clearInterval(shot.auto); shot.auto = auto.checked ? setInterval(() => { if (!$('#shotBox')) { clearInterval(shot.auto); shot.auto = null; } else if (!document.hidden) ACT.shotRefresh(); }, 10000) : null; };
  }
}
Object.assign(ACT, {
  async shotRefresh() {
    const btn = $('#shotBtn'), box = $('#shotBox');
    if (!btn || !box) return;
    btn.classList.add('busy');
    try {
      const url = await window.caracalScreenshot();
      const img = new Image();
      img.onload = () => box.replaceChildren(img);
      img.alt = T('Obrazovka zařízení', 'Device screen');
      img.src = url;
      $('#shotNote').textContent = T('Snímek ', 'Captured ') + ago(Date.now() / 1000);
    } catch (e) { $('#shotNote').textContent = e.message; }
    finally { btn.classList.remove('busy'); btn.lastChild.textContent = T('Obnovit', 'Refresh'); }
  },
  shotToggle() {
    const hide = !screenHidden();
    store.set('caracal-screen-hidden', hide ? '1' : '0');
    if (hide) { clearInterval(shot.auto); shot.auto = null; }
    drawScreenCard();
  },
});

// ------------------------------------------------------------------ playlist

const grafanaCfg = a => { try { return JSON.parse(a.source || '{}'); } catch (e) { return {}; } };
const hostOf = u => { try { return new URL(u).host; } catch (e) { return u; } };
function sourceText(a) {
  if (a.kind === 'grafana-tag') { const c = grafanaCfg(a); return `${T('tag', 'tag')} ${c.tag || '?'} · ${hostOf(c.grafana_url || '')}`; }
  if (a.kind === 'web') return a.source;
  return String(a.source || '').split('/').pop();
}
function dashboardsOf(id) { return S.options.filter(o => o.type === 'dashboard' && String(o.parent_id) === String(id)); }
function plRow(a, i) {
  const scalable = a.kind === 'web' || a.kind === 'grafana-tag';
  const prof = a.auth_profile_id ? S.profiles.find(p => String(p.id) === String(a.auth_profile_id)) : null;
  const dashes = a.kind === 'grafana-tag' ? dashboardsOf(a.id) : [];
  const open = S.open.has(String(a.id)), scaleOpen = S.openScale.has(String(a.id));
  const k = a.kind === 'grafana-tag' ? 'grafana' : a.kind;
  let html = `<li class="pl-row" data-id="${a.id}">
    <button class="handle" draggable="true" title="${T('Přetáhněte pro změnu pořadí', 'Drag to change the order')}" aria-label="${T('Přesunout', 'Move')}">${icon('drag')}</button>
    <span class="idx">${i + 1}</span><span class="kind k-${k}">${kindIcon(a.kind)}</span>
    <div class="pl-main"><b>${esc(a.name)}</b><small>${esc(sourceText(a))}</small>
      <div class="row-tags">${scalable ? `<button class="tag" data-do="toggleScale" data-id="${a.id}" title="${T('Velikost obsahu', 'Content size')}">${pct(a.scale)} %</button>` : ''}
        ${a.auth_profile_id ? `<span class="tag accent">${icon('lock')}${esc(prof ? prof.name : T('Přihlášení', 'Login'))}</span>` : ''}
        ${a.kind === 'grafana-tag' ? `<button class="expand ${open ? 'open' : ''}" data-do="toggleDash" data-id="${a.id}">${icon('chev')}${dashes.length} ${T('dashboardů', 'dashboards')}</button>` : ''}</div></div>
    <span class="pl-dur">${a.duration} s</span>
    <span class="pl-actions">${a.kind !== 'grafana-tag' ? `<button class="icon-btn" data-do="showItem" data-id="${a.id}" title="${T('Zobrazit teď', 'Show now')}">${icon('play')}</button><button class="icon-btn" data-do="freezeItem" data-id="${a.id}" title="${T('Zamrazit', 'Freeze')}">${icon('snow')}</button>` : ''}
      <button class="icon-btn" data-do="editItem" data-id="${a.id}" title="${T('Upravit', 'Edit')}">${icon('edit')}</button>
      <button class="icon-btn danger" data-do="deleteItem" data-id="${a.id}" title="${T('Smazat', 'Delete')}">${icon('trash')}</button></span></li>`;
  if (scalable && scaleOpen) {
    html += `<li class="pl-sub" data-for="${a.id}"><div class="slider-row"><span>${T('Velikost obsahu', 'Content size')}</span><input type="range" min="50" max="300" step="5" value="${pct(a.scale)}" data-scale-range="${a.id}"><input type="number" min="50" max="300" step="5" value="${pct(a.scale)}" data-scale-num="${a.id}"></div>
      <div class="presets">${[75, 100, 125, 150].map(v => `<button class="btn sm" data-do="scalePreset" data-id="${a.id}" data-v="${v}">${v} %</button>`).join('')}<button class="btn sm primary" data-do="saveScale" data-id="${a.id}">${T('Uložit velikost', 'Save size')}</button></div></li>`;
  }
  if (open) {
    html += dashes.map(d => `<li class="dash" data-dash="${d.id}"><span class="dash-name">${icon('grafana')}<span>${esc(d.name.replace(/^.*? · /, ''))}</span></span>
      <span class="pl-actions"><button class="icon-btn" data-do="showItem" data-id="${d.id}" title="${T('Zobrazit teď', 'Show now')}">${icon('play')}</button><button class="icon-btn" data-do="freezeItem" data-id="${d.id}" title="${T('Zamrazit', 'Freeze')}">${icon('snow')}</button></span></li>`).join('')
      || `<li class="dash"><span class="muted">${T('Kolekce zatím nemá žádné dashboardy (nebo je Grafana nedostupná).', 'The collection has no dashboards yet (or Grafana is unreachable).')}</span></li>`;
  }
  return html;
}
function markPlaying() {
  const id = S.status && S.status.player_online ? String(S.status.current_id) : '';
  const parent = Number(id) >= 100000 ? String(Math.floor(Number(id) / 100000)) : id;
  $$('.pl-row').forEach(r => r.classList.toggle('playing', r.dataset.id === parent));
  $$('.dash').forEach(r => r.classList.toggle('playing', r.dataset.dash === id));
}
function drawPlaylist() {
  const list = $('#plList');
  if (!list) return;
  $('#plCount').textContent = S.assets.length;
  list.innerHTML = S.assets.map(plRow).join('') || `<li>${emptyState('playlist', T('Playlist je prázdný', 'The playlist is empty'), T('Přidejte webovou stránku, obrázek, video nebo všechny dashboardy Grafany s jedním tagem.', 'Add a web page, a picture, a video or all Grafana dashboards with one tag.'),
    `<button class="btn primary" data-do="addWeb">${icon('plus')}${T('Přidat web', 'Add website')}</button><button class="btn" data-do="upload">${icon('upload')}${T('Nahrát médium', 'Upload media')}</button>`)}</li>`;
  markPlaying();
  bindDrag(list);
  $$('[data-scale-range]', list).forEach(r => { r.oninput = () => { $(`[data-scale-num="${r.dataset.scaleRange}"]`).value = r.value; }; });
  $$('[data-scale-num]', list).forEach(n => { n.oninput = () => { $(`[data-scale-range="${n.dataset.scaleNum}"]`).value = n.value; }; });
}
function bindDrag(list) {
  let dragged = null;
  $$('.handle', list).forEach(h => {
    h.ondragstart = e => { dragged = h.closest('.pl-row'); dragged.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragged.dataset.id); };
    h.ondragend = () => { $$('.pl-row', list).forEach(r => r.classList.remove('dragging', 'drop-before', 'drop-after')); dragged = null; };
  });
  list.ondragover = e => {
    if (!dragged) return;
    e.preventDefault();
    const t = e.target.closest('.pl-row');
    $$('.pl-row', list).forEach(r => r.classList.remove('drop-before', 'drop-after'));
    if (t && t !== dragged) { const r = t.getBoundingClientRect(); t.classList.add(e.clientY < r.top + r.height / 2 ? 'drop-before' : 'drop-after'); }
  };
  list.ondrop = async e => {
    e.preventDefault();
    const t = e.target.closest('.pl-row');
    if (!dragged || !t || t === dragged) return;
    const before = t.classList.contains('drop-before');
    const ids = S.assets.map(a => a.id).filter(id => String(id) !== dragged.dataset.id);
    const at = ids.findIndex(id => String(id) === t.dataset.id) + (before ? 0 : 1);
    ids.splice(at, 0, Number(dragged.dataset.id));
    try {
      await api('/api/playlist/reorder', { method: 'PUT', json: { ids } });
      S.assets = ids.map(id => S.assets.find(a => a.id === id));
      toast(T('Pořadí uloženo', 'Order saved'));
    } catch (err) { fail(err); }
    drawPlaylist();
  };
}

VIEWS.playlist = async (root, changed) => {
  setTitle('Playlist');
  await loadPlayer();
  S.profiles = await api('/api/profiles').catch(() => []);
  if (changed || !$('#plList')) {
    root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">Playlist <span class="badge" id="plCount"></span></h2>
        <button class="btn" data-do="addGrafana">${icon('grafana')}<span>${T('Grafana podle tagu', 'Grafana by tag')}</span></button>
        <button class="btn" data-do="upload">${icon('upload')}<span>${T('Nahrát médium', 'Upload media')}</span></button>
        <button class="btn primary" data-do="addWeb">${icon('plus')}<span>${T('Přidat web', 'Add website')}</span></button></div>
      <ol class="list" id="plList"></ol></section>
      <p class="muted" style="font-size:13px">${T('Pořadí změníte přetažením za úchyt vlevo. Tlačítkem s procenty upravíte velikost stránky, ikonou ▶ položku hned zobrazíte.', 'Drag the handle on the left to change the order. The button with the percentage changes the size of the page; ▶ shows the item right away.')}</p>`;
  }
  drawPlaylist();
};

async function profileOptions(sel = '') {
  if (!S.profiles.length) S.profiles = await api('/api/profiles').catch(() => []);
  return `<option value="">${T('Bez přihlášení', 'No login')}</option>` + S.profiles.map(p => `<option value="${p.id}" ${String(sel) === String(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
}
const scaleField = (v = 1) => `<label>${T('Velikost obsahu', 'Content size')}<input name="scale" inputmode="decimal" value="${String(Number(v || 1)).replace('.', S.lang === 'cs' ? ',' : '.')}" required><small>${T('0,5 = 50 %, 1 = 100 %, 1,3 = 130 %, nejvýš 3', '0.5 = 50 %, 1 = 100 %, 1.3 = 130 %, at most 3')}</small></label>`;

function uploadForm(form) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    const bar = $('.upload-bar', form), fill = $('.upload-bar i', form);
    bar.hidden = false;
    x.upload.onprogress = e => { if (e.lengthComputable) fill.style.width = (e.loaded / e.total * 100) + '%'; };
    x.onload = () => {
      let j = {};
      try { j = JSON.parse(x.responseText); } catch (e) { /* no body */ }
      if (x.status >= 200 && x.status < 300) resolve(j); else reject(new Error(j.detail || T('Nahrání selhalo', 'Upload failed')));
    };
    x.onerror = () => reject(new Error(T('Chyba sítě', 'Network error')));
    x.open('POST', '/api/assets/upload');
    x.send(new FormData(form));
  });
}

Object.assign(ACT, {
  async addWeb() {
    modal({
      title: T('Přidat web', 'Add website'),
      body: `<div class="form"><label>${T('Název', 'Name')}<input name="name" required placeholder="${T('např. Zabbix dashboard', 'e.g. Zabbix dashboard')}"></label>
        <label>URL<input name="source" type="url" required placeholder="https://"></label>
        <div class="row2"><label>${T('Doba zobrazení (s)', 'Display time (s)')}<input name="duration" type="number" min="5" value="30" required></label>${scaleField(1)}</div>
        <label>${T('Přihlašovací profil', 'Login profile')}<select name="auth_profile_id">${await profileOptions()}</select></label></div>`,
      submit: T('Přidat', 'Add'),
      onSubmit: async f => {
        const r = await api('/api/v9/assets/web', { json: { name: f.get('name').trim(), source: f.get('source').trim(), duration: Number(f.get('duration')), scale: parseScale(f.get('scale')), auth_profile_id: f.get('auth_profile_id') || null } });
        toast(T(`Web přidán · ${pct(r.scale)} %`, `Website added · ${pct(r.scale)} %`));
        render(true);
      },
    });
  },
  upload() {
    modal({
      title: T('Nahrát médium', 'Upload media'),
      body: `<div class="form"><label>${T('Soubor (obrázek nebo video)', 'File (picture or video)')}<input name="file" type="file" accept="image/*,video/*" required></label>
        <label>${T('Název', 'Name')}<input name="name" required></label>
        <label>${T('Doba zobrazení (s)', 'Display time (s)')}<input name="duration" type="number" min="5" value="15" required><small>${T('Video se během doby zobrazení opakuje.', 'A video loops during the display time.')}</small></label>
        <div class="upload-bar" hidden style="height:6px;border-radius:6px;background:var(--fill);overflow:hidden"><i style="display:block;height:100%;width:0;background:var(--accent);transition:width .2s"></i></div></div>`,
      submit: T('Nahrát', 'Upload'),
      onOpen: form => { form.file.onchange = () => { const f = form.file.files[0]; if (f && !form.name.value) form.name.value = f.name.replace(/\.[^.]+$/, ''); }; },
      onSubmit: async (f, form) => { await uploadForm(form); toast(T('Médium nahráno', 'Media uploaded')); render(true); },
    });
  },
  addGrafana() { grafanaDialog(); },
  async editItem(b) {
    const a = S.assets.find(x => String(x.id) === b.dataset.id);
    if (!a) return;
    if (a.kind === 'grafana-tag') return grafanaDialog(a);
    const web = a.kind === 'web';
    modal({
      title: T('Upravit položku', 'Edit item'),
      body: `<div class="form"><label>${T('Název', 'Name')}<input name="name" value="${esc(a.name)}" required></label>
        ${web ? `<label>URL<input name="source" type="url" value="${esc(a.source)}" required></label>` : `<label>${T('Soubor', 'File')}<input value="${esc(sourceText(a))}" disabled></label>`}
        <div class="row2"><label>${T('Doba zobrazení (s)', 'Display time (s)')}<input name="duration" type="number" min="5" value="${a.duration}" required></label>${web ? scaleField(a.scale) : '<span></span>'}</div>
        ${web ? `<label>${T('Přihlašovací profil', 'Login profile')}<select name="auth_profile_id">${await profileOptions(a.auth_profile_id)}</select></label>` : ''}</div>`,
      onSubmit: async f => {
        const scale = web ? parseScale(f.get('scale')) : null;
        await api('/api/assets/' + a.id, { method: 'PUT', json: { name: f.get('name').trim(), source: web ? f.get('source').trim() : a.source, duration: Number(f.get('duration')), auth_profile_id: web ? (f.get('auth_profile_id') || null) : null } });
        if (web) await api(`/api/v7/assets/${a.id}/scale`, { method: 'PUT', json: { scale } });
        toast(T('Položka uložena', 'Item saved'));
        render(true);
      },
    });
  },
  async deleteItem(b) {
    const a = S.assets.find(x => String(x.id) === b.dataset.id);
    if (!a || !await confirmBox(T(`Smazat položku <b>${esc(a.name)}</b> z playlistu?`, `Delete <b>${esc(a.name)}</b> from the playlist?`), { submit: T('Smazat', 'Delete') })) return;
    await api('/api/assets/' + a.id, { method: 'DELETE' });
    toast(T('Položka smazána', 'Item deleted'));
    render(true);
  },
  toggleDash(b) { const id = b.dataset.id; S.open.has(id) ? S.open.delete(id) : S.open.add(id); drawPlaylist(); },
  toggleScale(b) { const id = b.dataset.id; S.openScale.has(id) ? S.openScale.delete(id) : S.openScale.add(id); drawPlaylist(); },
  scalePreset(b) { $(`[data-scale-range="${b.dataset.id}"]`).value = b.dataset.v; $(`[data-scale-num="${b.dataset.id}"]`).value = b.dataset.v; },
  async saveScale(b) {
    const v = Number($(`[data-scale-num="${b.dataset.id}"]`).value);
    const r = await api(`/api/v7/assets/${b.dataset.id}/scale`, { method: 'PUT', json: { scale: parseScale(v / 100) } });
    const a = S.assets.find(x => String(x.id) === b.dataset.id);
    if (a) a.scale = r.scale;
    S.openScale.delete(b.dataset.id);
    toast(T(`Velikost ${pct(r.scale)} % uložena`, `Size ${pct(r.scale)} % saved`));
    drawPlaylist();
  },
});

function grafanaDialog(a = null) {
  const c = a ? grafanaCfg(a) : { kiosk: true };
  modal({
    title: a ? T('Upravit kolekci Grafany', 'Edit Grafana collection') : T('Grafana podle tagu', 'Grafana by tag'),
    body: `<div class="form"><p class="muted">${T('CARACAL se zeptá Grafany (účtem Guest) na všechny dashboardy s tagem a přehraje je jeden po druhém. Nové dashboardy s tagem přibudou samy.', 'CARACAL asks Grafana (as the Guest account) for every dashboard with the tag and plays them one after another. New dashboards with the tag are added on their own.')}</p>
      <label>${T('Název kolekce', 'Collection name')}<input name="name" required value="${esc(a ? a.name : '')}" placeholder="${T('např. Dashboardy NOC', 'e.g. NOC dashboards')}"></label>
      <div class="row2"><label>${T('Adresa Grafany', 'Grafana address')}<input name="grafana_url" type="url" required value="${esc(c.grafana_url || '')}" placeholder="https://grafana.example"></label>
      <label>${T('Tag dashboardů', 'Dashboard tag')}<input name="tag" required value="${esc(c.tag || '')}" placeholder="caracal-tv"></label></div>
      <div class="row2"><label>${T('Doba jednoho dashboardu (s)', 'Time per dashboard (s)')}<input name="duration" type="number" min="5" value="${a ? a.duration : 60}" required></label>${scaleField(a ? a.scale : 1)}</div>
      <label class="check"><input type="checkbox" class="switch" name="kiosk" ${c.kiosk !== false ? 'checked' : ''}>${T('Kiosk režim Grafany (bez menu a lišt)', 'Grafana kiosk mode (no menus and bars)')}</label>
      <div class="form-actions start"><button type="button" class="btn sm" data-do="testTag">${icon('check')}${T('Vyzkoušet tag', 'Try the tag')}</button></div><div class="result-box" id="tagResult"></div></div>`,
    submit: a ? T('Uložit', 'Save') : T('Přidat', 'Add'),
    onSubmit: async f => {
      const scale = parseScale(f.get('scale'));
      if (a) {
        const r = await api('/api/grafana-collections/' + a.id, { method: 'PUT', json: { name: f.get('name').trim(), grafana_url: f.get('grafana_url').trim(), tag: f.get('tag').trim(), duration: Number(f.get('duration')), scale, kiosk: f.get('kiosk') === 'on' } });
        await api(`/api/v7/assets/${a.id}/scale`, { method: 'PUT', json: { scale } });
        toast(T(`Kolekce uložena · ${r.dashboards} dashboardů`, `Collection saved · ${r.dashboards} dashboards`));
      } else {
        const fd = new FormData();
        for (const k of ['name', 'grafana_url', 'tag', 'duration']) fd.set(k, String(f.get(k)).trim());
        fd.set('scale', String(scale));
        if (f.get('kiosk') === 'on') fd.set('kiosk', 'on');
        const r = await api('/api/assets/grafana-tag', { form: fd });
        toast(T(`Kolekce přidána · ${r.dashboards} dashboardů`, `Collection added · ${r.dashboards} dashboards`));
      }
      render(true);
    },
  });
}
ACT.testTag = async b => {
  const form = b.closest('form'), box = $('#tagResult');
  const fd = new FormData();
  fd.set('grafana_url', form.grafana_url.value.trim());
  fd.set('tag', form.tag.value.trim());
  b.classList.add('busy');
  try {
    const r = await api('/api/grafana/discover', { form: fd });
    box.innerHTML = `<div class="note ok"><div><b>${T('Nalezeno', 'Found')}: ${r.count}</b><br>${r.dashboards.map(x => esc(x.title)).join(' · ') || T('Žádný dashboard s tímto tagem.', 'No dashboard with this tag.')}</div></div>`;
  } catch (e) { box.innerHTML = `<div class="note bad">${esc(e.message)}</div>`; }
  finally { b.classList.remove('busy'); }
};

// ------------------------------------------------------------------ website logins

VIEWS.logins = async root => {
  setTitle(T('Přihlášení webů', 'Website logins'));
  const [profiles, assets] = await Promise.all([api('/api/profiles'), api('/api/assets')]);
  S.profiles = profiles; S.assets = assets;
  const used = id => assets.filter(a => String(a.auth_profile_id) === String(id)).length;
  root.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${T('Přihlašovací profily', 'Login profiles')} <span class="badge">${profiles.length}</span></h2>
      <button class="btn primary" data-do="addProfile">${icon('plus')}<span>${T('Nový profil', 'New profile')}</span></button></div>
    <p class="card-intro" style="padding-top:12px">${T('Weby, které chtějí jméno a heslo: CARACAL se přihlásí sám. Údaje jsou uložené šifrovaně jen v tomto zařízení; profil pak přiřadíte položce playlistu.', 'Websites that ask for a user name and password: CARACAL logs in on its own. The credentials are stored encrypted on this device only; assign the profile to a playlist item.')}</p>
    <ul class="list">${profiles.map(p => {
      const http = p.auth_type === 'http', n = used(p.id);
      return `<li class="row"><span class="row-icon accent">${icon('lock')}</span><div class="row-main"><b>${esc(p.name)}</b>
        <small>${esc(http ? p.target_url : `${p.login_url} → ${p.target_url}`)}</small>
        <div class="row-tags"><span class="tag ok">${icon('shield')}${T('Šifrováno', 'Encrypted')}</span><span class="tag info">${http ? T('HTTP přihlášení', 'HTTP log-in') : T('Formulář na stránce', 'Log-in form')}</span>
          <span class="tag">${n ? T(`${n} v playlistu`, `${n} in the playlist`) : T('nepoužito', 'not used')}</span></div></div>
        <div class="row-actions"><button class="icon-btn" data-do="editProfile" data-id="${p.id}" title="${T('Upravit', 'Edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="deleteProfile" data-id="${p.id}" title="${T('Smazat', 'Delete')}">${icon('trash')}</button></div></li>`;
    }).join('') || `<li>${emptyState('lock', T('Zatím žádný profil', 'No profile yet'), T('Vytvořte profil pro Zabbix, Grafanu nebo jiný web, který vyžaduje přihlášení.', 'Create a profile for Zabbix, Grafana or another website that needs a log-in.'), `<button class="btn primary" data-do="addProfile">${icon('plus')}${T('Nový profil', 'New profile')}</button>`)}</li>`}</ul></section>`;
};
function profileDialog(p = null) {
  const keep = T('Prázdné = beze změny', 'Empty = unchanged'), type = p ? p.auth_type || 'form' : 'form';
  modal({
    title: p ? T('Upravit profil', 'Edit profile') : T('Nový přihlašovací profil', 'New login profile'), wide: true,
    body: `<div class="form">
      <label>${T('Typ přihlášení', 'Login type')}<select name="auth_type">${opts([['form', 'Přihlašovací formulář na stránce', 'Log-in form on the page'], ['http', 'HTTP přihlášení (vyskakovací okno prohlížeče)', 'HTTP log-in (the browser pop-up)']], type)}</select></label>
      <div class="note" data-http>${T('Pro weby, kde prohlížeč sám ukáže okno se jménem a heslem (HTTP Basic/Digest). Přihlášení platí jen pro server z cílové adresy.', 'For websites where the browser itself asks for a user name and password (HTTP Basic/Digest). The log-in is used only for the server of the target address.')}</div>
      <label>${T('Název', 'Name')}<input name="name" value="${esc(p ? p.name : '')}" required></label>
      <div class="row2"><label data-form>${T('Adresa přihlašovací stránky', 'Log-in page address')}<input name="login_url" type="url" value="${esc(p ? p.login_url : '')}" data-req></label>
      <label>${T('Cílová adresa', 'Target address')}<input name="target_url" type="url" value="${esc(p ? p.target_url : '')}" required placeholder="https://"></label></div>
      <div class="row2"><label>${T('Uživatel', 'User')}<input name="username" autocomplete="off" ${p ? `placeholder="${keep}"` : 'required'}></label>
      <label>${T('Heslo', 'Password')}<input name="password" type="password" autocomplete="new-password" ${p ? `placeholder="${keep}"` : 'required'}></label></div>
      <div class="row3" data-form><label>${T('Selektor uživatele', 'User selector')}<input name="user_selector" value="${esc(p ? p.user_selector : '#name')}" data-req></label>
      <label>${T('Selektor hesla', 'Password selector')}<input name="pass_selector" value="${esc(p ? p.pass_selector : '#password')}" data-req></label>
      <label>${T('Selektor tlačítka', 'Button selector')}<input name="submit_selector" value="${esc(p ? p.submit_selector : '#enter')}" data-req></label></div>
      <small class="muted" data-form>${T('Selektory jsou CSS selektory polí přihlašovacího formuláře, např. #name, input[name="password"].', 'Selectors are CSS selectors of the log-in form fields, e.g. #name, input[name="password"].')}</small></div>`,
    onOpen: form => {
      const sync = () => {
        const http = form.auth_type.value === 'http';
        $$('[data-form]', form).forEach(x => { x.hidden = http; $$('[data-req]', x).forEach(i => { i.required = !http; }); });
        $$('[data-form] [data-req], label[data-form] input', form).forEach(i => { i.required = !http; });
        $$('[data-http]', form).forEach(x => { x.hidden = !http; });
      };
      form.auth_type.onchange = sync;
      sync();
    },
    onSubmit: async f => {
      if (p) {
        await api('/api/profiles/' + p.id, { method: 'PUT', json: Object.fromEntries(f.entries()) });
      } else {
        await api('/api/profiles', { form: f });
      }
      toast(T('Profil uložen', 'Profile saved'));
      render(true);
    },
  });
}
Object.assign(ACT, {
  addProfile() { profileDialog(); },
  editProfile(b) { profileDialog(S.profiles.find(p => String(p.id) === b.dataset.id)); },
  async deleteProfile(b) {
    const p = S.profiles.find(x => String(x.id) === b.dataset.id);
    if (!p || !await confirmBox(T(`Smazat profil <b>${esc(p.name)}</b>? Weby, které ho používají, zůstanou bez automatického přihlášení.`, `Delete the profile <b>${esc(p.name)}</b>? Websites using it stay without automatic log-in.`), { submit: T('Smazat', 'Delete') })) return;
    await api('/api/profiles/' + p.id, { method: 'DELETE' });
    toast(T('Profil smazán', 'Profile deleted'));
    render(true);
  },
});

// ------------------------------------------------------------------ notifications

const LEVELS = [['info', 'Info', 'Info', 'info', 'info'], ['success', 'OK', 'OK', 'ok', 'check'], ['warning', 'Varování', 'Warning', 'warn', 'warn'], ['critical', 'Kritické', 'Critical', 'bad', 'warn']];
const level = l => LEVELS.find(x => x[0] === l) || LEVELS[0];
const POSITIONS = [['top-right', 'Vpravo nahoře', 'Top right'], ['top', 'Nahoře uprostřed', 'Top centre'], ['top-left', 'Vlevo nahoře', 'Top left'], ['bottom-right', 'Vpravo dole', 'Bottom right'], ['bottom', 'Dole uprostřed', 'Bottom centre'], ['bottom-left', 'Vlevo dole', 'Bottom left'], ['center', 'Uprostřed', 'Centre']];
const SOUNDS = [['off', 'Jen když si o zvuk řekne oznámení', 'Only when a notification asks'], ['critical', 'Jen kritická', 'Critical only'], ['warning', 'Varování a kritická', 'Warning and critical'], ['all', 'Všechna oznámení', 'All notifications']];
const STATES = { 1: ['Zobrazeno', 'Shown'], 2: ['Zahozeno / vypršelo', 'Dropped / expired'], 3: ['Odebráno', 'Removed'] };
const endpoint = () => location.origin + '/api/notify/v1';

function noteRow(n, extra = '', actions = '') {
  const [, cs, en, cls, ic] = level(n.level);
  return `<li class="row"><span class="row-icon ${cls}">${icon(ic)}</span><div class="row-main"><b>${esc(n.title || n.message)}</b>${n.title && n.message ? `<span class="msg">${esc(n.message)}</span>` : ''}
    <div class="row-tags"><span class="tag ${cls === 'warn' ? 'warn' : cls === 'bad' ? 'bad' : cls === 'ok' ? 'ok' : 'info'}">${T(cs, en)}</span>${n.source ? `<span class="tag">${esc(n.source)}</span>` : ''}<span class="tag">${n.duration} s</span><span class="tag">${when(n.created)}</span>${extra}</div></div>
    <div class="row-actions">${actions}</div></li>`;
}
function examples(token) {
  const t = token || '<TOKEN>', u = endpoint();
  return codeBlock(`curl -X POST ${u} \\\n  -H "Authorization: Bearer ${t}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"title":"${T('Záloha dokončena', 'Backup finished')}","message":"Server DB01, 42 GB","level":"success"}'`)
    + codeBlock(`curl -H "Authorization: Bearer ${t}" -H "Title: Deploy" -H "X-Level: warning" \\\n  -d "${T('Nasazení verze 2.4 začíná', 'Deploying version 2.4')}" ${u}`)
    + codeBlock(`Invoke-RestMethod -Method Post -Uri "${u}" -Headers @{Authorization="Bearer ${t}"} \`\n  -ContentType "application/json; charset=utf-8" \`\n  -Body (@{title="${T('Tisk', 'Printer')}"; message="${T('Došel toner', 'Out of toner')}"; level="warning"} | ConvertTo-Json)`);
}
async function notifyCounts() {
  const [q, tokens, watchers] = await Promise.all([api('/api/notify/queue'), api('/api/notify/tokens').catch(() => []), api('/api/notify/watchers').catch(() => [])]);
  S.queueWaiting = q.waiting.length + (q.current ? 1 : 0);
  updateNav();
  return { q, tokens, watchers };
}

VIEWS.notify = async (root, changed) => {
  setTitle(T('Oznámení', 'Notifications'));
  const { q, tokens, watchers } = await notifyCounts();
  const counts = { queue: S.queueWaiting, apps: tokens.length, watchers: watchers.length };
  const tabs = `<nav class="tabs">${NOTIFY_TABS.map(([k, cs, en]) => `<a href="#/notify/${k}" class="${k === S.tab ? 'active' : ''}">${T(cs, en)}${counts[k] ? `<span class="count">${counts[k]}</span>` : ''}</a>`).join('')}</nav>`;
  if (S.tab === 'queue') return queueTab(root, tabs, q, changed);
  root.innerHTML = tabs + '<div id="ntfBody" class="grid"></div>';
  const body = $('#ntfBody');
  if (S.tab === 'look') return lookTab(body);
  if (S.tab === 'settings') return settingsTab(body);
  if (S.tab === 'apps') return appsTab(body, tokens);
  if (S.tab === 'watchers') return watchersTab(body, watchers);
  if (S.tab === 'audit') return auditTab(body);
};

function queueHtml(q) {
  const left = q.current ? Math.max(0, Math.ceil(q.current.shown_at + q.current.duration - q.now)) : 0;
  return `<section class="card flush"><div class="toolbar"><h2 class="grow">${T('Na obrazovce a ve frontě', 'On screen and waiting')} <span class="badge">${q.waiting.length} ${T('čeká', 'waiting')}</span></h2>
      <button class="btn" data-do="ntfTest">${icon('bell')}<span>${T('Poslat test', 'Send test')}</span></button>
      <button class="btn danger" data-do="ntfClear" ${q.current || q.waiting.length ? '' : 'disabled'}>${icon('x')}<span>${T('Vyprázdnit', 'Clear')}</span></button></div>
    <ul class="list">${q.current ? noteRow(q.current, `<span class="tag accent">${T('Na obrazovce', 'On screen')} · ${left} s</span>`, `<button class="btn sm" data-do="ntfSkip">${icon('next')}${T('Přeskočit', 'Skip')}</button>`) : ''}
      ${q.waiting.map(n => noteRow(n, '', `<button class="icon-btn danger" data-do="ntfRemove" data-id="${n.id}" title="${T('Odebrat', 'Remove')}">${icon('trash')}</button>`)).join('')}
      ${q.current || q.waiting.length ? '' : `<li>${emptyState('bell', T('Nic nečeká', 'Nothing is waiting'), T('Oznámení se zobrazují po jednom, důležitější mají přednost. Vyzkoušejte to tlačítkem Poslat test.', 'Notifications are shown one at a time, more important ones first. Try it with Send test.'))}</li>`}</ul></section>
    <section class="card flush"><div class="toolbar"><h2 class="grow">${T('Poslední oznámení', 'Recent notifications')} <span class="badge">${q.history_count} ${T('v historii', 'in history')}</span></h2>
      <button class="btn danger" data-do="ntfClearHistory" ${q.history_count ? '' : 'disabled'}>${icon('trash')}<span>${T('Smazat historii', 'Clear history')}</span></button></div>
    <ul class="list">${q.history.map(n => noteRow(n, `<span class="tag">${T(...(STATES[n.done] || STATES[1]))}</span>`)).join('') || `<li class="row no-icon"><span class="muted">${T('Zatím žádná.', 'None yet.')}</span></li>`}</ul></section>`;
}
function queueTab(root, tabs, q, changed) {
  if (changed || !$('#ntfQueue')) root.innerHTML = tabs + `<div id="ntfQueue" class="grid"></div>`;
  else root.querySelector('.tabs').outerHTML = tabs;
  $('#ntfQueue').innerHTML = queueHtml(q);
}
setInterval(async () => {
  if (document.hidden || $('#app').hidden || S.view !== 'notify' || S.tab !== 'queue' || dlg().open) return;
  try { const { q } = await notifyCounts(); const el = $('#ntfQueue'); if (el) el.innerHTML = queueHtml(q); } catch (e) { /* next time */ }
}, 2000);

Object.assign(ACT, {
  ntfTest() {
    modal({
      title: T('Testovací oznámení', 'Test notification'),
      body: `<div class="form"><label>${T('Nadpis', 'Title')}<input name="title" value="${esc(T('Test oznámení', 'Test notification'))}"></label>
        <label>${T('Text', 'Message')}<input name="message" value="${esc(T('Takhle vypadá oznámení na obrazovce.', 'This is how a notification looks on the screen.'))}"></label>
        <div class="row2"><label>${T('Úroveň', 'Level')}<select name="level">${opts(LEVELS.map(l => l.slice(0, 3)), 'info')}</select></label>
        <label>${T('Doba zobrazení (s)', 'Display time (s)')}<input name="duration" type="number" min="3" max="120" placeholder="${T('výchozí', 'default')}"></label></div>
        <div class="row3"><label>${T('Zvuk', 'Sound')}<select name="sound">${opts([['', 'Podle nastavení', 'By the settings'], ['1', 'Zahrát', 'Play'], ['0', 'Bez zvuku', 'Silent']], '')}</select></label>
        <label>${T('Obrázek', 'Picture')}<select name="image">${opts([['', 'Podle vzhledu', 'As in the look'], ['none', 'Bez obrázku', 'No picture'], ['left', 'Vlevo', 'Left'], ['right', 'Vpravo', 'Right'], ['top', 'Nahoře', 'Top'], ['bottom', 'Dole', 'Bottom'], ['background', 'Jako pozadí', 'As background']], '')}</select></label>
        <label>${T('Velikost obrázku (%)', 'Picture size (%)')}<input name="image_size" type="number" min="10" max="60" step="5" placeholder="${T('podle vzhledu', 'as in the look')}"></label></div></div>`,
      submit: T('Poslat', 'Send'),
      onSubmit: async f => { await api('/api/notify/test', { json: Object.fromEntries(f.entries()) }); toast(T('Oznámení odesláno', 'Notification sent')); if (S.tab === 'queue') render(); },
    });
  },
  async ntfSkip() { await api('/api/notify/skip', { method: 'POST' }); render(); },
  async ntfRemove(b) { await api('/api/notify/queue/' + b.dataset.id, { method: 'DELETE' }); render(); },
  async ntfClear() {
    if (!await confirmBox(T('Odebrat všechna čekající oznámení i to, které je právě na obrazovce?', 'Remove all waiting notifications and the one on the screen?'), { submit: T('Vyprázdnit', 'Clear') })) return;
    const r = await api('/api/notify/clear', { method: 'POST' });
    toast(T('Odebráno: ', 'Removed: ') + r.cleared);
    render();
  },
  async ntfClearHistory() {
    if (!await confirmBox(T('Smazat celou historii oznámení? Čekající oznámení zůstanou.', 'Delete the whole notification history? Waiting notifications stay.'), { submit: T('Smazat', 'Delete') })) return;
    const r = await api('/api/notify/history/clear', { method: 'POST' });
    toast(T('Smazáno záznamů: ', 'Entries deleted: ') + r.deleted);
    render();
  },
});

async function lookTab(body) {
  const [s, picture] = await Promise.all([api('/api/notify/settings'), api('/api/notify/image').catch(() => null)]);
  S.picture = picture && picture.sha256 ? picture : null;
  body.innerHTML = `<section class="card"><div class="card-head"><h2>${icon('image')}${T('Vzhled oznámení', 'Notification look')}</h2>
      <div class="row-actions"><button class="btn" data-do="ntfTest">${icon('bell')}${T('Poslat test', 'Send test')}</button><button class="btn" data-do="lookDefault">${T('Výchozí', 'Default')}</button><button class="btn primary" data-do="lookSave">${T('Uložit vzhled', 'Save look')}</button></div></div>
    <p class="card-intro">${T('Barvy, tvar, průhlednost, obrázek, písmo a animace oznámení na televizi. Náhled odpovídá obrazovce 1920 × 1080 s pozicí a velikostí z nastavení; tlačítkem Skutečná obrazovka ho uvidíte nad tím, co televize právě ukazuje.', 'Colours, shape, transparency, picture, font and animation of the notifications on the TV. The preview matches a 1920 × 1080 screen with the position and size from the settings; Real screen shows it over what the TV shows right now.')}</p>
    <div id="lookEditor"></div></section>`;
  if (!window.NotifyStyle) return;
  const image = S.picture ? '/api/notify/image/file?v=' + S.picture.sha256.slice(0, 12) : '';
  S.editor = NotifyStyle.editor($('#lookEditor'), s.style, {
    t: T, position: s.position, scale: s.scale, image,
    onImage: async file => {
      if (file.size > 5 * 1048576) { toast(T('Obrázek je větší než 5 MB.', 'The picture is larger than 5 MB.'), 'err'); throw new Error('size'); }
      const fd = new FormData(); fd.append('file', file);
      try { S.picture = await api('/api/notify/image', { form: fd }); } catch (e) { fail(e); throw e; }
      toast(T('Obrázek nahrán – uložte vzhled', 'Picture uploaded – save the look'));
      return '/api/notify/image/file?v=' + S.picture.sha256.slice(0, 12);
    },
    onImageRemove: async () => {
      if (!await confirmBox(T('Odebrat obrázek oznámení?', 'Remove the notification picture?'), { submit: T('Odebrat', 'Remove') })) throw new Error('cancel');
      await api('/api/notify/image', { method: 'DELETE' });
      S.picture = null;
    },
    capture: () => window.caracalScreenshot(),
  });
}
Object.assign(ACT, {
  async lookSave() {
    if (!S.editor) return;
    await api('/api/notify/settings', { method: 'PUT', json: { style: S.editor.get() } });
    toast(T('Vzhled uložen – projeví se s dalším oznámením', 'Look saved – the screen uses it with the next notification'));
  },
  lookDefault() { if (S.editor) S.editor.set(NotifyStyle.DEFAULT); },
});

async function settingsTab(body) {
  const [s, sounds] = await Promise.all([api('/api/notify/settings'), api('/api/notify/sounds').catch(() => ({}))]);
  const num = (name, min, max, step = 1) => `<input name="${name}" type="number" min="${min}" max="${max}" step="${step}" value="${esc(s[name])}" required>`;
  body.innerHTML = `<section class="card"><div class="card-head"><h2>${icon('bell')}${T('Nastavení', 'Settings')}</h2></div>
    <form class="form" id="ntfSettings">
      <label class="check"><input type="checkbox" class="switch" name="enabled" ${s.enabled ? 'checked' : ''}>${T('Zobrazovat oznámení na obrazovce', 'Show notifications on the screen')}</label>
      <div class="row3"><label>${T('Pozice', 'Position')}<select name="position">${opts(POSITIONS, s.position)}</select></label>
        <label>${T('Doba zobrazení (s)', 'Display time (s)')}${num('duration', 3, 120)}</label><label>${T('Velikost (%)', 'Size (%)')}${num('scale', 50, 300, 10)}</label></div>
      <div class="row3"><label>${T('Zvuk', 'Sound')}<select name="sound">${opts(SOUNDS, s.sound)}</select></label>
        <label>${T('Hlasitost (%)', 'Volume (%)')}${num('volume', 0, 100, 10)}</label><label>${T('Nejvíc ve frontě', 'At most waiting')}${num('max_queue', 1, 200)}</label></div>
      <div class="row3"><label>${T('Historie: záznamů', 'History: entries')}${num('history_max', 50, 5000, 50)}</label><label>${T('Historie: dní', 'History: days')}${num('history_days', 1, 90)}</label>
        <label>${T('Zvukový výstup (ALSA)', 'Sound output (ALSA)')}<input name="sound_device" value="${esc(s.sound_device || '')}" placeholder="${T('prázdné = výchozí', 'empty = default')}"></label></div>
      <p class="hint">${T('Kritická oznámení zůstávají nejméně 15 s. Zvuk hraje zařízení přes HDMI nebo zvukový výstup, pro každou úroveň jinou znělkou; hlasitost 0 % obrazovku ztlumí. Plná fronta zahodí nejstarší méně důležité oznámení, oznámení se stejným klíčem (key) se nahrazují.', 'Critical notifications stay at least 15 s. The device plays the sound through HDMI or its audio output, with a different chime per level; volume 0 % mutes the screen. A full queue drops the oldest less important notification; notifications with the same key replace each other.')}</p>
      <div class="form-actions"><button class="btn primary">${T('Uložit nastavení', 'Save settings')}</button></div></form></section>
    <section class="card flush"><div class="toolbar"><h2 class="grow">${icon('sound')}${T('Vlastní zvuky', 'Custom sounds')}</h2></div>
      <p class="card-intro" style="padding-top:12px">${T('Pro každou úroveň jde nahrát vlastní MP3 (nejvýš 5 MB); obrazovka přehraje nejvýš 15 s s hlasitostí z nastavení. Bez souboru hraje vygenerovaná znělka.', 'For each level you can upload your own MP3 (at most 5 MB); the screen plays at most 15 s with the volume of the settings. Without a file the generated chime plays.')}</p>
      <ul class="list">${LEVELS.map(([lv, cs, en, cls, ic]) => { const snd = sounds[lv]; return `<li class="row"><span class="row-icon ${cls}">${icon(ic)}</span><div class="row-main"><b>${T(cs, en)}</b>
        <small>${snd ? `${esc(snd.name)} · ${fmtSize(snd.size)}` : T('Výchozí znělka', 'Default chime')}</small>${snd ? `<audio controls preload="none" src="/api/notify/sounds/${lv}/file?v=${esc(snd.sha256.slice(0, 12))}"></audio>` : ''}</div>
        <div class="row-actions"><input type="file" id="snd-${lv}" accept=".mp3,audio/mpeg" hidden data-sound="${lv}"><button class="btn sm" data-do="soundPick" data-lv="${lv}">${icon('upload')}${T('Nahrát MP3', 'Upload MP3')}</button>${snd ? `<button class="btn sm danger" data-do="soundReset" data-lv="${lv}">${T('Výchozí', 'Default')}</button>` : ''}</div></li>`; }).join('')}</ul></section>`;
  $('#ntfSettings').onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target), btn = $('button.primary', e.target);
    btn.classList.add('busy');
    try {
      const body = { enabled: !!f.get('enabled'), position: f.get('position'), sound: f.get('sound'), sound_device: f.get('sound_device').trim() };
      for (const k of ['duration', 'scale', 'volume', 'max_queue', 'history_max', 'history_days']) body[k] = Number(f.get(k));
      await api('/api/notify/settings', { method: 'PUT', json: body });
      toast(T('Nastavení oznámení uloženo', 'Notification settings saved'));
    } catch (err) { fail(err); }
    finally { btn.classList.remove('busy'); }
  };
  $$('[data-sound]', body).forEach(inp => {
    inp.onchange = async () => {
      const file = inp.files[0];
      if (!file) return;
      if (file.size > 5 * 1048576) return toast(T('Soubor je větší než 5 MB.', 'The file is larger than 5 MB.'), 'err');
      const fd = new FormData(); fd.append('file', file);
      try { await api('/api/notify/sounds/' + inp.dataset.sound, { form: fd }); toast(T('Zvuk nahrán', 'Sound uploaded')); render(true); } catch (e) { fail(e); }
    };
  });
}
Object.assign(ACT, {
  soundPick(b) { $('#snd-' + b.dataset.lv).click(); },
  async soundReset(b) {
    if (!await confirmBox(T('Vrátit výchozí znělku?', 'Restore the default chime?'), { danger: false })) return;
    await api('/api/notify/sounds/' + b.dataset.lv, { method: 'DELETE' });
    toast(T('Výchozí znělka obnovena', 'Default chime restored'));
    render(true);
  },
});

function appsTab(body, tokens) {
  const fresh = S.newToken ? `<section class="card"><div class="card-head"><h2>${icon('key')}${T('Token pro', 'Token for')} ${esc(S.newToken.name)}</h2><button class="btn" data-do="tokenDone">${T('Hotovo', 'Done')}</button></div>
      <div class="note warn">${T('Token se zobrazí jen teď. Uložte ho do aplikace, která bude oznámení posílat; zařízení si pamatuje jen jeho otisk.', 'The token is shown only now. Store it in the app that will send notifications; the device keeps only its hash.')}</div>
      ${codeBlock(S.newToken.token)}${examples(S.newToken.token)}</section>` : '';
  body.innerHTML = fresh + `<section class="card flush"><div class="toolbar"><h2 class="grow">${T('Aplikace a tokeny', 'Apps and tokens')} <span class="badge">${tokens.length}</span></h2>
      <button class="btn primary" data-do="tokenAdd">${icon('plus')}<span>${T('Nový token', 'New token')}</span></button></div>
    <ul class="list">${tokens.map(t => `<li class="row"><span class="row-icon ${t.enabled ? 'accent' : ''}">${icon('key')}</span><div class="row-main"><b>${esc(t.name)}</b>
      <div class="row-tags"><span class="tag">${esc(t.prefix)}…</span><span class="tag">${t.rate_per_min} / min</span><span class="tag ${t.enabled ? 'ok' : ''}">${t.enabled ? T('Povoleno', 'Enabled') : T('Zakázáno', 'Disabled')}</span><span class="tag">${T('Naposledy', 'Last used')}: ${when(t.last_used)}</span></div></div>
      <div class="row-actions"><button class="btn sm" data-do="tokenToggle" data-id="${t.id}" data-on="${t.enabled ? 0 : 1}">${t.enabled ? T('Zakázat', 'Disable') : T('Povolit', 'Enable')}</button>
        <button class="icon-btn" data-do="tokenEdit" data-id="${t.id}" title="${T('Upravit', 'Edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="tokenDelete" data-id="${t.id}" title="${T('Smazat', 'Delete')}">${icon('trash')}</button></div></li>`).join('')
      || `<li>${emptyState('key', T('Zatím žádná aplikace', 'No apps yet'), T('Každá aplikace (Grafana, zálohy, Home Assistant…) dostane vlastní token, který jde kdykoli zakázat nebo smazat.', 'Every app (Grafana, backups, Home Assistant…) gets its own token that can be disabled or deleted at any time.'), `<button class="btn primary" data-do="tokenAdd">${icon('plus')}${T('Nový token', 'New token')}</button>`)}</li>`}</ul></section>
    <section class="card"><div class="card-head"><h2>${icon('info')}${T('Jak posílat oznámení', 'How to send notifications')}</h2></div>
      <p class="card-intro">${T('POST na adresu níže s tokenem v hlavičce Authorization: Bearer (nebo X-Caracal-Token, nebo jako heslo v Basic auth). Pole: title, message, level (info, success, warning, critical), duration, key. Webhooky z Grafany, Alertmanageru a Uptime Kuma fungují tak, jak jsou: stačí jim zadat tuto adresu a token.', 'POST to the address below with the token in the Authorization: Bearer header (or X-Caracal-Token, or as the Basic auth password). Fields: title, message, level (info, success, warning, critical), duration, key. Webhooks from Grafana, Alertmanager and Uptime Kuma work as they are: give them this address and the token.')}</p>
      ${codeBlock(endpoint())}${examples()}</section>`;
}
function tokenDialog(t = null) {
  modal({
    title: t ? T('Upravit token', 'Edit token') : T('Nový token', 'New token'),
    body: `<div class="form">${t ? '' : `<p class="muted">${T('Jeden token pro jednu aplikaci. Název se zobrazí u jejích oznámení.', 'One token per app. The name is shown on its notifications.')}</p>`}
      <label>${T('Název aplikace', 'App name')}<input name="name" required value="${esc(t ? t.name : '')}" placeholder="Grafana"></label>
      <label>${T('Nejvíc oznámení za minutu', 'Notifications per minute at most')}<input name="rate_per_min" type="number" min="1" max="600" value="${t ? t.rate_per_min : 30}" required></label></div>`,
    submit: t ? T('Uložit', 'Save') : T('Vytvořit', 'Create'),
    onSubmit: async f => {
      const json = { name: f.get('name').trim(), rate_per_min: Number(f.get('rate_per_min')) };
      if (t) await api('/api/notify/tokens/' + t.id, { method: 'PUT', json });
      else S.newToken = { name: json.name, token: (await api('/api/notify/tokens', { json })).token };
      render(true);
    },
  });
}
Object.assign(ACT, {
  tokenAdd() { tokenDialog(); },
  async tokenEdit(b) { tokenDialog((await api('/api/notify/tokens')).find(x => String(x.id) === b.dataset.id)); },
  tokenDone() { S.newToken = null; render(true); },
  async tokenToggle(b) { await api('/api/notify/tokens/' + b.dataset.id, { method: 'PUT', json: { enabled: b.dataset.on === '1' } }); render(true); },
  async tokenDelete(b) {
    if (!await confirmBox(T('Smazat token? Aplikace, která ho používá, už nebude moct posílat oznámení.', 'Delete the token? The app using it will no longer be able to send notifications.'), { submit: T('Smazat', 'Delete') })) return;
    await api('/api/notify/tokens/' + b.dataset.id, { method: 'DELETE' });
    render(true);
  },
});

// watchers: CARACAL asks another app's API for new items (tickets, issues, e-mails) and announces each new one
const W_AUTH = [['none', 'Bez přihlášení', 'None'], ['bearer', 'Bearer token', 'Bearer token'], ['basic', 'Jméno a heslo / API klíč (Basic)', 'User and password / API key (Basic)'], ['header', 'Vlastní hlavička', 'Custom header'], ['oauth2', 'OAuth2', 'OAuth2']];
const W_GRANTS = [['client_credentials', 'Client credentials (server–server)', 'Client credentials (server to server)'], ['password', 'Password (technický uživatel)', 'Password (service user)'], ['refresh_token', 'Refresh token', 'Refresh token']];
const W_CLIENT = [['body', 'V těle požadavku (client_secret_post)', 'In the request body (client_secret_post)'], ['basic', 'Basic hlavička (client_secret_basic)', 'Basic header (client_secret_basic)']];
const W_PRESETS = {
  custom: { label: ['Vlastní API (JSON)', 'Custom API (JSON)'] },
  zammad: { label: ['Zammad – nové tickety', 'Zammad – new tickets'], url: 'https://zammad.example.com/api/v1/tickets/search?query=state.name:new&sort_by=created_at&order_by=desc&limit=50&expand=true', auth_type: 'header', auth_header: 'Authorization', secret_hint: 'Token token=…', list_path: '', id_field: 'id', title_template: 'Nový ticket #{number}: {title}', message_template: '{customer} · {group}', level_field: '' },
  jira: { label: ['Jira – nové issues', 'Jira – new issues'], url: 'https://example.atlassian.net/rest/api/3/search/jql?jql=project%3DSD%20AND%20statusCategory%3D%22To%20Do%22%20ORDER%20BY%20created%20DESC&fields=summary,priority,reporter&maxResults=50', auth_type: 'basic', username_hint: 'e-mail', secret_hint: 'API token', list_path: 'issues', id_field: 'key', title_template: '{key}: {fields.summary}', message_template: '{fields.reporter.displayName}', level_field: 'fields.priority.name' },
  redmine: { label: ['Redmine – nové úkoly', 'Redmine – new issues'], url: 'https://redmine.example.com/issues.json?status_id=open&sort=created_on:desc&limit=50', auth_type: 'header', auth_header: 'X-Redmine-API-Key', secret_hint: 'API key', list_path: 'issues', id_field: 'id', title_template: '#{id}: {subject}', message_template: '{project.name} · {author.name}', level_field: 'priority.name' },
  freshdesk: { label: ['Freshdesk – nové tickety', 'Freshdesk – new tickets'], url: 'https://example.freshdesk.com/api/v2/tickets?order_by=created_at&order_type=desc&per_page=50', auth_type: 'basic', username_hint: 'API key', secret_hint: 'X', list_path: '', id_field: 'id', title_template: 'Nový ticket #{id}: {subject}', message_template: '', level_field: '' },
  gitlab: { label: ['GitLab – nové issues', 'GitLab – new issues'], url: 'https://gitlab.example.com/api/v4/projects/123/issues?state=opened&order_by=created_at&per_page=50', auth_type: 'header', auth_header: 'PRIVATE-TOKEN', secret_hint: 'Personal access token', list_path: '', id_field: 'id', title_template: '#{iid}: {title}', message_template: '{author.name}', level_field: '' },
  github: { label: ['GitHub – nové issues', 'GitHub – new issues'], url: 'https://api.github.com/repos/OWNER/REPO/issues?state=open&sort=created&per_page=50', auth_type: 'bearer', secret_hint: 'Personal access token', list_path: '', id_field: 'id', title_template: '#{number}: {title}', message_template: '{user.login}', level_field: '' },
  graph: { label: ['Microsoft 365 – nové e-maily ve schránce (OAuth2)', 'Microsoft 365 – new e-mails in a mailbox (OAuth2)'], url: 'https://graph.microsoft.com/v1.0/users/helpdesk@example.com/mailFolders/inbox/messages?$top=25&$orderby=receivedDateTime%20desc&$select=subject,from,receivedDateTime,importance', auth_type: 'oauth2', oauth_token_url: 'https://login.microsoftonline.com/<TENANT-ID>/oauth2/v2.0/token', oauth_grant: 'client_credentials', oauth_scope: 'https://graph.microsoft.com/.default', oauth_client_auth: 'body', oauth_extra: '', list_path: 'value', id_field: 'id', title_template: '{subject}', message_template: '{from.emailAddress.name}', level_field: '' },
  servicenow: { label: ['ServiceNow – nové incidenty (OAuth2)', 'ServiceNow – new incidents (OAuth2)'], url: 'https://example.service-now.com/api/now/table/incident?sysparm_query=active%3Dtrue%5EORDERBYDESCsys_created_on&sysparm_limit=50&sysparm_fields=sys_id,number,short_description,priority,category', auth_type: 'oauth2', oauth_token_url: 'https://example.service-now.com/oauth_token.do', oauth_grant: 'password', oauth_scope: '', oauth_client_auth: 'body', oauth_extra: '', username_hint: 'service user', list_path: 'result', id_field: 'sys_id', title_template: '{number}: {short_description}', message_template: '{category}', level_field: '' },
  oauth: { label: ['Vlastní API s OAuth2', 'Custom API with OAuth2'], url: '', auth_type: 'oauth2', oauth_token_url: 'https://login.example.com/realms/example/protocol/openid-connect/token', oauth_grant: 'client_credentials', oauth_scope: '', oauth_client_auth: 'body', oauth_extra: '', list_path: '', id_field: 'id', title_template: '', message_template: '', level_field: '' },
};
const W_FIELDS = ['url', 'auth_type', 'auth_header', 'list_path', 'id_field', 'title_template', 'message_template', 'level_field', 'oauth_token_url', 'oauth_grant', 'oauth_scope', 'oauth_client_auth', 'oauth_extra'];
function watcherPayload(form, id) {
  const out = Object.fromEntries(new FormData(form).entries());
  delete out.preset;
  out.interval = Number(out.interval || 60);
  out.verify_tls = form.verify_tls.checked;
  if (id) out.id = id;
  return out;
}
function watchersTab(body, list) {
  body.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${T('Hlídače', 'Watchers')} <span class="badge">${list.length}</span></h2>
      <button class="btn primary" data-do="watcherAdd">${icon('plus')}<span>${T('Nový hlídač', 'New watcher')}</span></button></div>
    <p class="card-intro" style="padding-top:12px">${T('CARACAL se sám pravidelně ptá API jiné aplikace (helpdesk, Jira, GitLab, schránka…) a každou novou položku ukáže jako oznámení.', 'CARACAL regularly asks another app\'s API (helpdesk, Jira, GitLab, a mailbox…) on its own and shows every new item as a notification.')}</p>
    <ul class="list">${list.map(w => `<li class="row"><span class="row-icon ${w.last_error ? 'bad' : w.enabled ? 'accent' : ''}">${icon('eye')}</span><div class="row-main"><b>${esc(w.name)}</b><small>${esc(hostOf(w.url))}</small>
      <div class="row-tags"><span class="tag ${w.enabled ? 'ok' : ''}">${w.enabled ? T('Hlídá', 'Watching') : T('Vypnuto', 'Off')}</span><span class="tag">${T('každých', 'every')} ${w.interval} s</span><span class="tag">${T('Kontrola', 'Checked')}: ${when(w.last_check)}</span>
        ${w.last_count != null && !w.last_error ? `<span class="tag">${w.last_count} ${T('položek', 'items')}</span>` : ''}${w.last_error ? `<span class="tag bad" title="${esc(w.last_error)}">${T('Chyba', 'Error')}: ${esc(w.last_error.slice(0, 80))}</span>` : ''}</div></div>
      <div class="row-actions"><button class="btn sm" data-do="watcherCheck" data-id="${w.id}">${T('Zkontrolovat', 'Check now')}</button><button class="btn sm" data-do="watcherToggle" data-id="${w.id}" data-on="${w.enabled ? 0 : 1}">${w.enabled ? T('Vypnout', 'Turn off') : T('Zapnout', 'Turn on')}</button>
        <button class="icon-btn" data-do="watcherEdit" data-id="${w.id}" title="${T('Upravit', 'Edit')}">${icon('edit')}</button><button class="icon-btn danger" data-do="watcherDelete" data-id="${w.id}" title="${T('Smazat', 'Delete')}">${icon('trash')}</button></div></li>`).join('')
      || `<li>${emptyState('eye', T('Zatím žádný hlídač', 'No watcher yet'), T('Přidejte hlídač a vyberte předvolbu: Zammad, Jira, Redmine, Freshdesk, GitLab, GitHub, Microsoft 365, ServiceNow nebo vlastní API.', 'Add a watcher and pick a preset: Zammad, Jira, Redmine, Freshdesk, GitLab, GitHub, Microsoft 365, ServiceNow or a custom API.'), `<button class="btn primary" data-do="watcherAdd">${icon('plus')}${T('Nový hlídač', 'New watcher')}</button>`)}</li>`}</ul></section>`;
}
function watcherDialog(w = null) {
  const v = { auth_type: 'none', id_field: 'id', level: 'info', interval: 60, oauth_grant: 'client_credentials', oauth_client_auth: 'body', ...(w || {}) };
  const keep = w && w.has_credentials ? T('Prázdné = beze změny', 'Empty = unchanged') : '';
  const field = (name, label, attrs = '', cls = '') => `<label ${cls}>${label}<input name="${name}" value="${esc(v[name] || '')}" ${attrs}></label>`;
  modal({
    title: w ? T('Upravit hlídač', 'Edit watcher') : T('Nový hlídač', 'New watcher'), wide: true,
    body: `<div class="form">${w ? '' : `<label>${T('Předvolba', 'Preset')}<select name="preset">${Object.entries(W_PRESETS).map(([k, p]) => `<option value="${k}">${esc(T(...p.label))}</option>`).join('')}</select></label>`}
      <div class="row2">${field('name', T('Název (zobrazí se u oznámení)', 'Name (shown on notifications)'), 'required placeholder="Helpdesk"')}
        <label>${T('Přihlášení', 'Authentication')}<select name="auth_type">${opts(W_AUTH, v.auth_type)}</select></label></div>
      ${field('url', T('URL API (vrací JSON)', 'API URL (returns JSON)'), 'type="url" required placeholder="https://…"')}
      <div class="row2">${field('auth_header', T('Název hlavičky', 'Header name'), 'placeholder="X-API-Key"', 'data-auth="header"')}
        ${field('oauth_token_url', 'Token URL', 'placeholder="https://login…/oauth2/token"', 'data-auth="oauth2"')}
        <label data-auth="oauth2">${T('Způsob získání tokenu', 'Grant type')}<select name="oauth_grant">${opts(W_GRANTS, v.oauth_grant)}</select></label>
        ${field('oauth_client_id', 'Client ID', 'autocomplete="off"', 'data-auth="oauth2"')}
        <label data-auth="oauth2">Client Secret<input name="client_secret" type="password" autocomplete="new-password" placeholder="${keep}"></label>
        ${field('oauth_scope', 'Scope', `placeholder="${T('nepovinné', 'optional')}"`, 'data-auth="oauth2"')}
        <label data-auth="basic oauth2" data-grant="password">${T('Uživatel / API klíč', 'User / API key')}<input name="username" autocomplete="off" placeholder="${keep}"></label>
        <label data-auth="bearer basic header oauth2" data-grant="password">${T('Heslo / token / hodnota hlavičky', 'Password / token / header value')}<input name="secret" type="password" autocomplete="new-password" placeholder="${keep}"></label>
        <label data-auth="oauth2" data-grant="refresh_token">Refresh token<input name="refresh_token" type="password" autocomplete="new-password" placeholder="${keep || T('vložte jednou, CARACAL si ho obnovuje sám', 'paste once, CARACAL renews it')}"></label>
        <label data-auth="oauth2">${T('Předání Client Secret', 'Client authentication')}<select name="oauth_client_auth">${opts(W_CLIENT, v.oauth_client_auth)}</select></label>
        ${field('oauth_extra', T('Další parametry tokenu', 'Extra token parameters'), 'placeholder="audience=https://api.example.com"', 'data-auth="oauth2"')}</div>
      <div class="row2">${field('list_path', T('Cesta k seznamu v odpovědi', 'Path to the list in the response'), `placeholder="${T('prázdné = odpověď je seznam; např. data.tickets', 'empty = the response is a list; e.g. data.tickets')}"`)}
        ${field('id_field', T('Pole s ID položky', 'Item ID field'))}</div>
      ${field('title_template', T('Nadpis oznámení', 'Notification title'), `placeholder="${T('Nový ticket #{id}: {subject}', 'New ticket #{id}: {subject}')}"`)}
      ${field('message_template', T('Text oznámení', 'Notification text'), 'placeholder="{customer.name} · {group}"')}
      <div class="row3"><label>${T('Úroveň', 'Level')}<select name="level">${opts(LEVELS.map(l => l.slice(0, 3)), v.level)}</select></label>
        ${field('level_field', T('Úroveň podle pole', 'Level from field'), 'placeholder="priority.name"')}
        <label>${T('Kontrolovat každých (s)', 'Check every (s)')}<input name="interval" type="number" min="15" max="86400" value="${v.interval}"></label></div>
      <label class="check"><input type="checkbox" class="switch" name="verify_tls" ${v.verify_tls === 0 ? '' : 'checked'}>${T('Ověřovat HTTPS certifikát', 'Verify the HTTPS certificate')}</label>
      <p class="hint">${T('V nadpisu a textu jde použít pole položky ve složených závorkách, i vnořená: {id}, {subject}, {fields.summary}. První kontrola si jen zapamatuje, co v systému už je; oznámí až položky, které přibudou potom.', 'Title and text can use item fields in braces, nested too: {id}, {subject}, {fields.summary}. The first check only remembers what is already there; only items that appear later are announced.')}</p>
      <div class="form-actions start"><button type="button" class="btn sm" data-do="watcherTry" data-id="${w ? w.id : ''}">${icon('check')}${T('Vyzkoušet', 'Try it')}</button></div><div class="result-box" id="watcherResult"></div></div>`,
    onOpen: form => {
      const sync = () => {
        const type = form.auth_type.value, grant = form.oauth_grant.value;
        $$('[data-auth]', form).forEach(x => { x.hidden = !x.dataset.auth.split(' ').includes(type) || (type === 'oauth2' && x.dataset.grant !== undefined && !x.dataset.grant.split(' ').includes(grant)); });
      };
      form.auth_type.onchange = form.oauth_grant.onchange = sync;
      if (form.preset) form.preset.onchange = () => {
        const p = W_PRESETS[form.preset.value];
        if (!p || form.preset.value === 'custom') return;
        for (const k of W_FIELDS) if (p[k] !== undefined && form[k]) form[k].value = p[k];
        if (!form.name.value) form.name.value = T(...p.label).split(' – ')[0];
        form.username.placeholder = p.username_hint || '';
        form.secret.placeholder = p.secret_hint || '';
        sync();
      };
      sync();
    },
    onSubmit: async (f, form) => {
      if (w) {
        const r = await api('/api/notify/watchers/' + w.id, { method: 'PUT', json: watcherPayload(form) });
        if (r.reset) setTimeout(() => toast(T('Jiná URL nebo seznam: hlídač začíná znovu a stávající položky neoznámí', 'Different URL or list: the watcher starts again and will not announce existing items')), 400);
      } else await api('/api/notify/watchers', { json: watcherPayload(form) });
      toast(T('Hlídač uložen', 'Watcher saved'));
      render(true);
    },
  });
}
Object.assign(ACT, {
  watcherAdd() { watcherDialog(); },
  async watcherEdit(b) { watcherDialog((await api('/api/notify/watchers')).find(x => String(x.id) === b.dataset.id)); },
  async watcherTry(b) {
    const form = b.closest('form'), box = $('#watcherResult');
    b.classList.add('busy');
    try {
      const r = await api('/api/notify/watchers/preview', { json: watcherPayload(form, b.dataset.id ? Number(b.dataset.id) : undefined) });
      box.innerHTML = `<div class="note ok"><div><b>${T('Nalezeno položek', 'Items found')}: ${r.count}</b>${r.samples.map(x => `<div class="sample"><b>${esc(x.title)}</b>${x.message ? `<br>${esc(x.message)}` : ''} <span class="muted">(${esc(x.level)})</span></div>`).join('') || `<br>${T('Seznam je teď prázdný – to je v pořádku, nové položky se ukážou, až přibudou.', 'The list is empty right now – that is fine, new items show up when they appear.')}`}</div></div>`;
    } catch (e) { box.innerHTML = `<div class="note bad">${esc(e.message)}</div>`; }
    finally { b.classList.remove('busy'); }
  },
  async watcherCheck(b) {
    b.classList.add('busy');
    try {
      const r = await api(`/api/notify/watchers/${b.dataset.id}/check`, { method: 'POST' });
      toast(!r.ok ? T('Chyba: ', 'Error: ') + r.error : r.first ? T('Zapamatováno položek: ', 'Items remembered: ') + r.count : T('Nové: ', 'New: ') + r.new + ' · ' + T('položek: ', 'items: ') + r.count, r.ok ? '' : 'err');
    } finally { b.classList.remove('busy'); }
    render(true);
  },
  async watcherToggle(b) { await api('/api/notify/watchers/' + b.dataset.id, { method: 'PUT', json: { enabled: b.dataset.on === '1' } }); render(true); },
  async watcherDelete(b) {
    if (!await confirmBox(T('Smazat hlídač?', 'Delete the watcher?'), { submit: T('Smazat', 'Delete') })) return;
    await api('/api/notify/watchers/' + b.dataset.id, { method: 'DELETE' });
    render(true);
  },
});

async function auditTab(body) {
  const a = await api('/api/notify/audit?limit=100');
  body.innerHTML = `<section class="card flush"><div class="toolbar"><h2 class="grow">${T('Audit log', 'Audit log')} <span class="badge">${a.count} / ${a.max} · ${a.days} ${T('dní', 'days')}</span></h2>
      <button class="btn danger" data-do="auditClear" ${a.count ? '' : 'disabled'}>${icon('trash')}<span>${T('Smazat audit', 'Clear audit log')}</span></button></div>
    <p class="card-intro" style="padding-top:12px">${T('Kdo co změnil v oznámeních, odmítnuté tokeny a odesílatelé nad svým limitem.', 'Who changed what in the notifications, refused tokens and senders over their limit.')}</p>
    <ul class="list">${a.entries.map(x => `<li class="row no-icon"><div class="row-main"><b>${esc(x.action)}</b>${x.detail ? `<small>${esc(x.detail)}</small>` : ''}
      <div class="row-tags"><span class="tag">${when(x.ts)}</span>${x.actor ? `<span class="tag accent">${esc(x.actor)}</span>` : ''}${x.ip ? `<span class="tag">${esc(x.ip)}</span>` : ''}</div></div><span></span></li>`).join('')
      || `<li class="row no-icon"><span class="muted">${T('Zatím žádné záznamy.', 'No entries yet.')}</span></li>`}
      ${a.count > a.entries.length ? `<li class="row no-icon"><span class="muted">${T('Zobrazeno posledních ', 'Showing the last ')}${a.entries.length}.</span></li>` : ''}</ul></section>`;
}
ACT.auditClear = async () => {
  if (!await confirmBox(T('Smazat audit log? Samotné smazání v něm zůstane zapsané.', 'Clear the audit log? The clearing itself stays in it.'), { submit: T('Smazat', 'Delete') })) return;
  const r = await api('/api/notify/audit/clear', { method: 'POST' });
  toast(T('Smazáno záznamů: ', 'Entries deleted: ') + r.deleted);
  render(true);
};

// ------------------------------------------------------------------ start

(async function boot() {
  applyTheme();
  applyStatic();
  try { await api('/api/me'); await showApp(); } catch (e) { await showLogin(); }
})();
