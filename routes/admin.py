from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from database import get_connection
from werkzeug.security import check_password_hash

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("user_id") or session.get("role") != "admin":
        return redirect(url_for("auth_bp.admin_login"))
        
    conn = get_connection()
    # Count metrics
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_exams = conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0]
    total_students = conn.execute("SELECT COUNT(*) FROM student_details").fetchone()[0]
    total_faculty = conn.execute("SELECT COUNT(*) FROM faculty_details").fetchone()[0]
    conn.close()
    
    return render_template("admin_dashboard.html", 
        total_users=total_users, 
        total_exams=total_exams,
        total_students=total_students,
        total_faculty=total_faculty,
        username=session.get("username")
    )

@admin_bp.route("/admin/users")
def manage_users():
    if not session.get("user_id") or session.get("role") != "admin":
        return redirect(url_for("auth_bp.admin_login"))
        
    conn = get_connection()
    users = conn.execute("SELECT id, username, email, role FROM users ORDER BY role").fetchall()
    conn.close()
    
    return render_template("admin_users.html", users=[dict(u) for u in users])

@admin_bp.route("/admin/users/delete/<int:user_id>", methods=["POST"])
def delete_user(user_id):
    if not session.get("user_id") or session.get("role") != "admin":
        return redirect(url_for("auth_bp.admin_login"))
        
    if user_id == session.get("user_id"):
        flash("You cannot delete your own admin account.", "danger")
        return redirect(url_for("admin_bp.manage_users"))
        
    conn = get_connection()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    flash("User deleted successfully.", "success")
    return redirect(url_for("admin_bp.manage_users"))

@admin_bp.route("/admin/exams")
def manage_exams():
    if not session.get("user_id") or session.get("role") != "admin":
        return redirect(url_for("auth_bp.admin_login"))
        
    conn = get_connection()
    exams = conn.execute("""
        SELECT e.*, s.subject_name 
        FROM exams e
        JOIN subjects s ON e.subject_id = s.id
        ORDER BY e.exam_date DESC
    """).fetchall()
    conn.close()
    
    return render_template("admin_exams.html", exams=[dict(e) for e in exams])

@admin_bp.route("/admin/exams/delete/<string:course_code>", methods=["POST"])
def delete_exam(course_code):
    if not session.get("user_id") or session.get("role") != "admin":
        return redirect(url_for("auth_bp.admin_login"))
        
    conn = get_connection()
    conn.execute("DELETE FROM exams WHERE course_code = ?", (course_code,))
    conn.commit()
    conn.close()
    
    flash("Exam deleted successfully.", "success")
    return redirect(url_for("admin_bp.manage_exams"))
