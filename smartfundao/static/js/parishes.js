/* Townly Up — freguesias do concelho do Fundão (limites oficiais CAOP / DGT).
 *
 * Os limites estão em /static/data/freguesias_fundao.geojson (descarregado da DGT).
 * Este ficheiro oferece:
 *   - TownlyParishes.load()            lê o GeoJSON (ou null se ainda não existir)
 *   - TownlyParishes.find(geo, lat, lng)  em que freguesia está um ponto
 *   - TownlyParishes.svgMap(el, ...)   mapa interativo SEM internet (SVG), usado na página inicial
 *   - TownlyParishes.leafletLayer(...) camada para o mapa vivo (Leaflet)
 */
window.TownlyParishes = (function () {
  const URL_DATA = '/static/data/freguesias_fundao.geojson';
  let cache = null;

  async function load() {
    if (cache !== null) return cache;
    try {
      const res = await fetch(URL_DATA, { cache: 'force-cache' });
      cache = res.ok ? await res.json() : false;
    } catch (e) { cache = false; }
    if (!cache) console.info('[Townly] Limites das freguesias em falta: ' + URL_DATA + ' (ver README).');
    return cache || null;
  }

  /* "União das freguesias de Póvoa de Atalaia e Atalaia do Campo" -> "Póvoa de Atalaia e Atalaia do Campo" */
  function shortName(name) { return String(name || '').replace(/^União das freguesias de\s+/i, ''); }
  function nameOf(f) { return shortName(f.properties.freguesia); }
  function codeOf(f) { return String(f.properties.dicofre); }

  function polygons(geometry) {
    if (!geometry) return [];
    return geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates];
  }

  /* Ponto dentro de polígono (algoritmo "ray casting"); respeita buracos (anéis interiores). */
  function inRing(ring, x, y) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function contains(feature, lat, lng) {
    return polygons(feature.geometry).some(poly =>
      inRing(poly[0], lng, lat) && !poly.slice(1).some(hole => inRing(hole, lng, lat)));
  }
  function find(geo, lat, lng) { return geo ? geo.features.find(f => contains(f, lat, lng)) || null : null; }

  /* Conta ocorrências (do /api/v1/map) por freguesia: { dicofre: n } */
  function countBy(geo, items) {
    const counts = {};
    items.forEach(i => { const f = find(geo, i.lat, i.lng); if (f) counts[codeOf(f)] = (counts[codeOf(f)] || 0) + 1; });
    return counts;
  }

  // ----------------------------------------------------------------- tooltip partilhado
  let tip = null;
  function tooltip() {
    if (!tip) { tip = document.createElement('div'); tip.className = 'tu-parish-tip'; tip.hidden = true; document.body.append(tip); }
    return tip;
  }
  function showTip(title, line, x, y) {
    const t = tooltip();
    t.replaceChildren();
    const b = document.createElement('strong'); b.textContent = title;
    const s = document.createElement('span'); s.textContent = line;
    t.append(b, s);
    t.hidden = false;
    const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
    t.style.left = Math.min(x + pad, window.innerWidth - w - 8) + 'px';
    t.style.top = Math.max(8, y - h - pad) + 'px';
  }
  function hideTip() { if (tip) tip.hidden = true; }

  function countLine(n) {
    return n ? `${n} ocorrência${n > 1 ? 's' : ''} ativa${n > 1 ? 's' : ''}` : 'Sem ocorrências ativas';
  }

  // ----------------------------------------------------------------- mapa SVG (sem internet)
  /* Desenha as freguesias num <svg>. Projeção simples: x = longitude × cos(latitude média), y = −latitude. */
  function svgMap(el, geo, opts = {}) {
    const counts = opts.counts || {};
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    let latSum = 0, n = 0;
    geo.features.forEach(f => polygons(f.geometry).forEach(p => p[0].forEach(([lng, lat]) => { latSum += lat; n++; })));
    const k = Math.cos((latSum / n) * Math.PI / 180);
    const proj = ([lng, lat]) => [lng * k, -lat];
    geo.features.forEach(f => polygons(f.geometry).forEach(p => p[0].forEach(c => {
      const [x, y] = proj(c); minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    })));
    const W = 1000, scale = W / (maxX - minX), H = Math.round((maxY - minY) * scale);
    const pt = c => { const [x, y] = proj(c); return ((x - minX) * scale).toFixed(1) + ',' + ((y - minY) * scale).toFixed(1); };

    const max = Math.max(1, ...Object.values(counts));
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `-6 -6 ${W + 12} ${H + 12}`);
    svg.setAttribute('class', 'tu-territory-svg');
    svg.setAttribute('role', 'group');
    svg.setAttribute('aria-label', 'Mapa das freguesias do concelho do Fundão');

    geo.features
      .slice().sort((a, b) => nameOf(a).localeCompare(nameOf(b), 'pt'))
      .forEach(f => {
        const d = polygons(f.geometry).map(poly => poly.map(ring => 'M' + ring.map(pt).join('L') + 'Z').join('')).join('');
        const path = document.createElementNS(NS, 'path');
        const c = counts[codeOf(f)] || 0;
        path.setAttribute('d', d);
        path.setAttribute('fill-rule', 'evenodd');
        path.setAttribute('class', 'tu-parish');
        path.setAttribute('tabindex', '0');
        path.setAttribute('role', 'button');
        path.setAttribute('aria-label', `${nameOf(f)}: ${countLine(c)}`);
        path.dataset.code = codeOf(f);
        // Intensidade da cor conforme o nº de ocorrências ativas (0 = cor base).
        if (opts.heat !== false && c) path.style.setProperty('--heat', (0.25 + 0.75 * c / max).toFixed(2));
        const line = opts.line ? opts.line(f, c) : countLine(c);
        if (opts.tooltip !== false) {
          path.addEventListener('mousemove', e => showTip(nameOf(f), line, e.clientX, e.clientY));
          path.addEventListener('mouseleave', hideTip);
          path.addEventListener('focus', () => { const r = path.getBoundingClientRect(); showTip(nameOf(f), line, r.left + r.width / 2, r.top + r.height / 2); });
          path.addEventListener('blur', hideTip);
        }
        if (opts.onClick) {
          path.addEventListener('click', () => opts.onClick(f));
          path.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); opts.onClick(f); } });
        }
        svg.append(path);
      });
    el.replaceChildren(svg);
    return svg;
  }

  // ----------------------------------------------------------------- camada Leaflet (mapa vivo)
  const BASE = { color: '#0b2a5b', weight: 1.2, opacity: .55, fillColor: '#0b2a5b', fillOpacity: .03 };
  const HOVER = { color: '#0e9a3e', weight: 3, opacity: 1, fillColor: '#0e9a3e', fillOpacity: .18 };

  function leafletLayer(map, geo, opts = {}) {
    let selected = null;
    const layer = L.geoJSON(geo, {
      style: () => ({ ...BASE }),
      onEachFeature: (f, lyr) => {
        lyr.bindTooltip(() => {
          const box = document.createElement('div');
          const b = document.createElement('strong'); b.textContent = nameOf(f);
          const s = document.createElement('div'); s.className = 'small';
          s.textContent = opts.line ? opts.line(f) : '';
          box.append(b, s);
          return box;
        }, { sticky: true, direction: 'top', className: 'tu-parish-leaflet-tip', opacity: 1 });
        lyr.on('mouseover', () => { lyr.setStyle(HOVER); lyr.bringToFront(); });
        lyr.on('mouseout', () => { if (lyr !== selected) layer.resetStyle(lyr); });
        lyr.on('click', e => {
          if (selected && selected !== lyr) layer.resetStyle(selected);
          selected = lyr;
          lyr.setStyle(HOVER);
          if (opts.onClick) opts.onClick(f, lyr, e);
        });
      },
    });
    layer.clearSelection = () => { if (selected) layer.resetStyle(selected); selected = null; };
    layer.select = code => {
      layer.eachLayer(l => {
        if (codeOf(l.feature) === String(code)) {
          if (selected) layer.resetStyle(selected);
          selected = l; l.setStyle(HOVER);
        }
      });
    };
    return layer;
  }

  return { load, find, contains, countBy, shortName, nameOf, codeOf, svgMap, leafletLayer, countLine };
})();
