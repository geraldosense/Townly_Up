/* Townly Up — efeito «Dock» na barra de baixo do telemóvel.
 * Inspirado no componente Dock do React Bits (ampliação ao estilo macOS), escrito em JavaScript simples
 * porque a app não usa React. Ao deslizar o dedo (ou o rato) sobre a barra, o ícone mais perto cresce
 * e sobe, e os vizinhos crescem um pouco menos; ao largar, tudo volta ao lugar com uma mola suave.
 * Valores iguais aos do Dock original: ampliação 1,4×, distância 140 px, mola (massa .1, rigidez 150, amortecimento 12).
 */
(function () {
  const bar = document.querySelector('.tu-tabbar');
  if (!bar) return;
  const reduce = () => document.documentElement.getAttribute('data-motion') === 'reduce' ||
                       window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const MAGNIFY = 1.4, MAGNIFY_MAIN = 1.16, DISTANCE = 140, LIFT = 12;
  const MASS = 0.1, STIFFNESS = 150, DAMPING = 12;

  const items = [...bar.children].map(el => ({
    el,
    main: el.classList.contains('tu-tabbar-main'),
    s: 1, v: 0, target: 1,           // escala atual, velocidade, alvo
  }));
  let pointerX = null, raf = null, last = 0;

  function targets() {
    items.forEach(it => {
      if (pointerX === null) { it.target = 1; return; }
      const r = it.el.getBoundingClientRect();
      const d = Math.abs(pointerX - (r.left + r.width / 2));
      // curva em sino (cosseno): suave no centro, zero a partir de DISTANCE
      const k = d >= DISTANCE ? 0 : (Math.cos((d / DISTANCE) * Math.PI) + 1) / 2;
      it.target = 1 + ((it.main ? MAGNIFY_MAIN : MAGNIFY) - 1) * k;
    });
  }

  function frame(t) {
    const dt = Math.min(0.032, (t - (last || t)) / 1000) || 0.016;
    last = t;
    let moving = false;
    items.forEach(it => {
      // mola amortecida (F = -k·x - c·v ; a = F/m), integrada em 4 sub-passos para ficar estável
      for (let i = 0, h = dt / 4; i < 4; i++) {
        const a = (-STIFFNESS * (it.s - it.target) - DAMPING * it.v) / MASS;
        it.v += a * h;
        it.s += it.v * h;
      }
      if (Math.abs(it.s - it.target) > 0.001 || Math.abs(it.v) > 0.001) moving = true;
      else { it.s = it.target; it.v = 0; }
      const lift = (it.s - 1) / ((it.main ? MAGNIFY_MAIN : MAGNIFY) - 1 || 1) * LIFT;
      it.el.style.setProperty('--dock-scale', it.s.toFixed(4));
      it.el.style.setProperty('--dock-lift', (-lift).toFixed(2) + 'px');
      it.el.classList.toggle('is-docked', it.s > 1.08);
    });
    raf = moving ? requestAnimationFrame(frame) : null;
    if (!raf) last = 0;
  }
  const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };

  function move(e) {
    if (reduce()) return;
    pointerX = e.clientX;
    targets(); kick();
  }
  function leave() { pointerX = null; targets(); kick(); }

  bar.addEventListener('pointermove', move, { passive: true });
  bar.addEventListener('pointerdown', move, { passive: true });
  bar.addEventListener('pointerleave', leave);
  bar.addEventListener('pointercancel', leave);
  bar.addEventListener('pointerup', e => { if (e.pointerType !== 'mouse') setTimeout(leave, 120); });

  // Toque/clique: pequeno salto do ícone, como as apps a abrir na Dock.
  items.forEach(it => it.el.addEventListener('click', () => {
    if (reduce()) return;
    it.el.classList.remove('is-bounce'); void it.el.offsetWidth; it.el.classList.add('is-bounce');
  }));
})();
