// island-ui.js — 三个页面共用: 皮肤、顶栏、转义、请求、提示条。
(function () {
  const ISLAND_URL = 'https://our-island-frontend.vercel.app/';
  const qs = new URLSearchParams(location.search);
  try {
    if (qs.get('skin') === 'island') localStorage.setItem('ob-skin', 'island');
    if (qs.get('skin') === 'ob') localStorage.removeItem('ob-skin');
  } catch (_) {}
  let island = false;
  try { island = localStorage.getItem('ob-skin') === 'island'; } catch (_) {}
  if (island) document.documentElement.dataset.skin = 'island';

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = 'HTTP ' + r.status;
      try { const j = await r.json(); if (j && j.error) msg = j.error; } catch (_) {}
      throw new Error(msg);
    }
    return r.json();
  }

  function topbar(active) {
    const tabs = [['map', '/v2/map/', '足迹'], ['notes', '/v2/notes/', '知识本'], ['review', '/v2/review/', '回顾']];
    const back = island
      ? `<a class="pg-back" href="${ISLAND_URL}">← 回小岛</a>`
      : `<a class="pg-back" href="/v2/cells/">← 记忆库</a>`;
    return `<div class="pg-top">${back}<nav class="pg-tabs" aria-label="页面">${tabs.map(([k, h, t]) =>
      `<a href="${h}"${k === active ? ' aria-current="page"' : ''}>${t}</a>`).join('')}</nav></div>`;
  }

  let toastTimer;
  function toast(text, action) {
    let el = document.getElementById('pg-toast');
    if (!el) { el = document.createElement('div'); el.id = 'pg-toast'; el.className = 'toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
    el.innerHTML = `<span>${esc(text)}</span>${action ? `<button type="button">${esc(action.label)}</button>` : ''}`;
    if (action) el.querySelector('button').onclick = () => { el.classList.remove('show'); action.run(); };
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), action ? 5000 : 2600);
  }

  function hue(text) { // 主题 → 稳定的柔和色
    let h = 0; for (const c of String(text)) h = (h * 31 + c.codePointAt(0)) % 360;
    return `hsl(${h} 32% 58%)`;
  }

  function fmtDay(iso) {
    if (!iso) return { d: '', m: '', y: '' };
    const [y, m, d] = iso.split('-');
    const months = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
    return { d: String(+d), m: months[+m - 1] || '', y };
  }

  window.PG = { island, esc, api, topbar, toast, hue, fmtDay, ISLAND_URL };
})();
