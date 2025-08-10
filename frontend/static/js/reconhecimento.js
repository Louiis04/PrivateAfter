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

function drawBoxes(canvas, results, srcW, srcH) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  const scaleX = srcW ? (canvas.width / srcW) : 1;
  const scaleY = srcH ? (canvas.height / srcH) : 1;

  ctx.lineWidth = 2;
  ctx.strokeStyle = '#00ff00';
  ctx.fillStyle = 'rgba(0,0,0,0.6)';
  ctx.font = '14px sans-serif';

  for (const r of results) {
    const [x, y, w, h] = r.box;
    const rx = x * scaleX;
    const ry = y * scaleY;
    const rw = w * scaleX;
    const rh = h * scaleY;

    // centro e raio do círculo (encaixado dentro do box)
    const cx = rx + rw / 2;
    const cy = ry + rh / 2;
    const radius = Math.min(rw, rh) / 2;

    // círculo
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();

    // rótulo acima do círculo
    const label = r.name || 'Desconhecido';
    const tw = ctx.measureText(label).width + 10;
    const th = 18;
    let lx = Math.max(0, Math.min(canvas.width - tw, cx - tw / 2));
    let ly = Math.max(0, cy - radius - th - 6);

    ctx.fillStyle = 'rgba(0,0,0,0.6)';
    ctx.fillRect(lx, ly, tw, th);
    ctx.fillStyle = '#00ff00';
    ctx.fillText(label, lx + 5, ly + th - 5);
    ctx.fillStyle = 'rgba(0,0,0,0.6)';
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
  const { camera_id, results, frame_b64, frame_w, frame_h } = data;
  if (!camera_id) return;
  if (camera_id === 'main') {
    drawBoxes(mainOverlay, results || [], frame_w, frame_h);
    return;
  }
  const { video, canvas } = ensureGridCamera(camera_id);
  if (frame_b64) {
    const img = new Image();
    img.onload = () => {
      const ctx = canvas.getContext('2d');
      canvas.width = img.width; canvas.height = img.height;
      ctx.drawImage(img, 0, 0);
      drawBoxes(canvas, results || [], img.width, img.height);
    };
    img.src = frame_b64;
  } else {
    drawBoxes(canvas, results || []); // sem frame, assume 1:1
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
