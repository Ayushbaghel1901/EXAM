import os
import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

# ── Connection string ──────────────────────────────────────────────────────────
# Set DATABASE_URL in your .env file, e.g.:
#   DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL")



import urllib.parse

def get_connection():
    """
    Returns a psycopg2 connection using RealDictCursor.
    Uses explicit parameters to avoid issues with special characters in URL.
    """
    url = urllib.parse.urlparse(DATABASE_URL)
    username = url.username
    password = urllib.parse.unquote(url.password) if url.password else None
    database = url.path[1:]
    hostname = url.hostname
    port = url.port

    conn = psycopg2.connect(
        dbname=database,
        user=username,
        password=password,
        host=hostname,
        port=port,
        cursor_factory=psycopg2.extras.RealDictCursor,
        sslmode=os.environ.get("DB_SSLMODE", "require")
    )
    return conn


def init_db():
    """
    Creates all tables on first run (idempotent).
    In production with Supabase the tables already exist via migrations,
    so this is mainly a safety net for local development.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id        SERIAL PRIMARY KEY,
            username  TEXT UNIQUE NOT NULL,
            password  TEXT NOT NULL,
            email     TEXT UNIQUE,
            role      TEXT NOT NULL CHECK (role IN ('student','faculty','admin'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS faculty_details (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            faculty_id_code TEXT UNIQUE,
            full_name       TEXT NOT NULL,
            gender          TEXT,
            department      TEXT,
            branch_code     TEXT,
            branch_id       TEXT,
            email           TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS student_details (
            enrollment_no        TEXT PRIMARY KEY,
            user_id              INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            full_name            TEXT NOT NULL,
            email                TEXT,
            major                TEXT,
            branch_code          TEXT,
            branch_id            TEXT,
            year_of_induction    INTEGER,
            current_year_college INTEGER,
            semester             INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id           SERIAL PRIMARY KEY,
            subject_code TEXT UNIQUE,
            subject_name TEXT NOT NULL,
            branch       TEXT NOT NULL,
            semester     INTEGER NOT NULL,
            faculty_id   INTEGER REFERENCES faculty_details(id) ON DELETE SET NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exams (
            course_code      TEXT PRIMARY KEY,
            subject_id       INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            exam_name        TEXT NOT NULL,
            exam_date        TEXT,
            start_time       TEXT,
            end_time         TEXT,
            total_marks      INTEGER,
            duration_minutes INTEGER,
            pass_percentage  INTEGER DEFAULT 40
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS student_subjects (
            enrollment_no TEXT NOT NULL REFERENCES student_details(enrollment_no) ON DELETE CASCADE,
            course_code   TEXT NOT NULL REFERENCES exams(course_code) ON DELETE CASCADE,
            PRIMARY KEY (enrollment_no, course_code)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id                     SERIAL PRIMARY KEY,
            subject_id             INTEGER REFERENCES subjects(id) ON DELETE CASCADE,
            question_text          TEXT NOT NULL,
            question_type          TEXT NOT NULL,
            marks                  INTEGER DEFAULT 1,
            negative_marks         REAL DEFAULT 0,
            difficulty             TEXT,
            correct_integer_answer INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS options (
            id          SERIAL PRIMARY KEY,
            question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
            option_text TEXT NOT NULL,
            is_correct  INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_questions (
            id          SERIAL PRIMARY KEY,
            course_code TEXT NOT NULL REFERENCES exams(course_code) ON DELETE CASCADE,
            question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            section     TEXT DEFAULT 'A',
            UNIQUE (course_code, question_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_attempts (
            id            SERIAL PRIMARY KEY,
            enrollment_no TEXT NOT NULL REFERENCES student_details(enrollment_no) ON DELETE CASCADE,
            course_code   TEXT NOT NULL REFERENCES exams(course_code) ON DELETE CASCADE,
            score         INTEGER,
            completed     INTEGER DEFAULT 0,
            attempt_time  TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_blacklist (
            id            SERIAL PRIMARY KEY,
            course_code   TEXT NOT NULL REFERENCES exams(course_code) ON DELETE CASCADE,
            enrollment_no TEXT NOT NULL REFERENCES student_details(enrollment_no) ON DELETE CASCADE
        )
    """)

    # ── Scheduled Exams (faculty → students visibility) ──────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_exams (
            id          SERIAL PRIMARY KEY,
            subject     TEXT NOT NULL,
            course_code TEXT,
            exam_date   DATE NOT NULL,
            start_time  TIME NOT NULL,
            end_time    TIME NOT NULL,
            duration    INTEGER NOT NULL,
            total_marks INTEGER NOT NULL,
            status      TEXT NOT NULL DEFAULT 'scheduled',
            created_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_scheduled_exams_date
            ON scheduled_exams (exam_date, status)
    """)

    # ── Audit Logs (Activity & Security Tracking) ──────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id          SERIAL PRIMARY KEY,
            event_type  TEXT NOT NULL,
            actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
            actor_role  TEXT,
            target_type TEXT,
            target_id   TEXT,
            description TEXT,
            ip_address  TEXT,
            user_agent  TEXT,
            metadata    JSONB DEFAULT '{}',
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_actor
            ON audit_logs (actor_id)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type
            ON audit_logs (event_type)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
            ON audit_logs (created_at DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_logs_target
            ON audit_logs (target_type, target_id)
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("PostgreSQL schema verified / initialized.")


# ═══════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ═══════════════════════════════════════════════════════════════

def get_user_by_username(username, role):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = %s AND role = %s", (username, role))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user


def get_admin_by_username(username):
    return get_user_by_username(username, "admin")


def get_user_by_email(email, role):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s AND role = %s", (email.strip().lower(), role))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user


def get_user_by_email_any_role(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email = %s", (email.strip().lower(),))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user


def update_user_password(user_id, new_password):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET password = %s WHERE id = %s",
                (generate_password_hash(new_password), user_id))
    conn.commit()
    cur.close()
    conn.close()


# ═══════════════════════════════════════════════════════════════
#  PORTAL HELPERS
# ═══════════════════════════════════════════════════════════════

def get_faculty_by_user_id(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (user_id,))
    faculty = cur.fetchone()
    cur.close()
    conn.close()
    return faculty


def get_student_by_user_id(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM student_details WHERE user_id = %s", (user_id,))
    student = cur.fetchone()
    cur.close()
    conn.close()
    return student


def add_user(username, password, role, email=None):
    conn = get_connection()
    cur = conn.cursor()
    hashed = generate_password_hash(password)
    cur.execute(
        "INSERT INTO users (username, password, role, email) VALUES (%s, %s, %s, %s) RETURNING id",
        (username, hashed, role, email)
    )
    new_user_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return new_user_id

def update_scheduled_exam_statuses():
    """Automatically transitions exam statuses based on current timestamp."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        # 1. scheduled -> active
        cur.execute("""
            UPDATE scheduled_exams 
            SET status = 'active'
            WHERE status = 'scheduled'
              AND (exam_date + start_time) <= LOCALTIMESTAMP
        """)
        
        # 2. active -> completed
        cur.execute("""
            UPDATE scheduled_exams 
            SET status = 'completed'
            WHERE status = 'active'
              AND (exam_date + end_time) <= LOCALTIMESTAMP
        """)
        conn.commit()
    except Exception as e:
        print(f"Error updating exam statuses: {e}")
    finally:
        conn.close()
