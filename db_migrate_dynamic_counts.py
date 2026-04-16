import sqlite3

def migrate():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE exams ADD COLUMN dynamic_easy INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE exams ADD COLUMN dynamic_medium INTEGER DEFAULT 0;")
        cursor.execute("ALTER TABLE exams ADD COLUMN dynamic_hard INTEGER DEFAULT 0;")
        conn.commit()
        print("Migration successful: Added dynamic_easy, dynamic_medium, dynamic_hard to exams table.")
    except sqlite3.OperationalError as e:
        print("Migration info/error:", e)
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
