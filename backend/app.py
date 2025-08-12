import os
import re
import json
import base64
import subprocess
from pathlib import Path
from threading import Lock
from typing import Dict, Any, List
from urllib.parse import quote

from flask import Flask, request, send_from_directory, redirect, session, jsonify
from flask_socketio import SocketIO, emit
import mysql.connector
from mysql.connector import Error, errorcode
from werkzeug.security import generate_password_hash, check_password_hash

# ----------------- Config -----------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
FRONTEND_DIR = BASE_DIR / 'frontend'
DATA_DIR.mkdir(parents=True, exist_ok=True)
(FRONTEND_DIR / 'static').mkdir(parents=True, exist_ok=True)
(DATA_DIR / 'faces').mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(FRONTEND_DIR / 'static'))
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

socketio = SocketIO(app, cors_allowed_origins='*', async_mode='eventlet')

processes_lock = Lock()
processing_procs: Dict[str, subprocess.Popen] = {}

DB_CONFIG = {
    'user': 'ari',
    'password': 'ari',
    'host': '127.0.0.1',
    'port': '3307',
    'database': 'privateafter_db'  # não usar raise_on_warnings aqui
}

# ----------------- Helpers -----------------
def slugify_filename(name: str) -> str:
    base = re.sub(r'[^a-zA-Z0-9._-]+', '_', name.strip())
    return base.strip('_') or 'face'

def current_user_id():
    return session.get('user_id')

def require_auth_socketio() -> bool:
    if not current_user_id():
        emit('auth_error', {'error': 'not_authenticated'})
        return False
    return True

