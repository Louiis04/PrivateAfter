const socket = io();

const nameInput = document.getElementById('nameInput');
const sourceSelect = document.getElementById('sourceSelect');
const ipGroup = document.getElementById('ipGroup');
const ipUrlInput = document.getElementById('ipUrl');
const saveIpCamBtn = document.getElementById('saveIpCam');
const preview = document.getElementById('preview');
const hiddenCanvas = document.getElementById('hiddenCanvas');
const captureBtn = document.getElementById('captureBtn');

let localStream;
let captureInterval;
let lastIpCamId = 'cam1';

function toggleIpGroup() {
  ipGroup.classList.toggle('hidden', sourceSelect.value !== 'ip');
}
sourceSelect.addEventListener('change', toggleIpGroup);

async function startLocalCamera() {
  try {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    preview.srcObject = localStream;
  } catch (e) {
    alert('Erro ao acessar a webcam: ' + e.message);
  }
}

function stopLocalCamera() {
  if (localStream) {
    localStream.getTracks().forEach(t => t.stop());
    localStream = null;
  }
}

function drawFrameToCanvas() {
  if (!hiddenCanvas.width || !hiddenCanvas.height) {
    hiddenCanvas.width = preview.videoWidth;
    hiddenCanvas.height = preview.videoHeight;
  }
  const ctx = hiddenCanvas.getContext('2d');
  ctx.drawImage(preview, 0, 0, hiddenCanvas.width, hiddenCanvas.height);
  return hiddenCanvas.toDataURL('image/jpeg', 0.8);
}

captureBtn.addEventListener('click', async () => {
  const name = nameInput.value.trim();
  if (!name) return alert('Informe um nome.');

  const samples = [];
  for (let i = 0; i < 5; i++) {
    samples.push(drawFrameToCanvas());
    await new Promise(r => setTimeout(r, 200));
  }
  socket.emit('submit_face_samples', { name, samples });
});

saveIpCamBtn.addEventListener('click', () => {
  const url = ipUrlInput.value.trim();
  if (!url) return alert('Informe a URL da câmera.');
  // register camera with fixed id for demo; extend to multiple
  socket.emit('register_camera', { camera_id: lastIpCamId, url });
});

socket.on('submit_result', (data) => {
  alert(data.msg || (data.ok ? 'Sucesso.' : 'Falha.'));
});

socket.on('camera_registered', (data) => {
  alert('Câmera salva: ' + data.camera_id);
});

// init
startLocalCamera();
toggleIpGroup();
