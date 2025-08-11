import os
import json
import base64
import pickle
import subprocess
import sys
from pathlib import Path
from threading import Lock
from typing import Dict, Any, List

from flask import Flask, render_template_string, request, send_from_directory
from flask_socketio import SocketIO, emit

# Config
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
ENCODINGS_FILE = DATA_DIR / 'encodings.pkl'
CAMERAS_FILE = DATA_DIR / 'cameras.json'
FRONTEND_DIR = BASE_DIR / 'frontend'
PROCESSING_DIR = BASE_DIR / 'processing_nodes'


app = Flask(__name__, static_folder=str(FRONTEND_DIR / 'static'))
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

processes_lock = Lock()
processing_procs: Dict[str, subprocess.Popen] = {}

# ----------------- Helpers -----------------

import mysql.connector
from mysql.connector import Error
import json

DB_CONFIG = {
    'user': 'ari',
    'password': 'ari',
    'host': 'localhost',
    'port': '3307',  # Porta mapeada no docker-compose.yml
    'database': 'privateafter_db',
    'raise_on_warnings': True
}

def load_encodings() -> Dict[str, List[float]]:
    encodings = {}
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT name, encoding FROM encodings")
        for name, enc_json in cursor:
            encodings[name] = json.loads(enc_json)
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Erro ao carregar encodings: {e}")
    return encodings

def save_encodings(encodings: Dict[str, List[float]]):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        for name, enc in encodings.items():
            enc_json = json.dumps(enc)
            cursor.execute("""
                INSERT INTO encodings (name, encoding)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE encoding = %s
            """, (name, enc_json, enc_json))
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Erro ao salvar encodings: {e}")

def load_cameras() -> Dict[str, str]:
    cameras = {}
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT camera_id, url FROM cameras")
        for cam_id, url in cursor:
            cameras[cam_id] = url
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Erro ao carregar cameras: {e}")
    return cameras

def save_cameras(cameras: Dict[str, str]):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        for cam_id, url in cameras.items():
            cursor.execute("""
                INSERT INTO cameras (camera_id, url)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE url = %s
            """, (cam_id, url, url))
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Erro ao salvar cameras: {e}")

# ----------------- Routes -----------------

@app.route('/')
def index():
    return send_from_directory(str(FRONTEND_DIR), 'index.html')


@app.route('/cadastro')
def cadastro():
    return send_from_directory(str(FRONTEND_DIR), 'cadastro.html')


@app.route('/reconhecimento')
def reconhecimento():
    return send_from_directory(str(FRONTEND_DIR), 'reconhecimento.html')


@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(str(FRONTEND_DIR / 'static'), path)


# ----------------- SocketIO -----------------

@socketio.on('connect')
def on_connect():
    emit('server_info', {'status': 'connected'})


@socketio.on('register_camera')
def on_register_camera(data):
    # data: {camera_id, url}
    cameras = load_cameras()
    cameras[data['camera_id']] = data.get('url', '')
    save_cameras(cameras)
    emit('camera_registered', {'camera_id': data['camera_id']})


@socketio.on('submit_face_samples')
def on_submit_face_samples(data):
    # data: {name, samples: [dataURL,...]}
    import cv2
    import numpy as np
    import face_recognition

    name = data['name'].strip()
    samples: List[str] = data.get('samples', [])

    known = load_encodings()
    if name in known:
        # append new samples (average)
        pass

    encs = []
    for durl in samples:
        try:
            header, b64 = durl.split(',')
            img_bytes = base64.b64decode(b64)
            np_arr = np.frombuffer(img_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            boxes = face_recognition.face_locations(rgb, model='hog')
            if not boxes:
                continue
            enc = face_recognition.face_encodings(rgb, boxes)[0]
            encs.append(enc.tolist())
        except Exception as e:
            print('encoding error:', e)

    if not encs:
        emit('submit_result', {'ok': False, 'msg': 'Nenhum rosto detectado nas amostras.'})
        return

    # average encoding for stability
    import numpy as np
    enc_avg = np.mean(np.array(encs), axis=0).tolist()
    known[name] = enc_avg
    save_encodings(known)
    emit('submit_result', {'ok': True, 'msg': f'Cadastro salvo para {name}.', 'count': len(encs)})


@socketio.on('client_frame')
def on_client_frame(data):
    """
    Recebe um frame do navegador (main camera) e emite recognition_update para camera_id 'main'.
    Usa face_recognition se disponível; caso contrário, faz detecção Haar (Desconhecido).
    data: { dataURL }
    """
    import cv2
    import numpy as np
    try:
        import face_recognition  # type: ignore
        has_fr = True
    except Exception:
        has_fr = False

    durl = data.get('dataURL')
    if not durl:
        return
    try:
        header, b64 = durl.split(',')
        img_bytes = base64.b64decode(b64)
        np_arr = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        results = []
        if has_fr:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            boxes = face_recognition.face_locations(rgb, model='hog')
            encs = face_recognition.face_encodings(rgb, boxes)
            known = load_encodings()
            names = list(known.keys())
            known_encs = [np.array(known[n]) for n in names]
            for box, enc in zip(boxes, encs):
                label = 'Desconhecido'
                if known_encs:
                    dists = face_recognition.face_distance(known_encs, enc)
                    idx = int(np.argmin(dists))
                    if dists[idx] < 0.6:
                        label = names[idx]
                top, right, bottom, left = box
                x = int(left)
                y = int(top)
                w = int(right - left)
                h = int(bottom - top)
                results.append({'name': label, 'box': [x, y, w, h]})
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
            for (x, y, w, h) in faces:
                results.append({'name': 'Desconhecido', 'box': [int(x), int(y), int(w), int(h)]})

        h, w = frame.shape[:2]
        emit('recognition_update', {'camera_id': 'main', 'results': results, 'frame_w': int(w), 'frame_h': int(h)})
    except Exception as e:
        print('client_frame error:', e)

@socketio.on('enable_multicam')
def on_enable_multicam(data):
    # data: none or {cameras?: {id:url}}
    cameras = data.get('cameras') or load_cameras()
    started = []
    with processes_lock:
        for cam_id, url in cameras.items():
            if cam_id in processing_procs:
                continue
            cmd = [sys.executable, str(PROCESSING_DIR / 'node.py'), '--camera_id', cam_id]
            if url:
                cmd += ['--camera_url', url]
            cmd += ['--server_url', request.host_url.rstrip('/')]
            print('Starting node:', cmd)
            proc = subprocess.Popen(cmd, cwd=str(PROCESSING_DIR))
            processing_procs[cam_id] = proc
            started.append(cam_id)
    emit('multicam_started', {'started': started})


@socketio.on('disable_multicam')
def on_disable_multicam():
    with processes_lock:
        for cam_id, proc in list(processing_procs.items()):
            try:
                proc.terminate()
            except Exception:
                pass
            processing_procs.pop(cam_id, None)
    emit('multicam_stopped', {'ok': True})


@socketio.on('node_result')
def on_node_result(data):
    # Relay to all clients: {camera_id, results:[{name, box:[x,y,w,h]}], frame_b64?}
    emit('recognition_update', data, broadcast=True)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    socketio.run(app, host='0.0.0.0', port=port)