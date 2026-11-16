/* Townly Up — Mapa do concelho (país inteiro).
 * Abre no concelho da conta — qualquer um dos 308 — e, se a pessoa indicou a freguesia, já focado nela.
 * Contorno do concelho destacado (resto do país esbatido), limites das freguesias (CAOP/DGT), ocorrências agrupadas
 * em círculos (cor do estado que predomina) e pinos com o ícone da categoria. À esquerda filtra-se por categoria,
 * em baixo por estado (Recebida / Em execução / Concluída). Pesquisa por rua, lugar ou número TU-…
 */
(function () {
  const root = document.getElementById('live');
  if (!root) return;
  const countEl = document.getElementById('live-count') || { textContent: '' };   // contador (opcional)
  const role = root.dataset.role;
  const AREA = root.dataset.area;
  const ACTIVE = root.dataset.active === '1';
  const NATIONAL = root.dataset.national === '1';           // sem sessão: Portugal continental inteiro
  const PT = [36.95, 42.16, -9.53, -6.18];                 // [lat_min, lat_max, lng_min, lng_max] de Portugal continental            // a Câmara deste concelho já aderiu?
  let B = null;                                           // [lat_min, lat_max, lng_min, lng_max] do concelho
  let concelho = null;                                    // limites do concelho (Leaflet LatLngBounds)
  const PAD_TL = [16, 66], PAD_BR = [62, 20];             // espaço para a pesquisa (cima) e os botões (direita)
  const POPUP_W = Math.min(290, window.innerWidth - 150);   // cabe entre a barra das categorias e os botões da direita             // espaço para os botões por cima do mapa
  const GROUPS = {                                         // estados → grupo visível no mapa
    'registada': 'recebida', 'em-analise': 'recebida', 'em-tratamento': 'execucao', 'resolvida': 'concluida' };
  const COLORS = { recebida: '#e5484d', execucao: '#f5b301', concluida: '#30a46c' };
  const state = { items: [], cats: new Set(), groups: new Set(['recebida', 'execucao', 'concluida']),
                  me: null, markers: new Map(), parish: null, geo: null,
                  scope: 'tudo', view: 'mapa', open: null };
  // Estado inicial a partir do endereço (ligações partilháveis): ?tipo=1&tipo=3 · ?estado=recebida,execucao · ?vista=lista · ?meus=1 · ?ocorrencia=42
  const Q = new URLSearchParams(location.search);
  Q.getAll('tipo').concat(Q.getAll('extype')).forEach(t => String(t).split(',').forEach(x => /^\d+$/.test(x) && state.cats.add(x)));
  if (Q.get('estado')) { const g = Q.get('estado').split(',').filter(x => ['recebida', 'execucao', 'concluida'].includes(x)); if (g.length) state.groups = new Set(g); }
  if (Q.get('vista') === 'lista') state.view = 'lista';
  if (Q.get('meus') && role === 'citizen') state.scope = 'meus';
  const wantOpen = /^\d+$/.test(Q.get('ocorrencia') || '') ? +Q.get('ocorrencia') : null;
  const mineCountEl = document.getElementById('mine-count') || { textContent: '' };

  // ---------------------------------------------------------------- altura: ocupa o ecrã até à barra de baixo
  function fit() {
    const tab = document.querySelector('.tu-tabbar');
    const tabH = tab && getComputedStyle(tab).display !== 'none' ? tab.offsetHeight : 0;
    const top = root.getBoundingClientRect().top + window.scrollY;
    root.style.height = Math.max(420, window.innerHeight - top - tabH) + 'px';
    sizeRail();
    if (map) { map.invalidateSize(); limits(); }
  }

  // Barra das categorias: os botões encolhem para caberem TODOS entre o topo e os estados (nunca ficam cortados).
  function sizeRail() {
    const rail = document.getElementById('chips');
    if (!rail) return;
    const tiles = rail.children.length;
    const avail = rail.clientHeight - 8;                 // 4 px de margem em cima e em baixo (anel do botão ativo)
    const gap = avail / tiles < 40 ? 5 : 7;
    const size = Math.max(30, Math.min(46, Math.floor((avail - gap * (tiles - 1)) / tiles)));
    rail.style.setProperty('--tile', size + 'px');
    rail.style.setProperty('--tile-gap', gap + 'px');
  }

  // ---------------------------------------------------------------- mapa base
  let map = null, cluster = null, base = null, sat = null, parishLayer = null;
  if (typeof L !== 'undefined') {
    // Zoom em quartos (o concelho encaixa à justa no ecrã) com animações suaves (roda, gesto e voos).
    // Fluidez: inércia mais longa e suave ao largar, roda do rato sem saltos, contornos desenhados com margem
    // (ao arrastar não aparecem bocados por desenhar) e paragem firme no contorno do concelho (sem ressaltos).
    map = L.map('live-map', { zoomControl: false, zoomSnap: 0.25, zoomDelta: 0.5, wheelPxPerZoomLevel: 110, wheelDebounceTime: 25,
                              inertia: true, inertiaDeceleration: 2400, inertiaMaxSpeed: 2200, easeLinearity: 0.22,
                              renderer: L.svg({ padding: 0.6 }), minZoom: 6, maxBoundsViscosity: 1,
                              bounceAtZoomLimits: false }).setView([39.6, -8.0], 7);
    root.tuMap = map;
    // Vista de ruas «estilo Google»: Esri World Street Map (sem chave). Satélite: Esri World Imagery.
    // Sem sessão (Portugal inteiro) usa um mapa base cinzento-claro sem nomes, para os nomes dos distritos se lerem bem.
    base = L.tileLayer(NATIONAL ? 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}'
                                : 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19, keepBuffer: 6, updateWhenIdle: false, updateWhenZooming: false,
      attribution: 'Mapa &copy; Esri, HERE, Garmin, &copy; colaboradores <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19, keepBuffer: 6, updateWhenIdle: false, updateWhenZooming: false, attribution: 'Imagens &copy; Esri'
    });
    if (L.markerClusterGroup) {
      cluster = L.markerClusterGroup({ showCoverageOnHover: false, chunkedLoading: true, maxClusterRadius: 56, spiderfyOnMaxZoom: true,
                                       iconCreateFunction: clusterIcon });
    } else {
      cluster = L.layerGroup();
    }
    cluster.addTo(map);
  } else {
    document.getElementById('live-map').innerHTML =
      '<p class="p-3 small text-muted">Mapa indisponível (sem ligação à internet).</p>';
  }
  fit();
  // Só recalcula quando o tamanho muda mesmo (no telemóvel a barra do navegador dispara «resize» a toda a hora).
  // No telemóvel a barra do navegador aparece/esconde e dispara «resize» só com a altura a mudar um pouco:
  // isso redimensionava o mapa a meio de um gesto e os limites «saltavam». Só recalcula com mudanças reais.
  let lastW = window.innerWidth, lastH = window.innerHeight, resizeT = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeT);
    resizeT = setTimeout(() => {
      const w = window.innerWidth, h = window.innerHeight;
      if (w === lastW && Math.abs(h - lastH) < 140) return;
      lastW = w; lastH = h; fit();
    }, 150);
  });
  // A animação de «cair» dos pinos só na primeira vez (depois, ao aproximar/afastar, não volta a saltar).
  setTimeout(() => root.classList.add('is-settled'), 1600);

  // Limites do zoom e do arrastar, SEMPRE calculados a partir do concelho inteiro (e não do zoom atual):
  // aproximar e voltar a afastar regressa sempre ao concelho completo, nunca fica preso mais perto.
  function limits() {
    if (!map || !concelho) return;
    // O afastamento máximo fica FIXO no enquadramento exato da zona da pessoa (o concelho) e não se arrasta para fora.
    const z = Math.max(6, map.getBoundsZoom(concelho, false, L.point(PAD_TL[0] + PAD_BR[0], PAD_TL[1] + PAD_BR[1])));
    if (map.getZoom() < z) map.setZoom(z, { animate: false });
    map.setMinZoom(z);
    // Limite igual em todos os lados (cima, baixo, esquerda e direita): o mapa pára no contorno do concelho.
    applyBounds();
    lockAtMin();
    // O agrupamento (markercluster) prepara os níveis a partir do zoom mínimo que tinha ao ser criado:
    // volta a juntá-lo ao mapa para recalcular com o novo mínimo (senão, com zooms fracionários, os pinos sumiam).
    if (cluster && map.hasLayer(cluster) && cluster._tuMin !== z) {
      cluster._tuMin = z;
      map.removeLayer(cluster);
      map.addLayer(cluster);
    }
  }
  // No afastamento máximo (o concelho inteiro no ecrã) o mapa fica parado: não se arrasta para os lados, nem para cima
  // ou para baixo. Ao aproximar volta a poder arrastar-se, até ao contorno do concelho.
  // Perto do afastamento máximo, os gestos de zoom (dedos e roda) fazem-se à volta do centro: assim, ao voltar
  // ao concelho inteiro, o mapa não fica descentrado nem dá um salto no fim do gesto.
  function lockAtMin() {
    if (!map) return;
    const atMin = map.getZoom() <= map.getMinZoom() + 0.01;
    const near = map.getZoom() < map.getMinZoom() + 1.26;
    map.options.touchZoom = near ? 'center' : true;
    map.options.scrollWheelZoom = near ? 'center' : true;
    if (atMin && concelho) {
      if (map.dragging.enabled()) map.dragging.disable();
    } else if (!map.dragging.enabled()) map.dragging.enable();
  }
  // Limites do arrastar para o zoom atual: o contorno do concelho; mas se, numa direção, o concelho couber todo no
  // ecrã, os limites nessa direção ficam do tamanho do ecrã — o mapa não se mexe nesse sentido (antes deixava
  // arrastar e depois voltava para trás, sobretudo para baixo no telemóvel, que é mais alto do que largo).
  function applyBounds() {
    if (!map || !concelho) return;
    const z = map.getZoom(), size = map.getSize();
    const a = map.project(concelho.getNorthWest(), z), b = map.project(concelho.getSouthEast(), z);
    let x1 = a.x, x2 = b.x, y1 = a.y, y2 = b.y;
    if (x2 - x1 < size.x) { const m = (x1 + x2) / 2; x1 = m - size.x / 2; x2 = m + size.x / 2; }
    if (y2 - y1 < size.y) { const m = (y1 + y2) / 2; y1 = m - size.y / 2; y2 = m + size.y / 2; }
    const nb = L.latLngBounds(map.unproject(L.point(x1, y1), z), map.unproject(L.point(x2, y2), z));
    const cur = map.options.maxBounds;
    if (!cur || !cur.equals(nb, 1e-9)) map.setMaxBounds(nb);
    // no afastamento máximo fica sempre exatamente no mesmo sítio (ao voltar de um zoom não fica 1–2 px ao lado)
    if (z <= map.getMinZoom() + 0.01) {
      const mid = L.point((x1 + x2) / 2, (y1 + y2) / 2), now = map.project(map.getCenter(), z);
      if (mid.distanceTo(now) > 0.5) map.setView(map.unproject(mid, z), z, { animate: false });
    }
  }
  if (map) map.on('zoomend', () => { applyBounds(); lockAtMin(); });
  // Vista do concelho inteiro: sempre o mesmo centro e o mesmo zoom (os do afastamento máximo).
  function homeView() {
    const z = map.getMinZoom();
    const a = map.project(concelho.getNorthWest(), z), b = map.project(concelho.getSouthEast(), z);
    return { center: map.unproject(a.add(b).divideBy(2), z), zoom: z };
  }
  function showConcelho(animate = true) {
    if (!map || !concelho) return;
    if (map.getMinZoom() < 6.01) limits();
    const h = homeView();
    animate ? map.flyTo(h.center, h.zoom, { duration: 0.7 }) : map.setView(h.center, h.zoom, { animate: false });
  }


  // Contorno do concelho (verde claro por dentro, traço escuro), o resto do país esbatido e as freguesias.
  async function loadTerritory() {
    let geo;
    try {
      const res = await fetch(root.dataset.territory, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || 'erro');
      geo = await res.json();
    } catch (e) {
      countEl.textContent = 'Não foi possível carregar o mapa do concelho.';
      return;
    }
    const bb = geo.bbox;                                   // [lng_min, lat_min, lng_max, lat_max]
    B = NATIONAL ? PT.slice() : [bb[1], bb[3], bb[0], bb[2]];
    if (typeof L !== 'undefined') concelho = L.latLngBounds([B[0], B[2]], [B[1], B[3]]);
    if (map && NATIONAL) {
      // País inteiro (sem sessão): só Portugal continental, com o contorno do país e o resto esbatido,
      // tal como o concelho quando se tem conta.
      try {
        const pt = await (await fetch(root.dataset.country)).json();
        const f = pt.features[0];
        const rings = f.geometry.coordinates.map(p => p[0].map(([x, y]) => [y, x]));
        const o = concelho.pad(3), world = [[o.getNorth(), o.getWest()], [o.getNorth(), o.getEast()], [o.getSouth(), o.getEast()], [o.getSouth(), o.getWest()]];
        L.polygon([world, ...rings], { stroke: false, fillColor: '#eef1f4', fillOpacity: 0.62, interactive: false, className: 'tu-outside' }).addTo(map);
        L.geoJSON(f, { interactive: false, style: { color: '#1f3d2b', weight: 2.5, opacity: 0.9, fillColor: '#8fd19e', fillOpacity: 0.14 } }).addTo(map);
        const cb = L.geoJSON(f).getBounds();
        concelho = cb; B = [cb.getSouth(), cb.getNorth(), cb.getWest(), cb.getEast()];
      } catch (e) { /* sem contorno: fica só o enquadramento do país */ }
      // Os 18 distritos: limites finos e o nome no meio; tocar num distrito aproxima-o.
      try {
        const ds = await (await fetch(root.dataset.districts)).json();
        const names = L.layerGroup().addTo(map);
        L.geoJSON(ds, {
          style: { color: '#1f3d2b', weight: 1.2, opacity: 0.55, dashArray: '4 3', fill: true, fillOpacity: 0 },
          onEachFeature: (ft, layer) => {
            layer.on('click', () => map.flyToBounds(layer.getBounds(), { paddingTopLeft: PAD_TL, paddingBottomRight: PAD_BR, duration: 0.7 }));
            layer.on('mouseover', () => layer.setStyle({ fillColor: '#1f3d2b', fillOpacity: 0.06 }));
            layer.on('mouseout', () => layer.setStyle({ fillOpacity: 0 }));
            const p = ft.properties.label;
            L.marker(p, { interactive: false, keyboard: false,
              icon: L.divIcon({ className: 'tu-dist-label', html: `<span>${ft.properties.distrito}</span>`, iconSize: [0, 0] }) }).addTo(names);
          },
        }).addTo(map);
        const sizeNames = () => root.classList.toggle('is-far', map.getZoom() < map.getMinZoom() + 0.6);
        map.on('zoomend', sizeNames); sizeNames();
      } catch (e) { /* sem distritos: fica só o contorno do país */ }
    } else if (map) {
      const g = geo.outline.geometry;
      const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
      const rings = polys.map(p => p[0].map(([x, y]) => [y, x]));
      // Fora do concelho fica esbatido (mais claro e acinzentado): vê-se bem até onde vai a zona.
      const o = concelho.pad(3), world = [[o.getNorth(), o.getWest()], [o.getNorth(), o.getEast()], [o.getSouth(), o.getEast()], [o.getSouth(), o.getWest()]];
      L.polygon([world, ...rings], { stroke: false, fillColor: '#eef1f4', fillOpacity: 0.62, interactive: false, className: 'tu-outside' }).addTo(map);
      L.geoJSON(geo.outline, { interactive: false,
        style: { color: '#1f3d2b', weight: 3, opacity: 0.9, fillColor: '#8fd19e', fillOpacity: 0.24 } }).addTo(map);
    }
    state.geo = !NATIONAL && geo.parishes && geo.parishes.features.length ? geo.parishes : null;
    if (NATIONAL) { const pb = document.getElementById('gm-parishes'); if (pb) pb.hidden = true; }
    initParishes();
    // Abre na freguesia pedida (?freguesia=) ou na freguesia da pessoa; senão, o concelho inteiro.
    // Sem animação na 1.ª vista: uma animação a meio impedia o Leaflet de aplicar o enquadramento seguinte.
    // Abre sempre no concelho inteiro; só foca uma freguesia se o endereço o pedir (?freguesia=).
    const wanted = new URLSearchParams(location.search).get('freguesia');
    limits();
    if (wanted && parishFeature(wanted)) setParish(wanted, true, false);
    else showConcelho(false);
  }

  // ---------------------------------------------------------------- ícones
  function groupOf(i) { return GROUPS[i.slug] || 'recebida'; }

  function pinIcon(i) {
    const g = groupOf(i);
    const n = i.seen > 0 ? `<span class="tu-gpin-n">${i.seen + 1}</span>` : '';
    return L.divIcon({
      className: 'tu-gpin-wrap', iconSize: [36, 46], iconAnchor: [18, 44], popupAnchor: [0, -38],
      html: `<div class="tu-gpin" style="--pin:${i.color};--st:${COLORS[g]}"><i class="bi bi-${i.icon}"></i></div>${n}`
    });
  }

  function clusterIcon(c) {
    const kids = c.getAllChildMarkers();
    const tally = { recebida: 0, execucao: 0, concluida: 0 };
    kids.forEach(m => { tally[m.options.group] = (tally[m.options.group] || 0) + 1; });
    const top = Object.keys(tally).sort((a, b) => tally[b] - tally[a])[0];
    const n = kids.length;
    const size = n < 10 ? 40 : n < 50 ? 48 : 56;
    // anel com a proporção de cada estado (conic-gradient) e o número ao centro
    let acc = 0;
    const stops = ['recebida', 'execucao', 'concluida'].filter(k => tally[k]).map(k => {
      const from = acc; acc += tally[k] / n * 360;
      return `${COLORS[k]} ${from}deg ${acc}deg`;
    }).join(',');
    return L.divIcon({
      className: 'tu-gcluster-wrap', iconSize: [size, size],
      html: `<div class="tu-gcluster" style="--ring:conic-gradient(${stops});--st:${COLORS[top]}"><span>${n}</span></div>`
    });
  }

  // ---------------------------------------------------------------- dados
  async function load() {
    if (!ACTIVE) {                                         // concelho sem Câmara aderente: ainda não há ocorrências
      state.items = [];
      render();
      countEl.textContent = '0';
      return;
    }
    try {
      const url = state.scope === 'meus' ? '/api/v1/map?meus=1'
        : '/api/v1/map' + (B ? `?bbox=${B[2]},${B[0]},${B[3]},${B[1]}` : '');
      const data = await Townly.api(url);
      state.items = state.scope === 'meus' ? data.incidents
        : data.incidents.filter(i => !B || (i.lat >= B[0] && i.lat <= B[1] && i.lng >= B[2] && i.lng <= B[3]));
      if (mineCountEl && data.mine_total !== null && data.mine_total !== undefined) mineCountEl.textContent = data.mine_total;
      tagParishes();
      render();
      if (wantOpen && !state.opened) {
        state.opened = true;
        const i = state.items.find(x => x.id === wantOpen);
        if (i) openSheet(i, true);
      }
    } catch (e) {
      countEl.textContent = '!';
      Townly.toast('Não foi possível carregar as ocorrências.');
    }
  }

  function visible() {
    return state.items.filter(i => state.groups.has(groupOf(i)) &&
      (!state.cats.size || state.cats.has(String(i.category_id))) &&
      (!state.parish || i.parishCode === state.parish));
  }

  function render() {
    const byCat = state.items.filter(i => (!state.cats.size || state.cats.has(String(i.category_id))) &&
                                          (!state.parish || i.parishCode === state.parish));
    document.querySelectorAll('#gm-status button').forEach(b => {
      b.querySelector('b').textContent = byCat.filter(i => groupOf(i) === b.dataset.group).length;
    });
    const items = visible();
    (state.scope === 'meus' ? mineCountEl : countEl).textContent = items.length;
    syncUrl();
    renderList(items);
    if (!map) return;
    cluster.clearLayers();
    state.markers.clear();
    const markers = items.map(i => {
      const m = L.marker([i.lat, i.lng], { icon: pinIcon(i), title: i.category, group: groupOf(i), keyboard: true })
        .on('click', () => openSheet(i));
      state.markers.set(i.id, m);
      return m;
    });
    cluster.addLayers ? cluster.addLayers(markers) : markers.forEach(m => m.addTo(cluster));
  }

  // Endereço sempre igual ao que se vê (para partilhar ou voltar atrás).
  function syncUrl() {
    const url = new URL(location.href);
    ['tipo', 'extype', 'estado', 'vista', 'meus'].forEach(k => url.searchParams.delete(k));
    [...state.cats].forEach(c => url.searchParams.append('tipo', c));
    if (state.groups.size < 3) url.searchParams.set('estado', [...state.groups].join(','));
    if (state.view === 'lista') url.searchParams.set('vista', 'lista');
    if (state.scope === 'meus') url.searchParams.set('meus', '1');
    history.replaceState(null, '', url);
  }

  // ---------------------------------------------------------------- vista em lista
  const listEl = document.getElementById('gm-list');
  const listItems = document.getElementById('gm-list-items');
  const LABEL = { recebida: 'Recebida', execucao: 'Em execução', concluida: 'Concluída' };
  function fmtDate(v) { try { return new Date(String(v).replace(' ', 'T') + 'Z').toLocaleDateString('pt-PT', { day: '2-digit', month: 'short', year: 'numeric' }); } catch (e) { return ''; } }
  function renderList(items) {
    if (!listItems) return;
    const sorted = state.me ? items.map(i => ({ i, d: Townly.distance(state.me[0], state.me[1], i.lat, i.lng) })).sort((a, b) => a.d - b.d)
                            : items.map(i => ({ i }));
    listItems.replaceChildren(...sorted.map(({ i, d }) => {
      const g = groupOf(i);
      const li = el('li');
      const b = el('button', 'tu-glist-card is-' + g);
      b.type = 'button';
      const ph = el('span', 'tu-glist-photo');
      ph.style.setProperty('--c', i.color);
      ph.append(el('i', 'bi bi-' + i.icon));
      const img = el('img'); img.alt = ''; img.loading = 'lazy'; img.src = `/media/publica/${i.id}.jpg`; img.onerror = () => img.remove();
      ph.append(img);
      const body = el('span', 'tu-glist-body');
      const top = el('span', 'tu-glist-top');
      top.append(el('b', '', i.ticket), el('span', 'tu-gstatus is-' + g, i.status === 'Rejeitada' || i.status === 'Duplicada' ? i.status : LABEL[g]));
      body.append(top,
        el('span', 'tu-glist-cat', i.subcategory && i.subcategory !== 'Outro' ? i.subcategory : i.category),
        el('span', 'tu-glist-sub', i.category + (i.parishName ? ' · ' + i.parishName : '')),
        el('span', 'tu-glist-meta', (i.address ? i.address + ' · ' : '') + fmtDate(i.created_at) + (d !== undefined ? ' · a ' + Townly.formatDistance(d) : '')));
      b.append(ph, body);
      b.addEventListener('click', () => openSheet(i));
      li.append(b);
      return li;
    }));
    if (!items.length) {
      const empty = el('li', 'tu-glist-empty');
      empty.append(el('b', '', state.scope === 'meus' ? 'Ainda não comunicou nenhuma ocorrência.' : 'Sem ocorrências com estes filtros.'),
                   el('span', '', state.scope === 'meus' ? 'Quando comunicar um problema, aparece aqui.' : 'Experimente mostrar todas as categorias e estados.'));
      listItems.append(empty);
    }
  }
  function setView(v) {
    state.view = v;
    const btn = document.getElementById('gm-view');
    if (listEl) listEl.hidden = v !== 'lista';
    root.classList.toggle('is-list', v === 'lista');
    if (btn) { btn.setAttribute('aria-pressed', String(v === 'lista')); btn.title = v === 'lista' ? 'Ver no mapa' : 'Ver em lista'; }
    syncUrl();
    if (v === 'mapa' && map) setTimeout(() => map.invalidateSize(), 50);
  }
  const viewBtn = document.getElementById('gm-view');
  if (viewBtn) viewBtn.addEventListener('click', () => setView(state.view === 'lista' ? 'mapa' : 'lista'));

  // ---------------------------------------------------------------- Tudo / As minhas
  document.querySelectorAll('.tu-gmap-scope [data-scope]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.login) { location.href = b.dataset.login; return; }
    if (state.scope === b.dataset.scope) return;
    state.scope = b.dataset.scope;
    document.querySelectorAll('.tu-gmap-scope [data-scope]').forEach(x => {
      const on = x === b; x.classList.toggle('is-on', on); x.setAttribute('aria-selected', String(on));
    });
    load();
    if (state.scope === 'meus' && state.view === 'mapa' && map) showConcelho();
  }));

  // ---------------------------------------------------------------- detalhe (folha)
  const sheet = document.getElementById('gm-sheet');
  const sheetBody = document.getElementById('gm-sheet-body');
  function openSheet(i, fly) {
    state.open = i.id;
    sheetBody.replaceChildren(detail(i));
    sheet.hidden = false;
    root.classList.add('has-sheet');
    const url = new URL(location.href); url.searchParams.set('ocorrencia', i.id); history.replaceState(null, '', url);
    if (fly && map && state.view === 'mapa') map.flyTo([i.lat, i.lng], Math.max(map.getZoom(), 16), { duration: 0.6 });
    const close = sheetBody.querySelector('[data-sheet-close]');
    if (close) close.focus({ preventScroll: true });
  }
  function closeSheet() {
    sheet.hidden = true; state.open = null;
    root.classList.remove('has-sheet');
    const url = new URL(location.href); url.searchParams.delete('ocorrencia'); history.replaceState(null, '', url);
  }
  sheet.addEventListener('click', e => { if (e.target.closest('[data-sheet-close]')) closeSheet(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !sheet.hidden) closeSheet(); });

  // ---------------------------------------------------------------- freguesias (CAOP / DGT)
  function parishFeature(code) {
    return state.geo ? state.geo.features.find(f => TownlyParishes.codeOf(f) === String(code)) : null;
  }
  function tagParishes() {
    if (!state.geo) return;
    state.items.forEach(i => {
      const f = TownlyParishes.find(state.geo, i.lat, i.lng);
      i.parishCode = f ? TownlyParishes.codeOf(f) : null;
      i.parishName = f ? TownlyParishes.nameOf(f) : null;
    });
  }
  function initParishes() {
    if (!window.TownlyParishes || !state.geo) return;
    tagParishes();
    const btn = document.getElementById('gm-parishes');
    if (map && !parishLayer) {
      parishLayer = TownlyParishes.leafletLayer(map, state.geo, {
        line: f => {
          if (!ACTIVE) return 'Ainda não disponível';
          const code = TownlyParishes.codeOf(f);
          const n = state.items.filter(i => i.parishCode === code && groupOf(i) !== 'concluida').length;
          return TownlyParishes.countLine(n);
        },
        onClick: f => setParish(TownlyParishes.codeOf(f), false),
      });
      btn.addEventListener('click', () => {
        const on = map.hasLayer(parishLayer);
        on ? map.removeLayer(parishLayer) : parishLayer.addTo(map);
        btn.setAttribute('aria-pressed', String(!on));
        btn.classList.toggle('active', !on);
      });
    }
  }
  // Foca uma freguesia: só as ocorrências dela, contorno destacado e o botão «Todo o concelho» para sair.
  function setParish(code, zoom = true, animate = true) {
    const f = code ? parishFeature(code) : null;
    state.parish = f ? TownlyParishes.codeOf(f) : null;
    if (map) {
      if (f && zoom) {
        const b = L.geoJSON(f).getBounds(), opts = { paddingTopLeft: PAD_TL, paddingBottomRight: PAD_BR };
        animate ? map.flyToBounds(b, { ...opts, duration: 0.7 }) : map.fitBounds(b, { ...opts, animate: false });
      }
      if (!f && zoom) showConcelho(animate);
    }
    if (parishLayer) {
      if (f) { if (!map.hasLayer(parishLayer)) parishLayer.addTo(map); parishLayer.select && parishLayer.select(state.parish); }
      else parishLayer.clearSelection();
    }
    const url = new URL(location.href);
    f ? url.searchParams.set('freguesia', state.parish) : url.searchParams.delete('freguesia');
    history.replaceState(null, '', url);
    let chip = document.getElementById('gm-parish-chip');
    if (f) {
      if (!chip) {
        chip = el('button', 'tu-gmap-parish');
        chip.id = 'gm-parish-chip'; chip.type = 'button';
        chip.addEventListener('click', () => setParish(null));
        document.querySelector('.tu-gmap-top').append(chip);
      }
      chip.replaceChildren(el('span', '', TownlyParishes.nameOf(f)), el('small', '', 'Ver todo o concelho ×'));
      chip.title = 'Ver todo o concelho';
    } else if (chip) {
      chip.remove();
    }
    render();
  }

  // ---------------------------------------------------------------- popup
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;   // textContent: nunca injeta HTML
    return e;
  }

  function detail(i) {
    const box = el('div', 'tu-popup tu-gdetail');
    const bar = el('div', 'tu-gdetail-bar');
    const ttl = el('h2', '', i.ticket); ttl.id = 'gm-sheet-title';
    const x = el('button', 'tu-gdetail-close'); x.type = 'button'; x.setAttribute('data-sheet-close', ''); x.setAttribute('aria-label', 'Fechar');
    x.textContent = '×';
    bar.append(ttl, x);
    box.append(bar);
    // Fotografia do problema (miniatura pública, sem metadados); some se não existir.
    const ph = el('img', 'tu-popup-photo');
    ph.alt = 'Fotografia do problema';
    ph.src = `/media/publica/${i.id}.jpg`;
    ph.onerror = () => ph.remove();
    box.append(ph);
    const head = el('div', 'd-flex gap-2 align-items-center');
    const ic = el('div', 'tu-feed-icon');
    ic.style.setProperty('--c', i.color);
    ic.append(el('i', 'bi bi-' + i.icon));
    const title = el('div', 'min-w-0');
    title.append(el('strong', 'd-block', i.subcategory && i.subcategory !== 'Outro' ? i.subcategory : i.category),
      el('small', 'text-muted', i.category + ' · ' + Townly.timeAgo(i.created_at) + (i.parishName ? ` · ${i.parishName}` : '')));
    head.append(ic, title);
    const st = el('div', 'mt-2 d-flex align-items-center gap-2');
    const g = groupOf(i);
    const tag = el('span', 'tu-gstatus is-' + g, LABEL[g]);
    st.append(tag, el('small', 'text-muted', i.status));
    box.append(head, st);
    if (i.address) box.append(el('div', 'small mt-2', i.address));

    const people = el('div', 'small text-muted mt-1');
    people.textContent = i.seen > 0 ? `${i.seen + 1} munícipes viram este problema` : 'Comunicado por 1 munícipe';
    if (i.fixed > 0) people.textContent += ` · ${i.fixed} dizem que já está resolvido`;
    box.append(people);

    const nav = el('div', 'd-flex gap-2 mt-3');
    const go = el('a', 'btn btn-sm btn-primary flex-fill');
    go.href = Townly.gmapsDirections(i.lat, i.lng);
    go.target = '_blank'; go.rel = 'noopener';
    go.append(el('i', 'bi bi-sign-turn-right-fill'), document.createTextNode(' Como chegar'));
    nav.append(go);
    if (state.view === 'lista' && map) {
      const see = el('button', 'btn btn-sm btn-outline-primary flex-fill', 'Ver no mapa');
      see.type = 'button';
      see.addEventListener('click', () => { closeSheet(); setView('mapa'); openItem(i); });
      nav.append(see);
    }
    box.append(nav);
    // Quem comunicou (ou a entidade) abre a ficha completa: descrição, fotografias e histórico.
    if (i.mine || role === 'admin' || role === 'staff') {
      const full = el('a', 'btn btn-sm btn-outline-secondary w-100 mt-2', i.mine ? 'Abrir a minha ocorrência' : 'Abrir no painel');
      full.href = i.mine ? `/incidents/${i.id}` : `/admin/incidents/${i.id}`;
      box.append(full);
    }

    if (g === 'concluida') return box;
    if (role === 'anon') {
      const a = el('a', 'btn btn-sm btn-outline-primary w-100 mt-2', 'Entrar para confirmar');
      a.href = root.dataset.login;
      box.append(a);
    } else if (role === 'citizen') {
      if (i.mine) box.append(el('div', 'small fw-semibold text-success mt-2', 'Esta ocorrência é sua.'));
      else {
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
        if (state.open === i.id) sheetBody.replaceChildren(detail(i));
      } catch (e) {
        Townly.toast(e.message);
        b.disabled = false;
      }
    });
    return b;
  }

  function openItem(i) {
    const m = state.markers.get(i.id);
    if (!m || !map) { openSheet(i); return; }
    if (cluster.zoomToShowLayer) cluster.zoomToShowLayer(m, () => openSheet(i));
    else { map.setView(m.getLatLng(), 17); openSheet(i); }
  }

  // ---------------------------------------------------------------- filtros
  const chipsEl = document.getElementById('chips');
  if (chipsEl) chipsEl.addEventListener('click', e => {
    const tile = e.target.closest('.tu-rail-tile');
    if (!tile) return;
    const cat = tile.dataset.cat;
    if (!cat) state.cats.clear();
    else state.cats.has(cat) ? state.cats.delete(cat) : state.cats.add(cat);
    document.querySelectorAll('.tu-rail-tile').forEach(t => {
      const on = t.dataset.cat ? state.cats.has(t.dataset.cat) : !state.cats.size;
      t.classList.toggle('active', on);
      t.setAttribute('aria-pressed', String(on));
    });
    render();
  });

  const statusEl = document.getElementById('gm-status');
  if (statusEl) statusEl.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    const g = b.dataset.group;
    // 1.º toque com tudo ligado: mostra só esse estado; depois liga/desliga cada um
    if (state.groups.size === 3) { state.groups = new Set([g]); }
    else if (state.groups.has(g)) { state.groups.delete(g); if (!state.groups.size) state.groups = new Set(Object.keys(COLORS)); }
    else state.groups.add(g);
    document.querySelectorAll('#gm-status button').forEach(x => {
      const on = state.groups.has(x.dataset.group);
      x.classList.toggle('active', on);
      x.setAttribute('aria-pressed', String(on));
    });
    render();
  });

  // ---------------------------------------------------------------- botões do mapa
  if (map) {
    document.getElementById('gm-zin').addEventListener('click', () => map.zoomIn());
    document.getElementById('gm-zout').addEventListener('click', () => map.zoomOut());
    // Camadas: o botão do meio abre uma cápsula com «Satélite» e «Mapa» (a escolhida fica a azul).
    const layersBox = document.getElementById('gm-layers-box');
    const layersBtn = document.getElementById('gm-layers');
    const setLayersOpen = open => {
      layersBox.classList.toggle('is-open', open);
      layersBtn.setAttribute('aria-expanded', String(open));
      layersBox.querySelectorAll('[data-base]').forEach(b => { b.tabIndex = open ? 0 : -1; });
    };
    layersBtn.addEventListener('click', e => { e.stopPropagation(); setSettingsOpen(false); setLayersOpen(!layersBox.classList.contains('is-open')); });
    layersBox.querySelectorAll('[data-base]').forEach(b => b.addEventListener('click', e => {
      e.stopPropagation();
      const toSat = b.dataset.base === 'sat';
      if (toSat !== map.hasLayer(sat)) {
        map.removeLayer(toSat ? base : sat);
        (toSat ? sat : base).addTo(map).bringToBack();
      }
      layersBox.querySelectorAll('[data-base]').forEach(x => {
        const on = x === b;
        x.classList.toggle('is-on', on); x.setAttribute('aria-checked', String(on));
      });
      root.classList.toggle('is-sat', toSat);
      setTimeout(() => setLayersOpen(false), 260);
    }));

    // Opções do mapa (engrenagem): limites das freguesias e voltar ao concelho inteiro.
    const setBtn = document.getElementById('gm-settings');
    const setPop = document.getElementById('gm-settings-pop');
    function setSettingsOpen(open) {
      setPop.hidden = !open;
      setBtn.setAttribute('aria-expanded', String(open));
      setBtn.classList.toggle('is-open', open);
    }
    setBtn.addEventListener('click', e => { e.stopPropagation(); setLayersOpen(false); setSettingsOpen(setPop.hidden); });
    setPop.addEventListener('click', e => e.stopPropagation());
    document.getElementById('gm-fit').addEventListener('click', () => { setSettingsOpen(false); state.parish ? setParish(null) : showConcelho(); });
    document.addEventListener('click', () => { setSettingsOpen(false); setLayersOpen(false); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') { setSettingsOpen(false); setLayersOpen(false); } });
    map.on('movestart', () => { setLayersOpen(false); });
  }

  let meMarker = null;
  document.getElementById('locate').addEventListener('click', () => {
    if (!navigator.geolocation) { Townly.toast('O navegador não permite obter a localização.'); return; }
    navigator.geolocation.getCurrentPosition(pos => {
      state.me = [pos.coords.latitude, pos.coords.longitude];
      const inside = B && state.me[0] >= B[0] && state.me[0] <= B[1] && state.me[1] >= B[2] && state.me[1] <= B[3];
      if (!inside) { Townly.toast(NATIONAL ? 'Está fora de Portugal continental.' : 'Está fora do concelho de ' + AREA + '.'); return; }
      if (map) {
        if (meMarker) meMarker.setLatLng(state.me);
        else meMarker = L.marker(state.me, { icon: L.divIcon({ className: '', html: '<div class="tu-me-dot"></div>', iconSize: [18, 18] }) }).addTo(map);
        map.setView(state.me, 16);
      }
      render();                     // a lista passa a vir ordenada por distância
    }, () => Townly.toast('Não foi possível obter a sua localização.'), { enableHighAccuracy: true, timeout: 10000 });
  });

  // ---------------------------------------------------------------- pesquisa (n.º TU-…, categoria, rua ou lugar)
  const sToggle = document.getElementById('gm-search-toggle');
  const sForm = document.getElementById('gm-search');
  const sInput = document.getElementById('gm-q');
  const sResults = document.getElementById('gm-results');
  sToggle.addEventListener('click', () => {
    const open = sForm.hidden;
    sForm.hidden = !open;
    sToggle.setAttribute('aria-expanded', String(open));
    root.classList.toggle('is-searching', open);
    if (open) sInput.focus(); else sResults.replaceChildren();
  });
  let timer = null, seq = 0;
  sInput.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 300); });
  sForm.addEventListener('submit', e => { e.preventDefault(); search(); });

  function fold(s) { return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }

  async function search() {
    const q = sInput.value.trim();
    const my = ++seq;
    sResults.replaceChildren();
    if (q.length < 2) return;
    const fq = fold(q);
    const local = state.items.filter(i => fold(i.ticket).includes(fq) || fold(i.category).includes(fq) ||
      fold(i.subcategory).includes(fq) || fold(i.address).includes(fq) || fold(i.parishName).includes(fq)).slice(0, 5);
    local.forEach(i => sResults.append(result(i.ticket + ' · ' + (i.subcategory || i.category), i.address || i.parishName || '', () => openItem(i))));
    if (B) try {   // ruas e lugares só dentro do concelho (OpenStreetMap / Nominatim)
      const url = 'https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&countrycodes=pt&bounded=1&accept-language=pt' +
        `&viewbox=${B[2]},${B[1]},${B[3]},${B[0]}&q=${encodeURIComponent(q)}`;
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (my !== seq || !res.ok) return;
      (await res.json()).forEach(p => {
        const parts = p.display_name.split(',');
        sResults.append(result(parts[0], parts.slice(1, 3).join(',').trim(), () => {
          map.setView([+p.lat, +p.lon], 17);
        }, true));
      });
    } catch (e) { /* sem rede: ficam só os resultados das ocorrências */ }
    if (my === seq && !sResults.children.length) sResults.append(el('div', 'tu-gmap-empty', 'Sem resultados no concelho.'));
  }

  function result(title, sub, go, place) {
    const b = el('button', 'tu-gmap-result' + (place ? ' is-place' : ''));
    b.type = 'button';
    b.append(el('b', '', title), el('small', '', sub));
    b.addEventListener('click', () => { go(); sToggle.click(); });
    return b;
  }

  // Interface de acordo com o estado inicial (vindo do endereço).
  document.querySelectorAll('.tu-rail-tile').forEach(t => {
    const on = t.dataset.cat ? state.cats.has(t.dataset.cat) : !state.cats.size;
    t.classList.toggle('active', on); t.setAttribute('aria-pressed', String(on));
  });
  document.querySelectorAll('#gm-status button').forEach(x => {
    const on = state.groups.has(x.dataset.group); x.classList.toggle('active', on); x.setAttribute('aria-pressed', String(on));
  });
  if (state.scope === 'meus') document.querySelectorAll('.tu-gmap-scope [data-scope]').forEach(x => {
    const on = x.dataset.scope === 'meus'; x.classList.toggle('is-on', on); x.setAttribute('aria-selected', String(on));
  });
  if (state.view === 'lista') setView('lista');

  // Boas-vindas na 1.ª visita (até a pessoa marcar «Não mostrar novamente»; no máximo uma vez por sessão).
  (function intro() {
    const box = document.getElementById('gm-intro');
    if (!box || wantOpen) return;
    let skip = false;
    try { skip = localStorage.getItem('tu-map-intro') === 'nunca' || sessionStorage.getItem('tu-map-intro') === '1'; } catch (e) { skip = true; }
    if (skip || state.view === 'lista') return;             // na lista «Todas as ocorrências» não aparece
    box.hidden = false;
    const done = () => {
      try {
        sessionStorage.setItem('tu-map-intro', '1');
        if (document.getElementById('gm-intro-never').checked) localStorage.setItem('tu-map-intro', 'nunca');
      } catch (e) {}
      box.hidden = true;
    };
    box.querySelectorAll('[data-intro]').forEach(b => b.addEventListener('click', done));
    box.addEventListener('click', e => { if (e.target === box) done(); });
  })();

  loadTerritory().then(load);
  setInterval(load, 60000);   // «ao vivo»: atualiza a cada minuto
})();