def ensure_schema():
    # Conecta suprimindo warnings
    try:
        conn = mysql.connector.connect(**dict(DB_CONFIG, raise_on_warnings=False))
    except Error:
        cfg = dict(DB_CONFIG); cfg.pop('database', None)
        cfg['raise_on_warnings'] = False
        conn0 = mysql.connector.connect(**cfg)
        cur0 = conn0.cursor()
        cur0.execute("CREATE DATABASE IF NOT EXISTS privateafter_db")
        conn0.commit(); cur0.close(); conn0.close()
        conn = mysql.connector.connect(**dict(DB_CONFIG, raise_on_warnings=False))

    cur = conn.cursor()
    try:
        cur.execute("SET sql_notes = 0")
    except Exception:
        pass

    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(191) NOT NULL,
        email VARCHAR(191) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS encodings (
        name VARCHAR(191) PRIMARY KEY,
        encoding JSON NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")

    cur.execute("SHOW COLUMNS FROM encodings LIKE 'owner_user_id'")
    if not cur.fetchone():
        try:
            cur.execute("ALTER TABLE encodings ADD COLUMN owner_user_id INT NULL")
        except Error:
            pass

    cur.execute("SHOW COLUMNS FROM encodings LIKE 'photo_filename'")
    if not cur.fetchone():
        try:
            cur.execute("ALTER TABLE encodings ADD COLUMN photo_filename VARCHAR(255) NULL")
        except Error:
            pass

    # Tentar adicionar a FK (ignora se já existir)
    try:
        cur.execute("""ALTER TABLE encodings
                       ADD CONSTRAINT fk_enc_user
                       FOREIGN KEY (owner_user_id) REFERENCES users(id)
                       ON DELETE SET NULL""")
    except Error:
        pass

    cur.execute("""CREATE TABLE IF NOT EXISTS cameras (
        camera_id VARCHAR(191) PRIMARY KEY,
        url TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")

    conn.commit()
    try:
        cur.execute("SET sql_notes = 1")
    except Exception:
        pass
    cur.close(); conn.close()

def get_user_by_email(email: str):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        c = conn.cursor(dictionary=True)
        c.execute("SELECT id, name, email, password_hash FROM users WHERE email=%s", (email,))
        row = c.fetchone()
        c.close(); conn.close()
        return row
    except Error as e:
        print("get_user_by_email error:", e)
        return None

def get_user_by_id(user_id: int):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        c = conn.cursor(dictionary=True)
        c.execute("SELECT id, name, email FROM users WHERE id=%s", (user_id,))
        row = c.fetchone()
        c.close(); conn.close()
        return row
    except Error as e:
        print("get_user_by_id error:", e)
        return None

def upsert_encoding(name: str, enc: List[float], owner_user_id=None, photo_filename=None):
    # Valida owner_user_id; se não existir, usa NULL
    valid_owner_id = None
    try:
        if owner_user_id is not None:
            oid = int(owner_user_id)
            if get_user_by_id(oid):
                valid_owner_id = oid
    except Exception:
        valid_owner_id = None

    def _exec(owner_id):
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor()
        enc_json = json.dumps(enc)
        cur.execute("""
            INSERT INTO encodings (name, encoding, owner_user_id, photo_filename)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              encoding = VALUES(encoding),
              owner_user_id = VALUES(owner_user_id),
              photo_filename = VALUES(photo_filename)
        """, (name, enc_json, owner_id, photo_filename))
        conn.commit()
        cur.close(); conn.close()

    try:
        _exec(valid_owner_id)
    except Error as e:
        # Se falhar por FK, tenta novamente com NULL
        if getattr(e, 'errno', None) == errorcode.ER_NO_REFERENCED_ROW_2:
            try:
                _exec(None)
            except Exception as e2:
                print("upsert_encoding fallback error:", e2)
        else:
            print("upsert_encoding error:", e)

def load_encodings() -> Dict[str, List[float]]:
    encodings: Dict[str, List[float]] = {}
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        c = conn.cursor()
        c.execute("SELECT name, encoding FROM encodings")
        for name, enc_json in c:
            try:
                enc_list = json.loads(enc_json)
                encodings[name] = enc_list
            except Exception:
                pass
        c.close(); conn.close()
    except Error as e:
        print("load_encodings error:", e)
    return encodings

def load_cameras() -> Dict[str, str]:
    cams: Dict[str, str] = {}
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        c = conn.cursor()
        c.execute("SELECT camera_id, url FROM cameras")
        for cid, url in c:
            cams[cid] = url
        c.close(); conn.close()
    except Error as e:
        print("load_cameras error:", e)
    return cams

def save_camera(camera_id: str, url: str):
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO cameras (camera_id, url) VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE url = VALUES(url)
        """, (camera_id, url))
        conn.commit()
        cur.close(); conn.close()
    except Error as e:
        print("save_camera error:", e)

# ----------------- Routes (API) -----------------
@app.route('/api/signup', methods=['POST'])
def api_signup():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    if not name or not email or not password:
        return jsonify({'ok': False, 'msg': 'Nome, e-mail e senha são obrigatórios.'}), 400
    if get_user_by_email(email):
        return jsonify({'ok': False, 'msg': 'E-mail já cadastrado.'}), 409
    pwd_hash = generate_password_hash(password)
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)", (name, email, pwd_hash))
        conn.commit()
        user_id = cur.lastrowid
        cur.close(); conn.close()
        session['user_id'] = user_id
        return jsonify({'ok': True, 'user': {'id': user_id, 'name': name, 'email': email}})
    except Error as e:
        print("signup error:", e)
        return jsonify({'ok': False, 'msg': 'Erro ao cadastrar.'}), 500

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    user = get_user_by_email(email)
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'ok': False, 'msg': 'Credenciais inválidas.'}), 401
    session['user_id'] = user['id']
    return jsonify({'ok': True, 'user': {'id': user['id'], 'name': user['name'], 'email': user['email']}})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.pop('user_id', None)
    return jsonify({'ok': True})

@app.route('/api/me', methods=['GET'])
def api_me():
    uid = current_user_id()
    if not uid:
        return jsonify({'ok': False, 'msg': 'Not authenticated'}), 401
    
    user = get_user_by_id(uid)
    if not user:
        # Se o usuário não existir mais, limpar a sessão
        session.pop('user_id', None)
        return jsonify({'ok': False, 'msg': 'User not found'}), 401
    
    return jsonify({'ok': True, 'user': user})

