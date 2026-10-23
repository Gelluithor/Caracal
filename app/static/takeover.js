// The emergency message of CARACAL Fleet on the whole screen; the text is read again every few seconds.
const LABELS = { critical: 'DŮLEŽITÉ UPOZORNĚNÍ', warning: 'UPOZORNĚNÍ', info: 'INFORMACE' };
const $ = id => document.getElementById(id);
async function load() {
  try {
    const t = await (await fetch('/api/player/takeover', { cache: 'no-store' })).json();
    document.body.className = ['critical', 'warning', 'info'].includes(t.level) ? t.level : 'critical';
    $('label').textContent = LABELS[document.body.className];
    $('title').textContent = t.title || '';
    $('message').textContent = t.message || '';
  } catch { /* the next try */ }
}
function tick() {
  const now = new Date();
  $('clock').textContent = now.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' });
  $('date').textContent = now.toLocaleDateString('cs-CZ', { weekday: 'long', day: 'numeric', month: 'long' });
}
load();
tick();
setInterval(load, 5000);
setInterval(tick, 1000);
