/* Townly Up — utilitários partilhados (pedidos à API, avisos e pontos). */
window.Townly = (function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const csrf = meta ? meta.content : '';

  async function api(url, options = {}) {
    const opts = { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, ...options };
    if (opts.body && typeof opts.body !== 'string' && !(opts.body instanceof FormData)) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.method && opts.method !== 'GET') opts.headers['X-CSRF-Token'] = csrf;
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* resposta sem JSON */ }
    if (!res.ok) {
      const err = new Error(data.error || 'Não foi possível concluir o pedido.');
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function toast(message) {
    const el = document.createElement('div');
    el.className = 'tu-toast';
    el.textContent = message;
    document.body.append(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 400); }, 2600);
  }

  function updatePoints(total) {
    const el = document.getElementById('my-points');
    if (!el) return;
    el.textContent = total;
    el.parentElement.classList.remove('tu-points-pop');
    void el.offsetWidth; // reinicia a animação
    el.parentElement.classList.add('tu-points-pop');
  }

  function timeAgo(utc) {
    const then = new Date(utc.replace(' ', 'T') + 'Z');
    const s = Math.max(0, (Date.now() - then) / 1000);
    if (s < 3600) return 'há ' + Math.max(1, Math.round(s / 60)) + ' min';
    if (s < 86400) return 'há ' + Math.round(s / 3600) + ' h';
    const d = Math.round(s / 86400);
    return d === 1 ? 'ontem' : 'há ' + d + ' dias';
  }

  function distance(lat1, lng1, lat2, lng2) {
    const r = 6371000, rad = Math.PI / 180;
    const a = Math.sin((lat2 - lat1) * rad / 2) ** 2 +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin((lng2 - lng1) * rad / 2) ** 2;
    return 2 * r * Math.asin(Math.sqrt(a));
  }

  /* GPS: ligações oficiais do Google Maps (https://developers.google.com/maps/documentation/urls).
   * No telemóvel abrem a app Google Maps já em modo de navegação. Não precisam de chave de API. */
  function gmapsDirections(lat, lng) {
    return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
  }
  function gmapsView(lat, lng) { return `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`; }

  function formatDistance(m) { return m < 1000 ? Math.round(m) + ' m' : (m / 1000).toFixed(1).replace('.', ',') + ' km'; }

  /* Marcador estilo Waze: gota com o ícone da categoria (+ nº de confirmações). */
  function pinIcon(item) {
    const count = item.seen > 0 ? `<span class="tu-pin-count">${item.seen + 1}</span>` : '';
    return L.divIcon({
      className: '', iconSize: [34, 34], iconAnchor: [17, 34], popupAnchor: [0, -30],
      html: `<div style="position:relative"><div class="tu-pin ${item.slug === 'resolvida' ? 'is-resolved' : ''}" style="--pin:${item.color}"><i class="bi bi-${item.icon}"></i></div>${count}</div>`
    });
  }

  return { api, toast, updatePoints, timeAgo, distance, formatDistance, pinIcon, csrf, gmapsDirections, gmapsView };
})();
