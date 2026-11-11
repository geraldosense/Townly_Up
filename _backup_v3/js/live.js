/* Townly Up — Mapa vivo (estilo Waze).
 * Lê as ocorrências da API, mostra-as no mapa e numa lista, permite filtrar
 * por categoria e por freguesia (limites CAOP), ordenar por proximidade e confirmar («Eu também vi»).
 */
(function () {
  const root = document.getElementById('live');
  const feed = document.getElementById('feed');
  const countEl = document.getElementById('live-count');
  const role = root.dataset.role;
  const state = { items: [], cat: '', scope: 'active', me: null, markers: new Map(), selected: null, geo: null, parish: null };
  let parishLayer = null;

  let map = null;
  let layer = null;
  if (typeof L !== 'undefined') {
    map = L.map('live-map', { zoomControl: false }).setView([+root.dataset.lat, +root.dataset.lng], 14);
    L.control.zoom({ position: 'topright' }).addTo(map);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  } else {
    document.getElementById('live-map').innerHTML =
      '<p class="p-3 small text-muted">Mapa indisponível (sem ligação à internet). A lista continua a funcionar.</p>';
  }

  // ---------------------------------------------------------------- dados
  async function load() {
    try {
      const data = await Townly.api('/api/v1/map');
      state.items = data.incidents;
      tagParishes();
      render();
    } catch (e) {
      countEl.textContent = 'Não foi possível carregar o mapa.';
    }
  }

  function inScope(i) {
    return (state.scope === 'active' ? i.slug !== 'resolvida' : i.slug === 'resolvida') &&
      (!state.cat || String(i.category_id) === state.cat);
  }

  function visible() {
    return state.items.filter(i => inScope(i) && (!state.parish || i.parishCode === state.parish));
  }

  // ---------------------------------------------------------------- freguesias (CAOP / DGT)
  function tagParishes() {
    if (!state.geo) return;
    state.items.forEach(i => {
      const f = TownlyParishes.find(state.geo, i.lat, i.lng);
      i.parishCode = f ? TownlyParishes.codeOf(f) : null;
      i.parishName = f ? TownlyParishes.nameOf(f) : null;
    });
  }

  function parishFeature(code) {
    return state.geo ? state.geo.features.find(f => TownlyParishes.codeOf(f) === String(code)) : null;
  }

  function setParish(code, fit = true) {
    const f = code ? parishFeature(code) : null;
    state.parish = f ? TownlyParishes.codeOf(f) : null;
    const box = document.getElementById('parish-filter');
    box.replaceChildren();
    box.hidden = !f;
    if (f) {
      const chip = el('button', 'tu-chip-filter is-parish');
      chip.type = 'button';
      chip.append(el('i', 'bi bi-geo-alt-fill'), document.createTextNode(' ' + TownlyParishes.nameOf(f) + ' '), el('i', 'bi bi-x-lg'));
      chip.title = 'Mostrar todas as freguesias';
      chip.addEventListener('click', () => setParish(null));
      box.append(chip);
      if (parishLayer) parishLayer.select(state.parish);
      if (map && fit) map.fitBounds(L.geoJSON(f).getBounds(), { padding: [30, 30] });
    } else if (parishLayer) {
      parishLayer.clearSelection();
    }
    const url = new URL(location.href);
    f ? url.searchParams.set('freguesia', state.parish) : url.searchParams.delete('freguesia');
    history.replaceState(null, '', url);
    render();
  }

  async function initParishes() {
    if (!window.TownlyParishes) return;
    state.geo = await TownlyParishes.load();
    if (!state.geo) return;
    tagParishes();
    if (map) {
      parishLayer = TownlyParishes.leafletLayer(map, state.geo, {
        line: f => {
          const code = TownlyParishes.codeOf(f);
          const n = state.items.filter(i => inScope(i) && i.parishCode === code).length;
          return state.scope === 'active' ? TownlyParishes.countLine(n)
            : `${n} resolvida${n === 1 ? '' : 's'} (30 dias)`;
        },
        onClick: f => setParish(TownlyParishes.codeOf(f), false),
      }).addTo(map);
      const toggle = document.getElementById('toggle-parishes');
      toggle.hidden = false;
      toggle.addEventListener('click', () => {
        const on = map.hasLayer(parishLayer);
        on ? map.removeLayer(parishLayer) : parishLayer.addTo(map);
        toggle.classList.toggle('active', !on);
        toggle.setAttribute('aria-pressed', String(!on));
      });
    }
    const wanted = new URLSearchParams(location.search).get('freguesia');
    if (wanted) setParish(wanted); else render();
  }

  function withDistance(items) {
    if (!state.me) return items;
    return items.map(i => ({ ...i, dist: Townly.distance(state.me[0], state.me[1], i.lat, i.lng) }))
      .sort((a, b) => a.dist - b.dist);
  }

  // ---------------------------------------------------------------- desenho
  function render() {
    const items = withDistance(visible());
    const n = items.length;
    const where = state.parish ? ' em ' + TownlyParishes.nameOf(parishFeature(state.parish)) : '';
    countEl.textContent = n === 0 ? 'Nenhuma ocorrência com estes filtros' + where + '.'
      : `${n} ocorrência${n > 1 ? 's' : ''} ${state.scope === 'active' ? 'ativa' + (n > 1 ? 's' : '') : 'resolvida' + (n > 1 ? 's' : '') + ' (30 dias)'}${where}${state.me ? ' · ordenadas por distância' : ''}`;

    feed.replaceChildren(...items.map(feedItem));
    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'tu-empty m-2';
      empty.innerHTML = '<i class="bi bi-emoji-smile"></i><p class="mt-2 mb-0">Tudo tranquilo por aqui.</p>';
      feed.append(empty);
    }

    if (!map) return;
    layer.clearLayers();
    state.markers.clear();
    items.forEach(i => {
      const m = L.marker([i.lat, i.lng], { icon: Townly.pinIcon(i), title: i.category })
        .bindPopup(() => popup(i), { maxWidth: 280 })
        .on('click', () => highlight(i.id))
        .addTo(layer);
      state.markers.set(i.id, m);
    });
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;  // textContent: nunca injeta HTML
    return e;
  }

  function iconBox(i) {
    const box = el('div', 'tu-feed-icon');
    box.style.setProperty('--c', i.color);
    box.append(el('i', 'bi bi-' + i.icon));
    return box;
  }

  function badge(i) { return el('span', 'tu-badge st-' + i.slug, i.status); }

  function feedItem(i) {
    const row = el('div', 'tu-feed-item');
    row.dataset.id = i.id;
    row.tabIndex = 0;
    const body = el('div', 'flex-grow-1 min-w-0');
    const top = el('div', 'd-flex justify-content-between gap-2');
    top.append(el('strong', 'text-truncate', i.category), badge(i));
    const meta = el('div', 'small text-muted');
    meta.textContent = `${i.ticket} · ${Townly.timeAgo(i.created_at)}` + (i.parishName ? ` · ${i.parishName}` : '') +
      (i.dist !== undefined ? ` · a ${Townly.formatDistance(i.dist)}` : '');
    body.append(top, meta);
    if (i.seen > 0) {
      const pill = el('div', 'tu-conf-pill mt-1');
      pill.append(el('i', 'bi bi-people-fill'), document.createTextNode(` ${i.seen + 1} munícipes viram`));
      body.append(pill);
    }
    row.append(iconBox(i), body);
    const open = () => {
      highlight(i.id);
      const m = state.markers.get(i.id);
      if (m) { map.setView(m.getLatLng(), Math.max(map.getZoom(), 17)); m.openPopup(); }
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', e => { if (e.key === 'Enter') open(); });
    return row;
  }

  function highlight(id) {
    feed.querySelectorAll('.tu-feed-item').forEach(r => r.classList.toggle('active', r.dataset.id == id));
    const row = feed.querySelector(`[data-id="${id}"]`);
    if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function popup(i) {
    const box = el('div', 'tu-popup');
    const head = el('div', 'd-flex gap-2 align-items-center');
    const title = el('div');
    title.append(el('strong', 'd-block', i.category),
      el('small', 'text-muted', `${i.ticket} · ${Townly.timeAgo(i.created_at)}` + (i.parishName ? ` · ${i.parishName}` : '')));
    head.append(iconBox(i), title);
    const status = el('div', 'mt-2');
    status.append(badge(i));
    box.append(head, status);

    const people = el('div', 'small text-muted mt-2');
    people.textContent = i.seen > 0 ? `${i.seen + 1} munícipes viram este problema` : 'Comunicado por 1 munícipe';
    if (i.fixed > 0) people.textContent += ` · ${i.fixed} dizem que já está resolvido`;
    box.append(people);

    // GPS: «Como chegar» abre a navegação do Google Maps até ao local.
    const nav = el('div', 'd-flex gap-2 mt-2');
    const go = el('a', 'btn btn-sm btn-primary flex-fill tu-gmaps-btn');
    go.href = Townly.gmapsDirections(i.lat, i.lng);
    go.target = '_blank'; go.rel = 'noopener';
    go.append(el('i', 'bi bi-sign-turn-right-fill'), document.createTextNode(' Como chegar'));
    const view = el('a', 'btn btn-sm btn-outline-primary');
    view.href = Townly.gmapsView(i.lat, i.lng);
    view.target = '_blank'; view.rel = 'noopener';
    view.title = 'Ver no Google Maps';
    view.append(el('i', 'bi bi-google'));
    nav.append(go, view);
    box.append(nav);

    if (i.slug === 'resolvida') return box;
    if (role === 'anon') {
      const a = el('a', 'btn btn-sm btn-outline-primary w-100 mt-2', 'Entrar para confirmar');
      a.href = root.dataset.login;
      box.append(a);
    } else if (role === 'citizen') {
      if (i.mine) {
        box.append(el('div', 'small fw-semibold text-success mt-2', 'Esta ocorrência é sua.'));
      } else {
        const row = el('div', 'tu-confirm-row');
        row.append(confirmButton(i, 'seen', 'bi-eye', 'Eu também vi', 'btn-success'),
                   confirmButton(i, 'fixed', 'bi-check2-circle', 'Já resolvido?', 'btn-outline-primary'));
        box.append(row);
      }
    }
    return box;
  }

  function confirmButton(i, kind, icon, label, cls) {
    const done = i.confirmed.includes(kind);
    const b = el('button', 'btn btn-sm ' + (done ? 'btn-light' : cls));
    b.type = 'button';
    b.disabled = done;
    b.append(el('i', 'bi ' + (done ? 'bi-check-lg' : icon)), document.createTextNode(' ' + (done ? 'Confirmado' : label)));
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const res = await Townly.api(`/api/v1/incidents/${i.id}/confirm`, { method: 'POST', body: { kind } });
        i.confirmed.push(kind);
        i.seen = res.counts.seen;
        i.fixed = res.counts.fixed;
        Townly.toast(`Obrigado! +${res.points} pontos`);
        Townly.updatePoints(res.total_points);
        render();
        const m = state.markers.get(i.id);
        if (m) m.openPopup();
      } catch (e) {
        Townly.toast(e.message);
        b.disabled = false;
      }
    });
    return b;
  }

  // ---------------------------------------------------------------- controlos
  document.getElementById('chips').addEventListener('click', e => {
    const chip = e.target.closest('.tu-chip-filter');
    if (!chip) return;
    document.querySelectorAll('.tu-chip-filter').forEach(c => c.classList.toggle('active', c === chip));
    state.cat = chip.dataset.cat;
    render();
  });

  document.querySelectorAll('input[name="scope"]').forEach(r => r.addEventListener('change', () => {
    state.scope = r.value;
    render();
  }));

  let meMarker = null;
  document.getElementById('locate').addEventListener('click', () => {
    if (!navigator.geolocation) { Townly.toast('O navegador não permite obter a localização.'); return; }
    navigator.geolocation.getCurrentPosition(pos => {
      state.me = [pos.coords.latitude, pos.coords.longitude];
      if (map) {
        if (meMarker) meMarker.setLatLng(state.me);
        else meMarker = L.marker(state.me, { icon: L.divIcon({ className: '', html: '<div class="tu-me-dot"></div>', iconSize: [18, 18] }) }).addTo(map);
        map.setView(state.me, 16);
      }
      render();
    }, () => Townly.toast('Não foi possível obter a sua localização.'), { enableHighAccuracy: true, timeout: 10000 });
  });

  load().then(initParishes);
  setInterval(load, 60000); // "ao vivo": atualiza a cada minuto
})();
