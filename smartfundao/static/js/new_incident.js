/* Townly Up — «Comunicar em 2 passos».
 * Sem JavaScript o formulário mostra tudo numa página e continua a funcionar.
 * Com JavaScript vira um assistente: 1) tipo de problema  2) fotografia (o local fica registado com ela) + enviar.
 * A validação a sério é sempre feita no servidor (services.create_incident).
 */
(function () {
  const form = document.getElementById('incident-form');
  if (!form) return;
  const panes = [...form.querySelectorAll('.tu-step-pane')];
  const bars = [...form.querySelectorAll('.tu-stepper li')];
  const nav = document.getElementById('wizard-nav');
  const prev = document.getElementById('prev');
  const next = document.getElementById('next');
  const photos = document.getElementById('photos');
  const dupBox = document.getElementById('duplicate');
  const mapEl = document.getElementById('map');
  const MAX = 3;
  let step = parseInt(form.dataset.errorStep || '1', 10);
  let dismissed = new Set();

  form.classList.add('js-wizard');
  nav.hidden = false;

  // ------------------------------------------------------------ navegação
  function show(n) {
    step = n;
    panes.forEach(p => p.classList.toggle('is-current', +p.dataset.step === n));
    bars.forEach((b, i) => {
      b.classList.toggle('is-done', i < n - 1);
      b.classList.toggle('is-current', i === n - 1);
      if (i === n - 1) b.setAttribute('aria-current', 'step'); else b.removeAttribute('aria-current');
    });
    prev.style.visibility = n === 1 ? 'hidden' : 'visible';
    next.hidden = n === panes.length;
    nav.hidden = n === panes.length;   // no último passo: só «Enviar» (voltar = tocar no passo 1)
    if (n === panes.length && mapEl._map) setTimeout(() => mapEl._map.invalidateSize(), 50);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function valid(n) {
    if (n === 1 && !form.querySelector('input[name="category_id"]:checked')) {
      Townly.toast('Escolha o tipo de problema.'); return false;
    }
    if (n === 2) {
      if (!photos.files.length) { Townly.toast('Tire uma fotografia do problema (ou escolha da galeria).'); return false; }
      if (photos.files.length > MAX) { Townly.toast('Máximo de ' + MAX + ' fotografias.'); return false; }
      if (document.getElementById('description').value.trim().length < 10) {
        Townly.toast('Descreva o problema numa frase (pelo menos 10 caracteres).');
        caption.focus(); return false;
      }
      if (!document.getElementById('latitude').value || !document.getElementById('longitude').value) {
        setState('manual'); Townly.toast('Falta o local: toque no mapa no sítio do problema.');
        card.scrollIntoView({ behavior: 'smooth', block: 'center' }); return false;
      }
    }
    return true;
  }

  next.addEventListener('click', () => { if (valid(step)) show(step + 1); });
  prev.addEventListener('click', () => show(Math.max(1, step - 1)));
  // Tocar num passo já feito volta a esse passo.
  bars.forEach((b, i) => b.addEventListener('click', () => { if (i < step - 1) show(i + 1); }));

  // Escolher a categoria mostra os subtipos (logo abaixo); escolher o subtipo avança para o passo 2.
  // Categorias sem subtipos (ou só «Outro») avançam logo, como antes.
  form.querySelectorAll('input[name="category_id"]').forEach(r =>
    r.addEventListener('change', () => {
      const group = form.querySelector(`.tu-subcat-group[data-cat="${r.value}"]`);
      const options = group ? group.querySelectorAll('input') : [];
      if (options.length <= 1) { if (options[0]) options[0].checked = true; setTimeout(() => show(2), 180); return; }
      setTimeout(() => document.getElementById('subcats').scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 60);
    }));
  form.querySelectorAll('input[name="subcategory_id"]').forEach(r =>
    r.addEventListener('change', () => setTimeout(() => show(2), 220)));

  // ------------------------------------------------------------ fotografias
  // A câmara, a galeria e a pré-visualização estão em photos.js (que preenche #photos).

  // A descrição é uma legenda da fotografia: cresce à medida que se escreve.
  const caption = document.getElementById('description');
  const grow = () => { caption.style.height = 'auto'; caption.style.height = Math.min(caption.scrollHeight, 160) + 'px'; };
  caption.addEventListener('input', grow);
  document.addEventListener('tu-photos-changed', () => setTimeout(grow, 0));

  // ------------------------------------------------------------ local registado com a fotografia
  // 1.º a posição guardada na própria fotografia (GPS/EXIF); 2.º se não tiver, a localização do telemóvel nesse momento.
  // Aparece numa linha (morada + origem). O mapa só abre em «Alterar» ou quando não foi possível obter o local.
  // O local segue a 1.ª fotografia: trocá-la volta a calcular o local; um acerto no mapa vale só para essa fotografia.
  const card = document.getElementById('loc-card');
  const sourceEl = document.getElementById('loc-source');
  const addrEl = document.getElementById('address-text'), addrInput = document.getElementById('address');
  const noteEl = document.getElementById('loc-note');
  const editBtn = document.getElementById('loc-edit');
  const latEl = document.getElementById('latitude'), lngEl = document.getElementById('longitude');
  const LABELS = { foto: 'Da fotografia', telemovel: 'Localização do telemóvel', mapa: 'Marcado no mapa' };
  let origin = card.dataset.state === 'set' ? 'mapa' : null;
  let basedOn = null, asking = false;

  function openMap(open) {
    card.classList.toggle('is-open', open);
    editBtn.setAttribute('aria-expanded', String(open));
    editBtn.textContent = open ? 'Concluído' : 'Alterar';
    if (open) setTimeout(() => mapEl._map && mapEl._map.invalidateSize(), 30);
  }
  function setState(state) {
    card.dataset.state = state;
    if (state === 'locating') { addrEl.textContent = 'A registar o local…'; sourceEl.textContent = 'A obter a localização do telemóvel'; }
    if (state === 'manual') {
      addrEl.textContent = 'Marque o local no mapa';
      sourceEl.textContent = 'Não foi possível obter a localização automaticamente';
      openMap(true);
    }
    if (state === 'wait') openMap(false);
  }
  function setLocation(lat, lng, from, accuracy) {
    origin = from;
    if (from !== 'mapa') { addrEl.textContent = 'Local registado'; addrInput.value = ''; }
    setState('set');
    if (mapEl._place) mapEl._place(lat, lng, true);
    sourceEl.textContent = LABELS[from] + (accuracy ? ` · ± ${Math.max(5, Math.round(accuracy))} m` : '');
  }
  function deviceLocation(fresh) {
    if (!navigator.geolocation) { if (!origin) setState('manual'); return; }
    if (asking) return;
    asking = true;
    if (!origin) setState('locating');
    navigator.geolocation.getCurrentPosition(
      pos => { asking = false; setLocation(pos.coords.latitude, pos.coords.longitude, 'telemovel', pos.coords.accuracy); },
      () => { asking = false; if (!origin) setState('manual'); },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: fresh ? 0 : 20000 }
    );
  }
  document.addEventListener('tu-photos-changed', e => {
    const first = e.detail;
    if (!first) {   // sem fotografias: o local volta a ficar à espera
      basedOn = null; origin = null; latEl.value = ''; lngEl.value = ''; addrInput.value = '';
      sourceEl.textContent = ''; setState('wait');
      return;
    }
    if (first.id === basedOn) return;
    basedOn = first.id;
    origin = null;
    if (first.gps) setLocation(first.gps.lat, first.gps.lng, 'foto', first.gps.accuracy);
    else deviceLocation(true);
  });
  mapEl.addEventListener('tu-location', e => {
    if (!e.detail || !e.detail.manual) return;
    origin = 'mapa'; card.dataset.state = 'set';
    addrEl.textContent = 'Local registado'; addrInput.value = '';
    sourceEl.textContent = LABELS.mapa;
  });
  editBtn.addEventListener('click', () => openMap(!card.classList.contains('is-open')));
  document.getElementById('loc-now').addEventListener('click', () => { origin = null; deviceLocation(true); });
  if (card.dataset.state === 'set') sourceEl.textContent = LABELS.mapa;


  // ------------------------------------------------------------ repetidos (RF14, deteção inteligente)
  // O servidor compara local, categoria, texto e «impressão digital» das fotografias.
  // Para cada ocorrência parecida, o munícipe escolhe: «Eu também vi» ou «É outro problema».
  let timer = null;
  let checked = false;          // já verificámos com os dados atuais?
  let pendingSubmit = false;    // o munícipe carregou em Enviar enquanto verificávamos
  ['change', 'input'].forEach(ev => form.addEventListener(ev, e => {
    if (e.target.name !== 'not_duplicate') checked = false;
  }));
  mapEl.addEventListener('tu-location', () => { checked = false; clearTimeout(timer); timer = setTimeout(checkDuplicates, 400); });

  function markDifferent(id) {
    if (form.querySelector(`input[name="not_duplicate"][value="${id}"]`)) return;
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = 'not_duplicate'; input.value = id;
    form.append(input);
    dismissed.add(id);
  }

  async function checkDuplicates() {
    const lat = document.getElementById('latitude').value;
    const lng = document.getElementById('longitude').value;
    if (!lat || !lng || !form.querySelector('input[name="category_id"]:checked')) { dupBox.hidden = true; return []; }
    try {
      const data = await Townly.api('/api/v1/incidents/similar', { method: 'POST', body: new FormData(form) });
      checked = true;
      const open = data.incidents.filter(i => !dismissed.has(i.id));
      open.length ? showDuplicates(open) : (dupBox.hidden = true);
      return open;
    } catch (e) { checked = true; dupBox.hidden = true; return []; }  // sem verificação: o servidor volta a verificar
  }

  function showDuplicates(list) {
    dupBox.replaceChildren();
    const head = document.createElement('div');
    head.className = 'fw-bold mb-1';
    head.innerHTML = '<i class="bi bi-exclamation-triangle-fill text-warning"></i> ';
    head.append(document.createTextNode(list.length > 1 ? 'Estes problemas parecem o mesmo que o seu' : 'Este problema parece já ter sido comunicado'));
    dupBox.append(head);

    list.forEach(i => {
      const item = document.createElement('div');
      item.className = 'tu-dup-item';
      const text = document.createElement('div');
      text.className = 'flex-grow-1';
      const top = document.createElement('div');
      top.innerHTML = '<b></b> · ';
      top.querySelector('b').textContent = i.ticket;
      top.append(document.createTextNode(`${i.category} · ${i.status}` + (i.seen ? ` · ${i.seen + 1} munícipes já viram` : '')));
      const why = document.createElement('div');
      why.className = 'small text-muted';
      why.innerHTML = '<span class="tu-dup-score"></span> — ';
      why.querySelector('span').textContent = `${Math.round(i.score * 100)}% parecida`;
      why.append(document.createTextNode(i.reasons.join(' · ')));

      const actions = document.createElement('div');
      actions.className = 'tu-dup-actions';
      const same = document.createElement('button');
      same.type = 'button';
      same.className = 'btn btn-success btn-sm';
      same.innerHTML = '<i class="bi bi-eye"></i> É este — «Eu também vi»';
      same.addEventListener('click', async () => {
        same.disabled = true;
        try {
          const res = await Townly.api(`/api/v1/incidents/${i.id}/confirm`, { method: 'POST', body: { kind: 'seen' } });
          Townly.updatePoints(res.total_points);
          Townly.toast(`Obrigado! Juntámos o seu aviso ao ${i.ticket} (+${res.points} pontos).`);
          setTimeout(() => { location.href = '/mapa'; }, 1400);
        } catch (e) { Townly.toast(e.message); same.disabled = false; }
      });
      const other = document.createElement('button');
      other.type = 'button';
      other.className = 'btn btn-outline-secondary btn-sm';
      other.textContent = 'Não, é outro problema';
      other.addEventListener('click', () => {
        markDifferent(i.id);
        item.remove();
        if (!dupBox.querySelector('.tu-dup-item')) {
          dupBox.hidden = true;
          if (pendingSubmit) { pendingSubmit = false; submit(); }
        }
      });
      actions.append(same, other);
      text.append(top, why, actions);
      item.append(text);
      dupBox.append(item);
    });
    dupBox.hidden = false;
    dupBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function submit() {
    document.getElementById('send').disabled = true;
    HTMLFormElement.prototype.submit.call(form);
  }

  form.addEventListener('submit', async e => {
    e.preventDefault();
    for (let n = 1; n <= 2; n++) if (!valid(n)) { if (n !== step) show(n); return; }
    const send = document.getElementById('send');
    send.disabled = true;  // evita envios duplicados ao carregar duas vezes
    const open = checked ? [...dupBox.querySelectorAll('.tu-dup-item')] : await checkDuplicates();
    send.disabled = false;
    if (open.length) { pendingSubmit = true; Townly.toast('Antes de enviar, diga se é o mesmo problema.'); return; }
    submit();
  });

  show(step);
})();
