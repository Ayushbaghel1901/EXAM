from flask import Flask, redirect, url_for, session, request
import logging
from routes.logger import log_event, UNAUTHORIZED_ACCESS
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

@app.before_request
def check_access_and_log():
    path = request.path
    role = session.get("role")
    u_id = session.get("user_id")
    print(f"[DEBUG REQUEST] Path: {path}, Role: {role}, UID: {u_id}, session_keys: {list(session.keys())}")
    if not path.startswith("/admin") and not path.startswith("/faculty") and not path.startswith("/student"):
        return
        
    role = session.get("role")
    user_id = session.get("user_id")
    
    # If a user is completely unauthenticated and tries to hit a protected path
    if not role:
        # Avoid logging the static endpoints or API endpoints if they shouldn't trigger this, but path starts with /admin, /faculty, /student.
        if path not in ["/student/login", "/faculty/login", "/admin/login", "/admin/mfa"]:
            log_event(
                event_type=UNAUTHORIZED_ACCESS,
                description=f"Unauthenticated access attempt to {path}",
                metadata={"path": path},
                request=request
            )
        return

    # If they are authenticated but hit the wrong domain
    if path.startswith("/admin") and role != "admin":
        log_event(
            event_type=UNAUTHORIZED_ACCESS,
            description=f"Unauthorized access attempt to {path} by role '{role}'",
            actor_id=user_id,
            actor_role=role,
            metadata={"path": path},
            request=request
        )
    elif path.startswith("/faculty") and role not in ["faculty", "admin"]:
        # Exclude login route
        if path == "/faculty/login":
            return
        log_event(
            event_type=UNAUTHORIZED_ACCESS,
            description=f"Unauthorized access attempt to {path} by role '{role}'",
            actor_id=user_id,
            actor_role=role,
            metadata={"path": path},
            request=request
        )
    elif path.startswith("/student") and role != "student" and role != "admin":
        if path == "/student/login":
            return
        log_event(
            event_type=UNAUTHORIZED_ACCESS,
            description=f"Unauthorized access attempt to {path} by role '{role}'",
            actor_id=user_id,
            actor_role=role,
            metadata={"path": path},
            request=request
        )

@app.route("/")
def home():
    if session.get("user_id"):
        role = session.get("role")
        if role == "admin":
            return redirect(url_for("admin_bp.admin_dashboard"))
        elif role == "faculty":
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        return redirect(url_for("student_bp.student_dashboard"))
    return redirect(url_for("auth_bp.student_login"))

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5006)
