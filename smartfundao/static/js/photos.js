/* Townly Up — fotografias da ocorrência: câmara dentro da aplicação + galeria.
 *
 * Como funciona:
 *  - Todas as fotos (tiradas na câmara ou escolhidas na galeria) ficam numa lista em memória.
 *  - A lista é copiada para o campo <input name="photos"> com DataTransfer, por isso o
 *    formulário é enviado ao servidor normalmente (e o servidor valida tudo de novo).
 *  - A câmara usa getUserMedia, que só funciona em contexto seguro (https ou localhost).
 *    Se não estiver disponível (ou o utilizador recusar), abre-se a câmara nativa do
 *    telemóvel através de <input capture="environment">.
 */
(function () {
  const input = document.getElementById('photos');
  if (!input || typeof DataTransfer === 'undefined') return; // navegador antigo: fica o campo normal

  const MAX = 3;
  const MAX_BYTES = 5 * 1024 * 1024;
  const actions = document.getElementById('photo-actions');
  const gallery = document.getElementById('photos-gallery');
  const nativeCam = document.getElementById('photos-capture');
  const openBtn = document.getElementById('open-camera');
  const preview = document.getElementById('photo-preview');

  const cam = document.getElementById('camera');
  const video = document.getElementById('camera-video');
  const shutter = document.getElementById('shutter');
  const flipBtn = document.getElementById('camera-flip');
  const closeBtn = document.getElementById('camera-close');
  const doneBtn = document.getElementById('camera-done');
  const countEl = document.getElementById('camera-count');
  const lastEl = document.getElementById('camera-last');
  const msgEl = document.getElementById('camera-msg');
  const flash = document.getElementById('camera-flash');

  let photos = [];            // [{file, source: 'camera' | 'galeria'}]
  let stream = null;
  let facing = 'environment'; // câmara traseira por defeito
  const canLiveCamera = window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;

  // Com JavaScript: mostra os botões e esconde o campo de ficheiro "cru".
  actions.hidden = false;
  input.classList.add('visually-hidden');
  input.tabIndex = -1;

  // ------------------------------------------------------------ lista de fotos
  function sync() {
    const dt = new DataTransfer();
    photos.forEach(p => dt.items.add(p.file));
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    render();
  }

  function add(file, source) {
    if (photos.length >= MAX) { Townly.toast(`Máximo de ${MAX} fotografias.`); return false; }
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { Townly.toast('Use fotografias JPEG, PNG ou WebP.'); return false; }
    if (file.size > MAX_BYTES) { Townly.toast('Essa fotografia tem mais de 5 MB.'); return false; }
    photos.push({ file, source });
    return true;
  }

  // Prepara qualquer fotografia do telemóvel antes de a juntar:
  //  • abre também HEIC/HEIF (iPhone) e fotografias enormes (12–48 MP, 5–15 MB);
  //  • endireita-a conforme o telemóvel estava (EXIF);
  //  • reduz para 1920 px no lado maior e grava em JPEG (~300–800 KB) — envio rápido mesmo com rede fraca.
  const MAX_SIDE = 1920;
  function decode(file) {
    if (window.createImageBitmap) {
      return createImageBitmap(file, { imageOrientation: 'from-image' }).catch(() => decodeImg(file));
    }
    return decodeImg(file);
  }
  function decodeImg(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('formato')); };
      img.src = url;
    });
  }
  function toBlob(canvas, q) { return new Promise(r => canvas.toBlob(r, 'image/jpeg', q)); }
  async function prepare(file) {
    const looksImage = (file.type || '').startsWith('image/') || /\.(jpe?g|png|webp|heic|heif|gif|bmp|tiff?)$/i.test(file.name || '');
    if (!looksImage) throw new Error('tipo');
    const src = await decode(file);
    const w = src.width || src.naturalWidth, h = src.height || src.naturalHeight;
    if (!w || !h) throw new Error('formato');
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);   // PNG transparente → fundo branco
    ctx.drawImage(src, 0, 0, canvas.width, canvas.height);
    if (src.close) src.close();
    let blob = await toBlob(canvas, 0.85);
    if (blob && blob.size > MAX_BYTES) blob = await toBlob(canvas, 0.7);
    if (!blob) throw new Error('formato');
    const base = (file.name || 'foto').replace(/\.[^.]+$/, '') || 'foto';
    return new File([blob], base + '.jpg', { type: 'image/jpeg', lastModified: Date.now() });
  }

  // Junta as fotografias escolhidas (galeria ou câmara do telemóvel), uma a uma, com indicação de progresso.
  let busy = false;
  async function addMany(files, source) {
    if (busy) return;
    busy = true;
    const list = Array.from(files).slice(0, Math.max(0, MAX - photos.length));
    if (files.length > list.length) Townly.toast(`Máximo de ${MAX} fotografias.`);
    preview.classList.add('is-busy');
    actions.classList.add('is-busy');
    for (const f of list) {
      try {
        add(await prepare(f), source);
      } catch (e) {
        Townly.toast(e.message === 'tipo' ? 'Escolha uma fotografia (imagem).'
          : 'Não foi possível abrir essa fotografia neste telemóvel. Tire-a com o botão «Tirar fotografia».');
      }
      sync();
    }
    preview.classList.remove('is-busy');
    actions.classList.remove('is-busy');
    busy = false;
  }

  function render() {
    preview.replaceChildren(...photos.map((p, index) => {
      const item = document.createElement('div');
      item.className = 'tu-photo-item';
      const img = document.createElement('img');
      img.src = URL.createObjectURL(p.file);
      img.alt = `Fotografia ${index + 1}`;
      img.onload = () => URL.revokeObjectURL(img.src);
      const tag = document.createElement('span');
      tag.className = 'tu-photo-tag';
      tag.textContent = p.source === 'camera' ? 'Câmara' : 'Galeria';
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.setAttribute('aria-label', `Remover fotografia ${index + 1}`);
      remove.innerHTML = '<i class="bi bi-x-lg"></i>';
      remove.addEventListener('click', () => { photos.splice(index, 1); sync(); });
      item.append(img, tag, remove);
      return item;
    }));
    const full = photos.length >= MAX;
    openBtn.disabled = full;
    actions.querySelector('label[for="photos-gallery"]').style.opacity = full ? .5 : '';
    gallery.disabled = full;
  }

  gallery.addEventListener('change', () => {
    const files = Array.from(gallery.files);
    gallery.value = '';
    addMany(files, 'galeria');
  });

  nativeCam.addEventListener('change', () => {
    const files = Array.from(nativeCam.files);
    nativeCam.value = '';
    addMany(files, 'camera');
  });

  // ------------------------------------------------------------ câmara
  openBtn.addEventListener('click', () => {
    if (photos.length >= MAX) return;
    if (!canLiveCamera) { nativeCam.click(); return; } // ex.: telemóvel a aceder por http://IP
    openCamera();
  });

  async function startStream() {
    stopStream();
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1440 } },
      audio: false,
    });
    video.srcObject = stream;
    video.style.transform = facing === 'user' ? 'scaleX(-1)' : ''; // selfie em espelho
    await video.play().catch(() => {});
  }

  function stopStream() {
    if (stream) stream.getTracks().forEach(t => t.stop());
    stream = null;
    video.srcObject = null;
  }

  async function openCamera() {
    cam.hidden = false;
    document.body.classList.add('tu-no-scroll');
    msgEl.hidden = true;
    shutter.disabled = true;
    updateCount();
    try {
      await startStream();
      shutter.disabled = false;
      // Só mostra "trocar câmara" se houver mais do que uma.
      const devices = await navigator.mediaDevices.enumerateDevices();
      flipBtn.hidden = devices.filter(d => d.kind === 'videoinput').length < 2;
    } catch (err) {
      showError(err);
    }
  }

  function closeCamera() {
    stopStream();
    cam.hidden = true;
    document.body.classList.remove('tu-no-scroll');
    openBtn.focus();
  }

  function showError(err) {
    const messages = {
      NotAllowedError: 'O acesso à câmara foi recusado. Permita a câmara nas definições do navegador (ícone do cadeado na barra de endereço) e tente de novo.',
      NotFoundError: 'Não foi encontrada nenhuma câmara neste dispositivo.',
      NotReadableError: 'A câmara está a ser usada por outra aplicação. Feche-a e tente de novo.',
      OverconstrainedError: 'Não foi possível usar esta câmara.',
    };
    msgEl.replaceChildren();
    const p = document.createElement('p');
    p.className = 'mb-3';
    p.textContent = messages[err && err.name] || 'Não foi possível abrir a câmara.';
    const alt = document.createElement('button');
    alt.type = 'button';
    alt.className = 'btn btn-success w-100 mb-2';
    alt.textContent = 'Usar a câmara do telemóvel';
    alt.addEventListener('click', () => { closeCamera(); nativeCam.click(); });
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-outline-secondary w-100';
    cancel.textContent = 'Fechar';
    cancel.addEventListener('click', closeCamera);
    msgEl.append(p, alt, cancel);
    msgEl.hidden = false;
  }

  function updateCount() {
    countEl.textContent = `${photos.length}/${MAX}`;
    doneBtn.hidden = photos.length === 0;
    shutter.disabled = !stream || photos.length >= MAX;
  }

  shutter.addEventListener('click', () => {
    if (!stream || !video.videoWidth) return;
    // Desenha o fotograma atual num canvas (máx. 1920 px no lado maior) e converte para JPEG.
    const scale = Math.min(1, 1920 / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext('2d');
    if (facing === 'user') { ctx.translate(canvas.width, 0); ctx.scale(-1, 1); }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    shutter.disabled = true;
    canvas.toBlob(blob => {
      if (!blob) { Townly.toast('Não foi possível tirar a fotografia.'); updateCount(); return; }
      const file = new File([blob], `foto-${Date.now()}.jpg`, { type: 'image/jpeg' });
      if (add(file, 'camera')) {
        sync();
        flash.classList.remove('go'); void flash.offsetWidth; flash.classList.add('go');
        lastEl.style.backgroundImage = `url(${canvas.toDataURL('image/jpeg', 0.5)})`;
        if (navigator.vibrate) navigator.vibrate(30);
      }
      updateCount();
      if (photos.length >= MAX) setTimeout(closeCamera, 450); // já tem as 3 fotos
    }, 'image/jpeg', 0.88);
  });

  flipBtn.addEventListener('click', async () => {
    facing = facing === 'environment' ? 'user' : 'environment';
    try { await startStream(); } catch (err) { showError(err); }
  });

  closeBtn.addEventListener('click', closeCamera);
  doneBtn.addEventListener('click', closeCamera);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !cam.hidden) closeCamera(); });
  // Desliga a câmara se a pessoa mudar de separador ou sair da página.
  document.addEventListener('visibilitychange', () => { if (document.hidden && !cam.hidden) closeCamera(); });
  window.addEventListener('pagehide', stopStream);

  window.TownlyPhotos = { count: () => photos.length };
})();
