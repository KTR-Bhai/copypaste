from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import random
import string
import time
import os
from datetime import datetime, timedelta
import threading

app = Flask(__name__)

# Database setup
DB_PATH = 'text_storage.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS texts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        text TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    ''')
    conn.commit()
    conn.close()

init_db()

# Helper functions
def generate_code():
    """Generate a unique 5-character code"""
    characters = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choice(characters) for _ in range(5))
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT code FROM texts WHERE code = ?', (code,))
        result = cursor.fetchone()
        conn.close()
        if not result:
            return code

def cleanup_expired_texts():
    """Remove texts older than 24 hours"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    current_time = int(time.time())
    cursor.execute('DELETE FROM texts WHERE created_at < ?', (current_time - 86400,))
    conn.commit()
    conn.close()

def scheduled_cleanup():
    """Run cleanup every hour"""
    while True:
        cleanup_expired_texts()
        time.sleep(3600)  # Sleep for 1 hour

# Start cleanup thread
cleanup_thread = threading.Thread(target=scheduled_cleanup, daemon=True)
cleanup_thread.start()

# API Routes
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/api/create', methods=['POST'])
def create_text():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text'].strip()
    if not text:
        return jsonify({'error': 'Text cannot be empty'}), 400
    
    try:
        code = generate_code()
        current_time = int(time.time())
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO texts (code, text, created_at) VALUES (?, ?, ?)',
                      (code, text, current_time))
        conn.commit()
        conn.close()
        return jsonify({'code': code}), 201
    except Exception as e:
        return jsonify({'error': 'Server error'}), 500

@app.route('/api/retrieve/<code>', methods=['GET'])
def retrieve_text(code):
    if not code or len(code) != 5:
        return jsonify({'error': 'Invalid code format'}), 400
    
    code = code.upper()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT text, created_at FROM texts WHERE code = ?', (code,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return jsonify({'error': 'Invalid code or text expired'}), 404
        
        text, created_at = result
        current_time = int(time.time())
        
        if current_time - created_at > 86400:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM texts WHERE code = ?', (code,))
            conn.commit()
            conn.close()
            return jsonify({'error': 'Text has expired'}), 404
        
        return jsonify({'text': text}), 200
    except Exception as e:
        return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')
    app.run(debug=True, host='0.0.0.0', port=5000)