@app.route('/api/faces', methods=['GET'])
def api_faces():
    if not current_user_id():
        return jsonify({'ok': False, 'msg': 'Não autenticado.'}), 401
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        c = conn.cursor()
        c.execute("SELECT name, photo_filename FROM encodings ORDER BY name ASC")
        faces = []
        for name, photo_filename in c:
            photo_url = f"/faces/{photo_filename}" if photo_filename else None
            faces.append({'name': name, 'photo_url': photo_url})
        c.close(); conn.close()
        return jsonify({'ok': True, 'faces': faces})
    except Error as e:
        print("faces list error:", e)
        return jsonify({'ok': False, 'faces': []}), 500

@app.route('/faces/<path:filename>')
def faces_file(filename):
    faces_dir = DATA_DIR / 'faces'
    faces_dir.mkdir(parents=True, exist_ok=True)
    return send_from_directory(str(faces_dir), filename)

# ----------------- Routes (HTML) -----------------
@app.route('/')
def index():
    return send_from_directory(str(FRONTEND_DIR), 'index.html')

@app.route('/cadastro')
def cadastro():
    if not current_user_id():
        return redirect(f"/login.html?next={quote('/cadastro')}", code=302)
    return send_from_directory(str(FRONTEND_DIR), 'cadastro.html')

@app.route('/reconhecimento')
def reconhecimento_page():
    if not current_user_id():
        return redirect(f"/login.html?next={quote('/reconhecimento')}", code=302)
    return send_from_directory(str(FRONTEND_DIR), 'reconhecimento.html')

# Redirecionar rotas curtas para as páginas completas
@app.route('/login')
def login_redirect():
    return redirect('/login.html', code=302)

@app.route('/registro')
def registro_redirect():
    return redirect('/registro.html', code=302)

# Servir as páginas HTML de login e registro
@app.route('/login.html')
def serve_login():
    return send_from_directory(str(FRONTEND_DIR), 'login.html')

@app.route('/registro.html')
def serve_registro():
    return send_from_directory(str(FRONTEND_DIR), 'registro.html')

@app.route('/static/<path:path>')
def static_files(path):
    return send_from_directory(str(FRONTEND_DIR / 'static'), path)

@app.route('/api/faces/<string:face_name>', methods=['DELETE'])
def api_delete_face(face_name):
    if not current_user_id():
        return jsonify({'ok': False, 'msg': 'Não autenticado.'}), 401
    
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor()
        
        # Buscar o rosto antes de deletar para pegar o nome do arquivo da foto
        cur.execute("SELECT photo_filename FROM encodings WHERE name = %s", (face_name,))
        result = cur.fetchone()
        
        if not result:
            cur.close(); conn.close()
            return jsonify({'ok': False, 'msg': 'Rosto não encontrado.'}), 404
        
        photo_filename = result[0]
        
        # Deletar do banco
        cur.execute("DELETE FROM encodings WHERE name = %s", (face_name,))
        deleted_rows = cur.rowcount
        conn.commit()
        cur.close(); conn.close()
        
        if deleted_rows == 0:
            return jsonify({'ok': False, 'msg': 'Rosto não encontrado.'}), 404
        
        # Tentar deletar o arquivo de foto se existir
        if photo_filename:
            try:
                photo_path = DATA_DIR / 'faces' / photo_filename
                if photo_path.exists():
                    photo_path.unlink()
            except Exception as e:
                print(f"Erro ao deletar foto {photo_filename}: {e}")
        
        return jsonify({'ok': True, 'msg': f'Rosto "{face_name}" excluído com sucesso.'})
        
    except Error as e:
        print("delete face error:", e)
        return jsonify({'ok': False, 'msg': 'Erro ao excluir rosto.'}), 500


# ----------------- SocketIO -----------------
@socketio.on('connect')
def on_connect():
    emit('server_info', {'status': 'connected', 'authenticated': bool(current_user_id())})

@socketio.on('register_camera')
def on_register_camera(data):
    if not require_auth_socketio():
        return
    camera_id = (data.get('camera_id') or '').strip()
    url = (data.get('url') or '').strip()
    if not camera_id or not url:
        emit('camera_result', {'ok': False, 'msg': 'camera_id e url são obrigatórios.'})
        return
    save_camera(camera_id, url)
    emit('camera_result', {'ok': True})

