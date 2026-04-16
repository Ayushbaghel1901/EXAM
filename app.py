from flask import Flask, redirect, url_for, session
from database import init_db
from routes.auth import auth_bp
from routes.student import student_bp
from routes.faculty import faculty_bp
from routes.faculty_analysis import faculty_analysis_bp
from routes.admin import admin_bp

app = Flask(__name__)
app.secret_key = "ONLINE_EXAM_PORTAL_STABLE_KEY_2024"

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(student_bp)
app.register_blueprint(faculty_bp)
app.register_blueprint(faculty_analysis_bp)
app.register_blueprint(admin_bp)

@app.route("/")
def home():
    if session.get("user_id"):
        if session.get("role") == "faculty":
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        elif session.get("role") == "admin":
            return redirect(url_for("admin_bp.admin_dashboard"))
        return redirect(url_for("student_bp.student_dashboard"))
    return redirect(url_for("auth_bp.student_login"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5006)
