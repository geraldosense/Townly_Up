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

  // ------------------------------------------------------------ duplicados (RF14)
  let timer = null;
  mapEl.addEventListener('tu-location', () => { clearTimeout(timer); timer = setTimeout(checkDuplicates, 300); });

  async function checkDuplicates() {
    const lat = document.getElementById('latitude').value;
    const lng = document.getElementById('longitude').value;
    const cat = form.querySelector('input[name="category_id"]:checked');
    if (!lat || !lng || !cat) { dupBox.hidden = true; return; }
    try {
      const data = await Townly.api(`/api/v1/incidents/nearby?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}&category_id=${cat.value}`);
      const found = data.incidents.find(i => !dismissed.has(i.id));
      found ? showDuplicate(found, data.radius_m) : (dupBox.hidden = true);
    } catch (e) { dupBox.hidden = true; }
  }

  function showDuplicate(i, radius) {
    dupBox.replaceChildren();
    const title = document.createElement('div');
    title.className = 'fw-bold mb-1';
    title.innerHTML = '<i class="bi bi-exclamation-triangle-fill text-warning"></i> ';
    title.append(document.createTextNode('Parece que alguém já comunicou isto'));
    const text = document.createElement('p');
    text.className = 'small mb-2';
    text.textContent = `${i.ticket} · ${i.category} · a ${i.distance_m} m daqui · ${i.status}` +
      (i.seen ? ` · ${i.seen + 1} munícipes já viram` : '') + '. É o mesmo problema?';
    const row = document.createElement('div');
    row.className = 'd-flex flex-wrap gap-2';

    const yes = document.createElement('button');
    yes.type = 'button';
    yes.className = 'btn btn-success btn-sm';
    yes.innerHTML = '<i class="bi bi-eye"></i> Sim — «Eu também vi»';
    yes.addEventListener('click', async () => {
      yes.disabled = true;
      try {
        const res = await Townly.api(`/api/v1/incidents/${i.id}/confirm`, { method: 'POST', body: { kind: 'seen' } });
        Townly.updatePoints(res.total_points);
        Townly.toast(`Confirmado! +${res.points} pontos. Obrigado por evitar duplicados.`);
        setTimeout(() => { location.href = '/mapa'; }, 1400);
      } catch (e) { Townly.toast(e.message); yes.disabled = false; }
    });

    const no = document.createElement('button');
    no.type = 'button';
    no.className = 'btn btn-outline-secondary btn-sm';
    no.textContent = 'Não, é outro problema';
    no.addEventListener('click', () => { dismissed.add(i.id); checkDuplicates(); });

    row.append(yes, no);
    dupBox.append(title, text, row);
    dupBox.hidden = false;
  }

  // Evita envios duplicados ao carregar duas vezes no botão.
  form.addEventListener('submit', e => {
    for (let n = 1; n <= 2; n++) if (!valid(n)) { e.preventDefault(); show(n); return; }
    document.getElementById('send').disabled = true;
  });

  show(step);
})();
