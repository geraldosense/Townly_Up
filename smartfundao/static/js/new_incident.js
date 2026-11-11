/* Townly Up — «Comunicar em 3 passos».
 * Sem JavaScript o formulário mostra tudo numa página e continua a funcionar.
 * Com JavaScript vira um assistente: 1) categoria  2) foto + descrição  3) local + enviar.
 * A validação a sério é sempre feita no servidor (services.create_incident).
 */
(function () {
  const form = document.getElementById('incident-form');
  if (!form) return;
  const panes = [...form.querySelectorAll('.tu-step-pane')];
  const bars = [...form.querySelectorAll('.tu-steps span')];
  const nav = document.getElementById('wizard-nav');
  const prev = document.getElementById('prev');
  const next = document.getElementById('next');
  const photos = document.getElementById('photos');
  const dupBox = document.getElementById('duplicate');
  const mapEl = document.getElementById('map');
  const MAX = 3;
  let step = parseInt(form.dataset.errorStep || '1', 10);
  let located = false;
  let dismissed = new Set();

  form.classList.add('js-wizard');
  nav.hidden = false;

  // ------------------------------------------------------------ navegação
  function show(n) {
    step = n;
    panes.forEach(p => p.classList.toggle('is-current', +p.dataset.step === n));
    bars.forEach((b, i) => b.classList.toggle('done', i < n));
    prev.style.visibility = n === 1 ? 'hidden' : 'visible';
    next.hidden = n === panes.length;
    if (n === 3) {
      if (mapEl._map) setTimeout(() => mapEl._map.invalidateSize(), 50);
      const hasCoords = document.getElementById('latitude').value && document.getElementById('longitude').value;
      if (!located && !hasCoords) { located = true; document.getElementById('use-location').click(); }
      summary();
      checkDuplicates();
    }
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
        Townly.toast('Descreva o problema (pelo menos 10 caracteres).'); return false;
      }
      if (!document.getElementById('privacy').checked) {
        Townly.toast('Confirme que as fotos não identificam pessoas.'); return false;
      }
    }
    return true;
  }

  next.addEventListener('click', () => { if (valid(step)) show(step + 1); });
  prev.addEventListener('click', () => show(Math.max(1, step - 1)));

  // Escolher a categoria avança logo para o passo 2 (menos toques).
  form.querySelectorAll('input[name="category_id"]').forEach(r =>
    r.addEventListener('change', () => setTimeout(() => show(2), 180)));

  // ------------------------------------------------------------ fotografias
  // A câmara, a galeria e a pré-visualização estão em photos.js (que preenche #photos).

  function summary() {
    const cat = form.querySelector('input[name="category_id"]:checked');
    const label = cat ? form.querySelector(`label[for="${cat.id}"]`).textContent.trim() : '—';
    document.getElementById('summary').textContent =
      `Resumo: ${label} · ${photos.files.length} fotografia(s) · «${document.getElementById('description').value.trim().slice(0, 60)}»`;
  }

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
    for (let n = 1; n <= 2; n++) if (!valid(n)) { show(n); return; }
    const send = document.getElementById('send');
    send.disabled = true;  // evita envios duplicados ao carregar duas vezes
    const open = checked ? [...dupBox.querySelectorAll('.tu-dup-item')] : await checkDuplicates();
    send.disabled = false;
    if (open.length) { pendingSubmit = true; Townly.toast('Antes de enviar, diga se é o mesmo problema.'); return; }
    submit();
  });

  show(step);
})();
