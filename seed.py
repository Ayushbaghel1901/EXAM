from database import add_user, get_admin_by_username, init_db
import sys

def seed_data():
    print("Initializing database...")
    init_db()
    
    admin_username = "admin"
    admin_password = "admin_password_2024" # Default password
    admin_email = "admin@example.com"
    
    print(f"Checking for admin user: {admin_username}")
    if not get_admin_by_username(admin_username):
        print(f"Creating admin user: {admin_username}")
        add_user(admin_username, admin_password, "admin", admin_email)
        print("Admin user created successfully.")
    else:
        print("Admin user already exists.")

if __name__ == "__main__":
    seed_data()
