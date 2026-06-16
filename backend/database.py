import psycopg2
from psycopg2.extras import RealDictCursor
from pathlib import Path
import os

from dotenv import load_dotenv
from passlib.context import CryptContext

# =========================
# Database configuration
# =========================

load_dotenv()

pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto"
)


# =========================
# Database connection
# =========================

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )
    return conn
# =========================
# Initialize database
# =========================
def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # ======================================================
    # USERS TABLE
    # ======================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,

            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,

            auth_provider TEXT NOT NULL DEFAULT 'local',
            google_sub TEXT UNIQUE,

            email_verified INTEGER NOT NULL DEFAULT 0,
            verification_token TEXT UNIQUE,
            verification_sent_at TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            role TEXT NOT NULL DEFAULT 'user',
            status TEXT NOT NULL DEFAULT 'active',

            sessions_invalidated_at TIMESTAMP
        )
    """)

    # ======================================================
    # MIGRATIONS (for DBs created before this column existed)

    # ======================================================
    # PDF FILES TABLE
    # ======================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdfs (
            id SERIAL PRIMARY KEY,

            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,

            upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ======================================================


    # ======================================================
    # AUTH EVENTS TABLE
    # ======================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_events (
            id SERIAL PRIMARY KEY,

            user_id INTEGER,
            email TEXT,

            ip TEXT,

            action TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 0,

            detail TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ======================================================
    # FEEDBACK TABLE
    # ======================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,

            user_id INTEGER,

            rating INTEGER,
            message TEXT NOT NULL,
            page TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # ======================================================
    # INDEXES
    # ======================================================
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_events_ip_time
        ON auth_events(ip, created_at)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_events_action_time
        ON auth_events(action, created_at)
    """)

    conn.commit()
    conn.close()

    print("✅ Clean database initialized successfully")


# =========================
# Create admin user
# =========================
def create_admin():
    conn = get_db()
    cursor = conn.cursor()

    admin_email = os.getenv("ADMIN_EMAILS")
    admin_password = os.getenv("ADMIN_PASSWORD")

    if not admin_email or not admin_password:
        print("⚠️ ADMIN_EMAILS or ADMIN_PASSWORD missing")
        return

    cursor.execute(
        "SELECT id FROM users WHERE email = %s",
        (admin_email,)
    )

    existing = cursor.fetchone()

    if existing:
        print("✅ Admin already exists")
        conn.close()
        return

    hashed_password = pwd_context.hash(admin_password)

    cursor.execute("""
        INSERT INTO users (
            email,
            password_hash,
            role,
            email_verified
        )
        VALUES (%s, %s, %s, %s)
    """, (
        admin_email,
        hashed_password,
        "admin",
        1
    ))

    conn.commit()
    conn.close()

    print("✅ Admin user created")


# =========================
# Main
# =========================
if __name__ == "__main__":
    init_db()
    create_admin()
