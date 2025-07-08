from flask import Flask, request, jsonify, send_from_directory
import psycopg2
from psycopg2 import pool
import random
import time
import os
import logging
from contextlib import contextmanager
import urllib.parse as urlparse
from dotenv import load_dotenv

# Load .env for local dev (ignored on Render if not present)
load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db_pool = None
DB_PARAMS = None

def initialize_database():
    """Initialize database connection lazily - only when needed"""
    global db_pool, DB_PARAMS

    if db_pool is not None:
        return db_pool

    logger.info("Initializing database connection...")

    DB_URL = os.environ.get("DATABASE_URL")
    logger.info(f"DATABASE_URL from env: {DB_URL}")

    if not DB_URL or not DB_URL.startswith("postgresql://"):
        logger.error("DATABASE_URL not set or invalid format")
        raise ValueError("DATABASE_URL environment variable not set or invalid")

    try:
        url = urlparse.urlparse(DB_URL)
        if not all([url.scheme, url.username, url.password, url.hostname, url.path]):
            raise ValueError("Invalid DATABASE_URL format: missing required components")

        dbname = url.path.lstrip("/")
        if not dbname:
            raise ValueError("Invalid DATABASE_URL format: missing database name")

        query_params = urlparse.parse_qs(url.query)
        sslmode = query_params.get("sslmode", ["require"])[0]

        DB_PARAMS = {
            "dbname": dbname,
            "user": url.username,
            "password": url.password,
            "host": url.hostname,
            "port": url.port or 5432,
            "sslmode": sslmode,
            "connect_timeout": 10
        }

        logger.info(f"Parsed DB params: host={url.hostname}, db={dbname}, sslmode={sslmode}")
    except Exception as e:
        logger.error(f"Failed to parse DATABASE_URL: {str(e)}")
        raise

    max_retries = 5
    for attempt in range(max_retries):
        try:
            logger.info(f"DB connection attempt {attempt + 1}/{max_retries}")
            db_pool = psycopg2.pool.SimpleConnectionPool(1, 5, **DB_PARAMS)
            logger.info("Database connection pool initialized")
            break
        except psycopg2.OperationalError as e:
            logger.error(f"DB connection failed: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    return db_pool

@contextmanager
def get_db_connection():
    pool = initialize_database()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)

def init_db():
    logger.info("Initializing database tables...")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS texts (
                    id SERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_code ON texts (code)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON texts (created_at)")
            conn.commit()
            logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database init error: {str(e)}", exc_info=True)
        raise

def generate_code():
    digits = '0123456789'
    max_attempts = 10
    for _ in range(max_attempts):
        code = ''.join(random.choice(digits) for _ in range(3))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT code FROM texts WHERE code = %s", (code,))
            if not cursor.fetchone():
                return code
    raise Exception("Failed to generate unique code")

LAST_CLEANUP = 0
CLEANUP_INTERVAL = 300  # 5 minutes

def cleanup_expired_texts(conn):
    global LAST_CLEANUP
    current_time = int(time.time())
    if current_time - LAST_CLEANUP < CLEANUP_INTERVAL:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM texts WHERE created_at < %s", (current_time - 86400,))
        deleted = cursor.rowcount
        conn.commit()
        if deleted > 0:
            logger.info(f"Deleted {deleted} expired texts")
        LAST_CLEANUP = current_time
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}", exc_info=True)
        conn.rollback()

@app.after_request
def add_no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    try:
        return send_from_directory('static', 'index.html')
    except Exception as e:
        logger.error(f"Error serving index.html: {str(e)}")
        return jsonify({'error': 'Server error'}), 500

@app.route('/health')
def health_check():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return jsonify({'status': 'healthy', 'database': 'connected'}), 200
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/api/create', methods=['POST'])
def create_text():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400

    text = data['text'].strip()
    if not text:
        return jsonify({'error': 'Text cannot be empty'}), 400

    try:
        init_db()
        with get_db_connection() as conn:
            cleanup_expired_texts(conn)
            code = generate_code()
            current_time = int(time.time())
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO texts (code, text, created_at) VALUES (%s, %s, %s)",
                (code, text, current_time)
            )
            conn.commit()
            logger.info(f"Text created with code: {code}")
            return jsonify({'code': code}), 201
    except Exception as e:
        logger.error(f"Error in /api/create: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/api/retrieve/<code>', methods=['GET'])
def retrieve_text(code):
    if not code or len(code) != 3 or not code.isdigit():
        return jsonify({'error': 'Invalid code format (must be 3 digits)'}), 400

    try:
        with get_db_connection() as conn:
            cleanup_expired_texts(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT text, created_at FROM texts WHERE code = %s", (code,))
            result = cursor.fetchone()
            if not result:
                return jsonify({'error': 'Invalid code or text expired'}), 404

            text, created_at = result
            if int(time.time()) - created_at > 86400:
                cursor.execute("DELETE FROM texts WHERE code = %s", (code,))
                conn.commit()
                logger.info(f"Code {code} expired and deleted")
                return jsonify({'error': 'Text has expired'}), 404

            logger.info(f"Text retrieved for code: {code}")
            return jsonify({'text': text}), 200
    except Exception as e:
        logger.error(f"Error in /api/retrieve: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
