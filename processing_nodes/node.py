import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import socketio
import face_recognition

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
ENCODINGS_FILE = DATA_DIR / 'encodings.pkl'

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

sio = socketio.Client()

def load_known():
    known = {}
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT name, encoding FROM encodings")
        for name, enc_json in cursor:
            known[name] = json.loads(enc_json)
        cursor.close()
        conn.close()
    except Error as e:
        print(f"Erro ao carregar known: {e}")
    return known

@sio.event
def connect():
    print('Node connected to server')

@sio.event
def disconnect():
    print('Node disconnected from server')

def open_capture(camera_url: str | None):
    if camera_url:
        return cv2.VideoCapture(camera_url)
    # default webcam
    return cv2.VideoCapture(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera_id', required=True)
    parser.add_argument('--camera_url', default=None)
    parser.add_argument('--server_url', default='http://localhost:5000')
    parser.add_argument('--send_frame', action='store_true')
    args = parser.parse_args()

    known_dict = load_known()
    known_names = list(known_dict.keys())
    known_encs = [np.array(known_dict[n]) for n in known_names]

    sio.connect(args.server_url, transports=['websocket', 'polling'])

    cap = open_capture(args.camera_url)
    if not cap.isOpened():
        print('Failed to open camera', args.camera_url)
        return

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            boxes = face_recognition.face_locations(rgb, model='hog')
            encs = face_recognition.face_encodings(rgb, boxes)

            results = []
            for box, enc in zip(boxes, encs):
                name = 'Desconhecido'
                if known_encs:
                    dists = face_recognition.face_distance(known_encs, enc)
                    idx = int(np.argmin(dists))
                    if dists[idx] < 0.5:
                        name = known_names[idx]
                top, right, bottom, left = box
                # Scale back to original frame size
                x = int(left * 2)
                y = int(top * 2)
                w = int((right - left) * 2)
                h = int((bottom - top) * 2)
                results.append({'name': name, 'box': [x, y, w, h]})

            payload = {
                'camera_id': args.camera_id,
                'results': results,
            }

            if args.send_frame:
                _, jpg = cv2.imencode('.jpg', frame)
                b64 = base64.b64encode(jpg.tobytes()).decode('ascii')
                payload['frame_b64'] = 'data:image/jpeg;base64,' + b64

            sio.emit('node_result', payload)
            # modest frame rate
            time.sleep(0.05)
    finally:
        cap.release()
        sio.disconnect()

if __name__ == '__main__':
    main()