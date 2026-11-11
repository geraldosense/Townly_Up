/* Townly Up — mapas Leaflet.
 * Cada <div data-map="..."> transforma-se num mapa:
 *   picker → o munícipe toca no mapa para escolher o local (preenche #latitude/#longitude)
 *   view   → mostra um único ponto (detalhe da ocorrência)
 *   multi  → mostra várias ocorrências (painel municipal), lidas de #map-points
 */
(function () {
  const STATUS_COLORS = {
    'registada': '#6c757d', 'em-analise': '#f0a500', 'em-tratamento': '#0b5ed7',
    'resolvida': '#1e8c3a', 'rejeitada': '#c62828', 'duplicada': '#7b1fa2'
  };

  function baseMap(el, lat, lng, zoom) {
    const map = L.map(el).setView([lat, lng], zoom);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
    return map;
  }

  function picker(el) {
    const latInput = document.getElementById('latitude');
    const lngInput = document.getElementById('longitude');
    const hasValue = latInput.value && lngInput.value;
    const start = hasValue
      ? [parseFloat(latInput.value.replace(',', '.')), parseFloat(lngInput.value.replace(',', '.'))]
      : [parseFloat(el.dataset.lat), parseFloat(el.dataset.lng)];
    const map = baseMap(el, start[0], start[1], hasValue ? 17 : 14);
    el._map = map;  // permite ao assistente redesenhar o mapa quando o passo fica visível
    let marker = null;

    function place(lat, lng, pan) {
      if (marker) marker.setLatLng([lat, lng]);
      else marker = L.marker([lat, lng], { draggable: true }).addTo(map)
        .on('dragend', e => { const p = e.target.getLatLng(); place(p.lat, p.lng, false); });
      latInput.value = lat.toFixed(6);
      lngInput.value = lng.toFixed(6);
      if (pan) map.setView([lat, lng], Math.max(map.getZoom(), 17));
      // Avisa o formulário (usado para procurar ocorrências iguais perto deste ponto).
      el.dispatchEvent(new CustomEvent('tu-location', { bubbles: true, detail: { lat, lng } }));
    }

    if (hasValue) place(start[0], start[1], false);
    map.on('click', e => place(e.latlng.lat, e.latlng.lng, false));

    // Alternativa manual: ao escrever as coordenadas, o marcador acompanha.
    [latInput, lngInput].forEach(input => input.addEventListener('change', () => {
      const lat = parseFloat(latInput.value.replace(',', '.'));
      const lng = parseFloat(lngInput.value.replace(',', '.'));
      if (!isNaN(lat) && !isNaN(lng)) place(lat, lng, true);
    }));

    const btn = document.getElementById('use-location');
    if (btn) btn.addEventListener('click', () => {
      if (!navigator.geolocation) { alertBox('O seu navegador não permite obter a localização.'); return; }
      btn.disabled = true;
      navigator.geolocation.getCurrentPosition(
        pos => { place(pos.coords.latitude, pos.coords.longitude, true); btn.disabled = false; },
        () => { alertBox('Não foi possível obter a localização. Marque o local no mapa.'); btn.disabled = false; },
        { enableHighAccuracy: true, timeout: 10000 }
      );
    });
  }

  function view(el) {
    const lat = parseFloat(el.dataset.lat), lng = parseFloat(el.dataset.lng);
    const map = baseMap(el, lat, lng, 17);
    L.marker([lat, lng]).addTo(map);
  }

  function multi(el) {
    const source = document.getElementById('map-points');
    const points = source ? JSON.parse(source.textContent) : [];
    const map = baseMap(el, parseFloat(el.dataset.lat), parseFloat(el.dataset.lng), 14);
    const markers = points.map(p => {
      const m = (window.Townly && p.icon)
        ? L.marker([p.lat, p.lng], { icon: Townly.pinIcon(p) }).addTo(map)
        : L.circleMarker([p.lat, p.lng], {
            radius: 9, weight: 2, color: '#fff', fillOpacity: 0.95,
            fillColor: STATUS_COLORS[p.slug] || '#333'
          }).addTo(map);
      const link = document.createElement('a');
      link.href = p.url;
      link.textContent = p.ticket;            // textContent: evita injetar HTML
      const box = document.createElement('div');
      box.append(link, document.createElement('br'), p.title, document.createElement('br'));
      const badge = document.createElement('span');
      badge.className = 'tu-badge st-' + p.slug;
      badge.textContent = p.status;
      box.append(badge);
      m.bindPopup(box);
      return m;
    });
    if (markers.length) map.fitBounds(L.featureGroup(markers).getBounds().pad(0.3), { maxZoom: 16 });
  }

  function alertBox(msg) {
    const div = document.createElement('div');
    div.className = 'alert alert-warning small py-2 mt-2';
    div.textContent = msg;
    document.getElementById('use-location')?.after(div);
    setTimeout(() => div.remove(), 6000);
  }

  document.querySelectorAll('[data-map]').forEach(el => {
    if (typeof L === 'undefined') {
      el.innerHTML = '<p class="p-3 small text-muted">Mapa indisponível (sem ligação à internet). ' +
        (el.dataset.map === 'picker' ? 'Indique a latitude e a longitude manualmente.' : '') + '</p>';
      return;
    }
    ({ picker, view, multi })[el.dataset.map]?.(el);
  });
})();
