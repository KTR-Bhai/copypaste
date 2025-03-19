from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import random
import time
import os
import logging
import threading

# Setup Flask app and logging
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration
DB_PATH = os.environ.get('DB_PATH', 'text_storage.db')  # Use /data/text_storage.db on Render with Disk

def init_db():
    """Initialize SQLite database with texts table"""
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
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

# Run database initialization
init_db()

# Helper functions
def generate_code():
    """Generate a unique 3-digit code (only digits)"""
    digits = '0123456789'
    while True:
        code = ''.join(random.choice(digits) for _ in range(3))  # Changed to 3 digits
        conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT code FROM texts WHERE code = ?', (code,))
        result = cursor.fetchone()
        conn.close()
        if not result:
            return code

def cleanup_expired_texts():
    """Remove texts older than 24 hours"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        cursor = conn.cursor()
        current_time = int(time.time())
        cursor.execute('DELETE FROM texts WHERE created_at < ?', (current_time - 86400,))
        conn.commit()
        conn.close()
        logger.info("Expired texts cleaned up")
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}", exc_info=True)

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
    """Serve the index.html from static folder"""
    try:
        return send_from_directory('static', 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/api/create', methods=['POST'])
def create_text():
    """Create a new text entry and return a unique 3-digit code"""
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text'].strip()
    if not text:
        return jsonify({'error': 'Text cannot be empty'}), 400
    
    try:
        code = generate_code()
        current_time = int(time.time())
        conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO texts (code, text, created_at) VALUES (?, ?, ?)',
                      (code, text, current_time))
        conn.commit()
        conn.close()
        logger.info(f"Text created with code: {code}")
        return jsonify({'code': code}), 201
    except Exception as e:
        logger.error(f"Error in /api/create: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/api/retrieve/<code>', methods=['GET'])
def retrieve_text(code):
    """Retrieve text by code, checking expiration"""
    if not code or len(code) != 3:  # Changed to 3 digits
        return jsonify({'error': 'Invalid code format (must be 3 digits)'}), 400
    
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('SELECT text, created_at FROM texts WHERE code = ?', (code,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return jsonify({'error': 'Invalid code or text expired'}), 404
        
        text, created_at = result
        current_time = int(time.time())
        
        if current_time - created_at > 86400:
            conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM texts WHERE code = ?', (code,))
            conn.commit()
            conn.close()
            return jsonify({'error': 'Text has expired'}), 404
        
        logger.info(f"Text retrieved for code: {code}")
        return jsonify({'text': text}), 200
    except Exception as e:
        logger.error(f"Error in /api/retrieve: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    # Ensure static folder exists
    if not os.path.exists('static'):
        os.makedirs('static')
    # Use environment PORT for Render, default to 5000 locally
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)