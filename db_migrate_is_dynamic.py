import sqlite3

def migrate():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE exams ADD COLUMN is_dynamic INTEGER DEFAULT 0;")
        conn.commit()
        print("Success: is_dynamic added.")
    except Exception as e:
        print("Info:", e)
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
