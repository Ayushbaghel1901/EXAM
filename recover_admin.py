import os
from werkzeug.security import generate_password_hash
from database import get_connection
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def recover_admin():
    # Configuration
    # You can change these if you want a different username/password
    admin_username = "Admin-ExamPortal"
    admin_password = "Exam!2026"
    
    print(f"--- Admin Recovery Started ---")
    print(f"Target Username: {admin_username}")
    
    conn = get_connection()
    try:
        cur = conn.cursor()
        
        # Check if user already exists
        cur.execute("SELECT id FROM users WHERE username = %s", (admin_username,))
        exists = cur.fetchone()
        
        if exists:
            print(f"User '{admin_username}' already exists in the database. Updating password instead...")
            hashed_pw = generate_password_hash(admin_password)
            cur.execute("UPDATE users SET password = %s, role = 'admin' WHERE username = %s", (hashed_pw, admin_username))
            print("Password updated successfully!")
        else:
            print(f"Creating new admin user '{admin_username}'...")
            hashed_pw = generate_password_hash(admin_password)
            cur.execute("""
                INSERT INTO users (username, password, role) 
                VALUES (%s, %s, 'admin')
            """, (admin_username, hashed_pw))
            print("Admin user created successfully!")
            
        conn.commit()
        print("Done! You can now log in to the Admin Portal.")
        
    except Exception as e:
        print(f"Error during recovery: {str(e)}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    recover_admin()
