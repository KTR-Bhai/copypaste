from flask import Flask, request, jsonify, send_from_directory, make_response
import psycopg2
from psycopg2 import pool
import random
import time
import os
import logging
from contextlib import contextmanager
import urllib.parse as urlparse

# Setup Flask app and logging
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get DATABASE_URL from environment (Render uses internal URL)
DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    DB_URL = os.environ.get("DATABASE_URL_LOCAL", None)
    if not DB_URL:
        logger.warning("DATABASE_URL not set. For local testing, set it (e.g., 'postgresql://user:password@host:port/dbname').")
        raise ValueError("DATABASE_URL not set. Set it in your environment or Render's 'teju' group.")

# Parse the DATABASE_URL and validate it
try:
    url = urlparse.urlparse(DB_URL)
    if not all([url.scheme, url.username, url.password, url.hostname, url.path]):
        raise ValueError("Invalid DATABASE_URL format. Missing required components (scheme, user, password, host, dbname).")
    port = url.port if url.port is not None else 5432  # Default to 5432 if port is missing
    DB_PARAMS = {
        "dbname": url.path[1:],  # Remove leading '/'
        "user": url.username,
        "password": url.password,
        "host": url.hostname,
        "port": port
    }
    logger.info(f"Parsed DATABASE_URL: host={url.hostname}, port={port}, dbname={DB_PARAMS['dbname']}")
except ValueError as e:
    logger.error(f"Failed to parse DATABASE_URL: {str(e)}")
    raise
except Exception as e:
    logger.error(f"Unexpected error parsing DATABASE_URL: {str(e)}")
    raise

# Connection pool for PostgreSQL with min 2 connections to reduce cold starts
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(2, 10, **DB_PARAMS)
    logger.info("Database connection pool initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize database pool: {str(e)}")
    raise

@contextmanager
def get_db_connection():
    """Reusable PostgreSQL connection from pool"""
    conn = db_pool.getconn()
    try:
        yield conn
    finally:
        db_pool.putconn(conn)

def init_db():
    """Initialize PostgreSQL database with texts table"""
    with get_db_connection() as conn:
        try:
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
            logger.info("Database initialized with indexes")
        except Exception as e:
            logger.error(f"Database init error: {str(e)}", exc_info=True)
            conn.rollback()

# Run database initialization
init_db()

def generate_code():
    """Generate a unique 3-digit code"""
    start_time = time.time()
    digits = '0123456789'
    max_attempts = 10
    for _ in range(max_attempts):
        code = ''.join(random.choice(digits) for _ in range(3))
        with get_db_connection() as conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT code FROM texts WHERE code = %s", (code,))
                if not cursor.fetchone():
                    logger.info(f"Code generation took {time.time() - start_time:.2f} seconds")
                    return code
            except Exception as e:
                logger.error(f"Code generation error: {str(e)}", exc_info=True)
    raise Exception("Failed to generate unique code after max attempts")

# Global variable to track last cleanup time
LAST_CLEANUP = 0
CLEANUP_INTERVAL = 300  # 5 minutes in seconds

def cleanup_expired_texts(conn):
    """Remove texts older than 24 hours, run only every 5 minutes"""
    global LAST_CLEANUP
    start_time = time.time()
    current_time = int(time.time())
    if current_time - LAST_CLEANUP < CLEANUP_INTERVAL:
        return  # Skip if too soon
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM texts WHERE created_at < %s", (current_time - 86400,))
        deleted_count = cursor.rowcount
        conn.commit()
        if deleted_count > 0:
            logger.info(f"Expired {deleted_count} texts during cleanup")
        LAST_CLEANUP = current_time
        logger.info(f"Cleanup took {time.time() - start_time:.2f} seconds")
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}", exc_info=True)
        conn.rollback()

# Prevent caching for dynamic routes
@app.after_request
def add_no_cache(response):
    """Add no-cache headers to all responses"""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    """Serve the index.html from static folder with no caching"""
    start_time = time.time()
    try:
        response = make_response(send_from_directory('static', 'index.html'))
        logger.info(f"Serving index.html took {time.time() - start_time:.2f} seconds")
        return response
    except Exception as e:
        logger.error(f"Error serving index.html: {str(e)}", exc_info=True)
        return jsonify({'error': 'Server error'}), 500

@app.route('/api/create', methods=['POST'])
def create_text():
    """Create a new text entry with a unique code"""
    start_time = time.time()
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'No text provided'}), 400
    
    text = data['text'].strip()
    if not text:
        return jsonify({'error': 'Text cannot be empty'}), 400
    
    with get_db_connection() as conn:
        try:
            cleanup_expired_texts(conn)  # Runs only if 5 minutes have passed
            code = generate_code()
            current_time = int(time.time())
            cursor = conn.cursor()
            insert_start = time.time()
            cursor.execute(
                "INSERT INTO texts (code, text, created_at) VALUES (%s, %s, %s)",
                (code, text, current_time)
            )
            conn.commit()
            logger.info(f"Insert took {time.time() - insert_start:.2f} seconds")
            logger.info(f"Text created with code: {code}")
            logger.info(f"Total /api/create took {time.time() - start_time:.2f} seconds")
            return jsonify({'code': code}), 201
        except Exception as e:
            logger.error(f"Error in /api/create: {str(e)}", exc_info=True)
            conn.rollback()
            return jsonify({'error': 'Server error'}), 500

@app.route('/api/retrieve/<code>', methods=['GET'])
def retrieve_text(code):
    """Retrieve text by code, expiring if over 24 hours"""
    start_time = time.time()
    if not code or len(code) != 3 or not code.isdigit():
        return jsonify({'error': 'Invalid code format (must be 3 digits)'}), 400
    
    with get_db_connection() as conn:
        try:
            cleanup_expired_texts(conn)  # Runs only if 5 minutes have passed
            cursor = conn.cursor()
            select_start = time.time()
            cursor.execute("SELECT text, created_at FROM texts WHERE code = %s", (code,))
            result = cursor.fetchone()
            logger.info(f"Select took {time.time() - select_start:.2f} seconds")
            
            if not result:
                return jsonify({'error': 'Invalid code or text expired'}), 404
            
            text, created_at = result
            current_time = int(time.time())
            
            if current_time - created_at > 86400:
                delete_start = time.time()
                cursor.execute("DELETE FROM texts WHERE code = %s", (code,))
                conn.commit()
                logger.info(f"Delete took {time.time() - delete_start:.2f} seconds")
                logger.info(f"Code {code} expired and deleted")
                return jsonify({'error': 'Text has expired'}), 404
            
            logger.info(f"Text retrieved for code: {code}")
            logger.info(f"Total /api/retrieve took {time.time() - start_time:.2f} seconds")
            return jsonify({'text': text}), 200
        except Exception as e:
            logger.error(f"Error in /api/retrieve: {str(e)}", exc_info=True)
            return jsonify({'error': 'Server error'}), 500

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)