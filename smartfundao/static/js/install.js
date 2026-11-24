/* Townly Up — «Instalar a app» (PWA).
 * Android / Chrome / Edge: guarda o pedido de instalação do navegador (beforeinstallprompt) e mostra-o ao tocar.
 * iPhone / iPad (Safari): não há pedido automático — mostra os 3 passos (Partilhar › Adicionar ao ecrã principal).
 * Dentro da app já instalada (modo «standalone») os botões ficam escondidos.
 */
(function () {
  const buttons = () => document.querySelectorAll('[data-install]');
  const sheet = document.getElementById('tu-install-sheet');
  const standalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  const ua = navigator.userAgent || '';
  const ios = /iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let deferred = null;

  function show(on) { buttons().forEach(b => { b.hidden = !on; }); }
  if (standalone) { show(false); return; }
  if (ios) show(true);                              // Safari no iPhone: mostra sempre (com os passos)

  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();                             // guarda o pedido para o botão da própria app
    deferred = e;
    show(true);
  });
  window.addEventListener('appinstalled', () => {
    deferred = null; show(false);
    if (window.Townly) Townly.toast('Townly Up instalado no ecrã inicial.');
  });

  function openSheet(open) {
    if (!sheet) return;
    sheet.hidden = !open;
    document.body.classList.toggle('tu-no-scroll', open);
  }
  document.addEventListener('click', async e => {
    if (e.target.closest('[data-install-close]')) { openSheet(false); return; }
    const b = e.target.closest('[data-install]');
    if (!b) return;
    e.preventDefault();
    if (deferred) {
      deferred.prompt();
      const choice = await deferred.userChoice.catch(() => null);
      deferred = null;
      if (choice && choice.outcome === 'accepted') show(false);
      return;
    }
    openSheet(true);                                // iPhone (ou navegador sem pedido automático)
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && sheet && !sheet.hidden) openSheet(false); });
})();
