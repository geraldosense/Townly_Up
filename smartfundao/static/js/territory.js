/* Townly Up — Distrito → Concelho → Freguesia (os 308 concelhos e as freguesias de cada um, CAOP/DGT).
 * Usado no registo e em Definições › O meu concelho. As freguesias vêm de /api/v1/territorio?concelho=…&leve=1. */
window.TownlyTerritory = (function () {
  const cache = {};
  async function parishesOf(concelho) {
    if (!concelho) return [];
    if (cache[concelho]) return cache[concelho];
    try {
      const res = await fetch('/api/v1/territorio?leve=1&concelho=' + encodeURIComponent(concelho));
      if (!res.ok) return null;                       // sem ligação aos dados das freguesias
      cache[concelho] = (await res.json()).parishes;
      return cache[concelho];
    } catch (e) { return null; }
  }

  function bind(o) {
    const { district: d, municipality: m, parish: p, parishWrap: wrap, note, data, active = [] } = o;
    let keepParish = p ? p.dataset.selected || p.value : '';
    function fillMunicipalities(keep) {
      const list = data[d.value] || [];
      m.replaceChildren(new Option(list.length ? 'Escolher…' : 'Escolha o distrito', ''));
      list.forEach(c => m.add(new Option(active.includes(c) ? c + ' ✓' : c, c, false, c === keep)));
      m.disabled = !list.length;
      changed();
    }
    async function changed() {
      const c = m.value, on = active.includes(c);
      if (note) {
        note.hidden = !c;
        note.classList.toggle('is-off', !on);
        note.textContent = !c ? '' : on
          ? '✓ O concelho de ' + c + ' já está no Townly Up: pode comunicar problemas.'
          : 'Ainda não disponível no seu concelho: pode criar conta e ver o mapa. Quando a Câmara de ' + c + ' aderir, passa a poder comunicar.';
      }
      if (!p) return;
      p.replaceChildren(new Option(c ? 'A carregar freguesias…' : 'Escolha o concelho', ''));
      p.disabled = true;
      if (wrap) wrap.hidden = !c;
      const list = await parishesOf(c);
      if (m.value !== c) return;                      // a pessoa entretanto mudou de concelho
      p.replaceChildren(new Option(list === null ? 'Indisponível agora (pode escolher depois)' : 'Não indicar', ''));
      (list || []).forEach(f => p.add(new Option(f.name.replace(/^União das freguesias de\s+/i, ''), f.code, false, f.code === keepParish)));
      p.disabled = !list || !list.length;
      keepParish = '';
    }
    d.addEventListener('change', () => fillMunicipalities(''));
    m.addEventListener('change', changed);
    fillMunicipalities(m.dataset.selected || '');
  }
  return { bind, parishesOf };
})();
