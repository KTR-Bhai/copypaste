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

# SQLite database path (Render uses /data, local uses current directory)
DB_PATH = os.environ.get("DB_PATH", "text_storage.db")

def init_db():
    """Initialize SQLite database with texts table"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                text TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_code ON texts (code)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON texts (created_at)")
        conn.commit()
        logger.info("Database initialized with indexes")
    except Exception as e:
        logger.error(f"Database init error: {str(e)}", exc_info=True)
        conn.rollback()
    finally:
        conn.close()

# Run database initialization
init_db()

def generate_code():
    """Generate a unique 3-digit code"""
    digits = '0123456789'
    max_attempts = 10
    for _ in range(max_attempts):
        code = ''.join(random.choice(digits) for _ in range(3))
        conn = sqlite3.connect(DB_PATH)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT code FROM texts WHERE code = ?", (code,))
            if not cursor.fetchone():
                return code
        except Exception as e:
            logger.error(f"Code generation error: {str(e)}", exc_info=True)
        finally:
            conn.close()
    raise Exception("Failed to generate unique code")

def cleanup_expired_texts():
    """Remove texts and codes older than 24 hours"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        current_time = int(time.time())
        cursor.execute("DELETE FROM texts WHERE created_at < ?", (current_time - 86400,))
        deleted_count = cursor.rowcount
        conn.commit()
        logger.info(f"Expired {deleted_count} texts and codes")
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}", exc_info=True)
        conn.rollback()
    finally:
        conn.close()

def scheduled_cleanup():
    """Run cleanup every hour"""
    while True:
        cleanup_expired_texts()
        time.sleep(3600)  # 1 hour

# Start cleanup thread
cleanup_thread = threading.Thread(target=scheduled_cleanup, daemon=True)
cleanup_thread.start()

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
    """Create a new text entry with a unique code"""
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text'].strip()
    if not text:
        return jsonify({'error': 'Text cannot be empty'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    try:
        code = generate_code()
        current_time = int(time.time())
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO texts (code, text, created_at) VALUES (?, ?, ?)",
            (code, text, current_time)
        )
        conn.commit()
        logger.info(f"Text created with code: {code}")
        return jsonify({'code': code}), 201
    except Exception as e:
        logger.error(f"Error in /api/create: {str(e)}", exc_info=True)
        conn.rollback()
        return jsonify({'error': 'Server error'}), 500
    finally:
        conn.close()

@app.route('/api/retrieve/<code>', methods=['GET'])
def retrieve_text(code):
    """Retrieve text by code, expiring if over 24 hours"""
    if not code or len(code) != 3 or not code.isdigit():
        return jsonify({'error': 'Invalid code format (must be 3 digits)'}), 400
    
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT text, created_at FROM texts WHERE code = ?", (code,))
        result = cursor.fetchone()
        
        if not result:
            return jsonify({'error': 'Invalid code or text expired'}), 404
        
        text, created_at = result
        current_time = int(time.time())
        
        if current_time - created_at > 86400:
            cursor.execute("DELETE FROM texts WHERE code = ?", (code,))
            conn.commit()
            logger.info(f"Code {code} expired and deleted")
            return jsonify({'error': 'Text has expired'}), 404
        
        logger.info(f"Text retrieved for code: {code}")
        return jsonify({'text': text}), 200
    except Exception as e:
        logger.error(f"Error in /api/retrieve: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500
    finally:
        conn.close()

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)