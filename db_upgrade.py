import sqlite3
from werkzeug.security import generate_password_hash

def upgrade_db():
    conn = sqlite3.connect('c:/Users/Diya Panjwani/Downloads/EXAM-main/EXAM-main/database.db')
    conn.execute('PRAGMA foreign_keys = OFF;')
    
    # Check if table already altered
    has_admin = False
    try:
        conn.execute("INSERT INTO users (username, password, email, role) VALUES ('test_dummy', '123', 'dummy@admin.com', 'admin')")
        conn.execute("DELETE FROM users WHERE username = 'test_dummy'")
        has_admin = True
    except sqlite3.IntegrityError:
        has_admin = False
        
    if not has_admin:
        print("Migrating users table...")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT UNIQUE,
                role TEXT NOT NULL CHECK(role IN ('student','faculty','admin'))
            )
        """)
        conn.execute("INSERT INTO users_new SELECT * FROM users;")
        conn.execute("DROP TABLE users;")
        conn.execute("ALTER TABLE users_new RENAME TO users;")
    
    # Check if admin user exists
    cur = conn.execute("SELECT * FROM users WHERE username = 'admin'")
    if not cur.fetchone():
        print("Creating default admin user...")
        pwd = generate_password_hash("admin")
        conn.execute("INSERT INTO users (username, password, role, email) VALUES ('admin', ?, 'admin', 'admin@example.com')", (pwd,))
        
    conn.commit()
    conn.close()
    print("DB upgrade completed.")

if __name__ == '__main__':
    upgrade_db()
