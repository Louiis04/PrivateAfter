const socket = io();

const mainVideo = document.getElementById('mainVideo');
const mainOverlay = document.getElementById('mainOverlay');
const grid = document.getElementById('grid');
const multiCamToggle = document.getElementById('multiCamToggle');

let localStream;
const cameraFeeds = new Map(); // camera_id -> { video, canvas, ctx }
const hidden = document.createElement('canvas');
let sendTimer;

async function startMainCamera() {
  try {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    mainVideo.srcObject = localStream;
    fitOverlayToVideo(mainVideo, mainOverlay);
  } catch (e) {
    console.error(e);
    alert('Erro ao acessar a webcam: ' + e.message);
  }
}

function fitOverlayToVideo(videoEl, canvasEl) {
  function resize() {
    canvasEl.width = videoEl.clientWidth;
    canvasEl.height = videoEl.clientHeight;
  }
  resize();
  new ResizeObserver(resize).observe(videoEl);
}

function drawBoxes(canvas, results) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#00ff00';
  ctx.fillStyle = 'rgba(0,0,0,0.5)';
  ctx.font = '14px sans-serif';

  for (const r of results) {
    const [x, y, w, h] = r.box;
    ctx.strokeRect(x, y, w, h);
    const label = r.name || 'Desconhecido';
    const tw = ctx.measureText(label).width + 8;
    const th = 18;
    ctx.fillRect(x, Math.max(0, y - th), tw, th);
    ctx.fillStyle = '#00ff00';
    ctx.fillText(label, x + 4, y - 4);
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
  }
}

function ensureGridCamera(cameraId) {
  if (cameraFeeds.has(cameraId)) return cameraFeeds.get(cameraId);
  const wrap = document.createElement('div');
  wrap.className = 'video-block';

  const title = document.createElement('h3');
  title.textContent = 'Câmera ' + cameraId;
  wrap.appendChild(title);

  const video = document.createElement('video');
  video.autoplay = true; video.playsInline = true;
  wrap.appendChild(video);

  const canvas = document.createElement('canvas');
  canvas.className = 'overlay';
  wrap.appendChild(canvas);

  grid.appendChild(wrap);
  fitOverlayToVideo(video, canvas);
  const obj = { video, canvas };
  cameraFeeds.set(cameraId, obj);
  return obj;
}

multiCamToggle.addEventListener('change', () => {
  if (multiCamToggle.checked) {
    socket.emit('enable_multicam', {});
  } else {
    socket.emit('disable_multicam', {});
    cameraFeeds.clear();
    grid.innerHTML = '';
  }
});

socket.on('recognition_update', (data) => {
  const { camera_id, results, frame_b64 } = data;
  if (!camera_id) return;
  if (camera_id === 'main') {
    drawBoxes(mainOverlay, results || []);
    return;
  }
  const { video, canvas } = ensureGridCamera(camera_id);
  if (frame_b64) {
    // optional: render remote frame
    const img = new Image();
    img.onload = () => {
      const ctx = canvas.getContext('2d');
      canvas.width = img.width; canvas.height = img.height;
      ctx.drawImage(img, 0, 0);
      drawBoxes(canvas, results || []);
    };
    img.src = frame_b64;
  } else {
    drawBoxes(canvas, results || []);
  }
});

startMainCamera();

function captureAndSend() {
  if (!mainVideo.videoWidth) return;
  if (!hidden.width || !hidden.height) {
    hidden.width = mainVideo.videoWidth;
    hidden.height = mainVideo.videoHeight;
  }
  const ctx = hidden.getContext('2d');
  ctx.drawImage(mainVideo, 0, 0, hidden.width, hidden.height);
  const dataURL = hidden.toDataURL('image/jpeg', 0.6);
  socket.emit('client_frame', { dataURL });
}

// Envie frames do main a cada ~150ms
sendTimer = setInterval(captureAndSend, 150);