@socketio.on('submit_face_samples')
def on_submit_face_samples(data):
    if not require_auth_socketio():
        return
    # data: {name, samples: [dataURL,...]}
    name = (data.get('name') or '').strip()
    samples: List[str] = data.get('samples') or []
    if not name or not samples:
        emit('submit_result', {'ok': False, 'msg': 'Nome e amostras são obrigatórios.'})
        return

    try:
        import cv2
        import numpy as np
        import face_recognition
    except Exception as e:
        emit('submit_result', {'ok': False, 'msg': f'Dependências não disponíveis: {e}'})
        return

    encs = []
    first_image_bytes = None
    for durl in samples:
        try:
            header, b64 = durl.split(',', 1)
            img_bytes = base64.b64decode(b64)
            if first_image_bytes is None:
                first_image_bytes = img_bytes
            np_arr = np.frombuffer(img_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is None:
                continue
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

    import numpy as np
    enc_avg = np.mean(np.array(encs), axis=0).tolist()

    # Salvar foto representativa
    faces_dir = DATA_DIR / 'faces'
    faces_dir.mkdir(parents=True, exist_ok=True)
    safe_name = slugify_filename(name)
    photo_filename = f"{safe_name}.jpg"
    try:
        if first_image_bytes:
            with open(faces_dir / photo_filename, 'wb') as f:
                f.write(first_image_bytes)
    except Exception as e:
        print("Erro ao salvar foto:", e)
        photo_filename = None

    # Salvar encoding + metadados do dono (validação já acontece em upsert_encoding)
    upsert_encoding(name, enc_avg, owner_user_id=current_user_id(), photo_filename=photo_filename)
    emit('submit_result', {'ok': True, 'msg': f'Cadastro salvo para {name}.', 'count': len(encs)})

@socketio.on('client_frame')
def on_client_frame(data):
    if not require_auth_socketio():
        return
    durl = data.get('dataURL')
    if not durl:
        return
    try:
        import cv2
        import numpy as np
        try:
            import face_recognition
            have_fr = True
        except Exception:
            have_fr = False

        header, b64 = durl.split(',', 1)
        img_bytes = base64.b64decode(b64)
        np_arr = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = frame.shape[:2]

        results = []
        if have_fr:
            boxes = face_recognition.face_locations(rgb, model='hog')
            if boxes:
                known = load_encodings()
                names = list(known.keys())
                vecs = [known[n] for n in names]
                for (top, right, bottom, left) in boxes:
                    enc = face_recognition.face_encodings(rgb, [(top, right, bottom, left)])[0]
                    label = 'Desconhecido'
                    if names:
                        import numpy as np
                        dists = face_recognition.face_distance(np.array(vecs), enc)
                        if len(dists) > 0:
                            idx = int(dists.argmin())
                            if float(dists[idx]) < 0.6:
                                label = names[idx]
                    x, y = left, top
                    results.append({'name': label, 'box': [x, y, right - left, bottom - top]})
        else:
            # Fallback com Haar frontal (sem reconhecimento)
            cv2_base_dir = os.path.dirname(os.path.abspath(cv2.__file__))
            cascade_path = os.path.join(cv2_base_dir, 'data', 'haarcascade_frontalface_default.xml')
            face_cascade = cv2.CascadeClassifier(cascade_path)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            detections = face_cascade.detectMultiScale(gray, 1.3, 5)
            for (x, y, ww, hh) in detections:
                results.append({'name': 'Desconhecido', 'box': [int(x), int(y), int(ww), int(hh)]})

        emit('recognition_update', {'camera_id': 'main', 'results': results, 'frame_w': w, 'frame_h': h})
    except Exception as e:
        print('client_frame error:', e)

@socketio.on('enable_multicam')
def on_enable_multicam(data):
    if not require_auth_socketio():
        return
    # placeholder apenas para compatibilidade
    emit('multicam_status', {'ok': True, 'cameras': load_cameras()})

@socketio.on('disable_multicam')
def on_disable_multicam():
    if not require_auth_socketio():
        return
    emit('multicam_status', {'ok': True, 'cameras': {}})

@socketio.on('node_result')
def on_node_result(data):
    # reservado para nós externos
    pass

# ----------------- Main -----------------
if __name__ == '__main__':
    ensure_schema()
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', '5000'))
    socketio.run(app, host=host, port=port)