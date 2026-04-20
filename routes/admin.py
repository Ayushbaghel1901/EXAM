from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, Response
from database import get_connection
from werkzeug.security import check_password_hash, generate_password_hash
import random
import string
import csv
import io
import threading
import uuid
import time
from functools import wraps
from psycopg2.extras import execute_values
from routes.logger import (
    log_event, LOGIN_SUCCESS, USER_CREATED, USER_DELETED, 
    SUBJECT_ASSIGNED, SUBJECT_CREATED, SUBJECT_EDITED, SUBJECT_DELETED, CSV_IMPORT
)

admin_bp = Blueprint('admin_bp', __name__)

# ── Bulk Upload Task Registry ────────────────────────────────────────────────
# In a production app, use Redis/Celery. For this scale, a global dict is fine.
BULK_TASKS = {}

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id") or session.get("role") != "admin" or not session.get("mfa_verified"):
            return redirect(url_for("admin_bp.admin_login"))
        return f(*args, **kwargs)
    return decorated_function

def send_otp_email(to_email, otp):
    print(f"\n" + "█"*50)
    print(f" SECURITY ALERT: ADMIN LOGIN ATTEMPT")
    print(f" MFA OTP CODE: {otp}")
    print(f" DESTINATION: {to_email}")
    print("█"*50 + "\n")

@admin_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("user_id") and session.get("role") == "admin" and session.get("mfa_verified"):
        return redirect(url_for("admin_bp.admin_dashboard"))
        
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s AND role = 'admin'", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = "admin"
            session["mfa_verified"] = True

            log_event(
                event_type=LOGIN_SUCCESS,
                description=f"Admin '{username}' logged in successfully.",
                actor_id=user["id"],
                actor_role="admin",
                request=request
            )
            flash("Welcome back, Administrator.", "success")
            return redirect(url_for("admin_bp.admin_dashboard"))
        else:
            flash("Access Denied: Invalid administrator credentials.", "danger")
            
    return render_template("admin_login.html")

@admin_bp.route("/admin/mfa", methods=["GET", "POST"])
def admin_mfa():
    if not session.get("mfa_user_id"):
        return redirect(url_for("admin_bp.admin_login"))
        
    if request.method == "POST":
        otp_input = request.form.get("otp")
        if otp_input == session.get("mfa_otp"):
            session["user_id"] = session.get("mfa_user_id")
            session["role"] = "admin"
            session["mfa_verified"] = True
            
            session.pop("mfa_user_id", None)
            session.pop("mfa_otp", None)
            session.pop("mfa_email", None)
            
            flash("Secure session established. Welcome back.", "success")
            return redirect(url_for("admin_bp.admin_dashboard"))
        else:
            flash("Verification failed: Invalid security code.", "danger")
            
    return render_template("admin_mfa.html", email=session.get("mfa_email"))

@admin_bp.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_bp.admin_login"))

@admin_bp.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) AS c FROM faculty_details"); total_faculty = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM student_details"); total_students = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM subjects");        total_subjects = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM exams");           total_exams = cur.fetchone()["c"]
    
    cur.execute("""
        SELECT sd.full_name, e.exam_name, ea.score, e.total_marks, ea.attempt_time
        FROM exam_attempts ea
        JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
        JOIN exams e ON ea.course_code = e.course_code
        ORDER BY ea.id DESC LIMIT 5
    """)
    recent_attempts = cur.fetchall()
    
    cur.execute("""
        SELECT fd.id, fd.full_name, fd.department,
               (SELECT COUNT(*) FROM subjects s WHERE s.faculty_id = fd.id) as subject_count
        FROM faculty_details fd
    """)
    faculty_list = cur.fetchall()

    cur.close()
    conn.close()
    
    return render_template("admin_dashboard.html",
        stats={
            "faculty": total_faculty,
            "students": total_students,
            "subjects": total_subjects,
            "exams": total_exams
        },
        recent_activity=[dict(r) for r in recent_attempts],
        faculty_list=[dict(f) for f in faculty_list]
    )

@admin_bp.route("/admin/faculty", methods=["GET", "POST"])
@admin_required
def manage_faculty():
    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        action    = request.form.get("action", "add")
        username  = request.form.get("username")
        password  = request.form.get("password")
        full_name = request.form.get("full_name")
        email     = request.form.get("email")
        dept      = request.form.get("department")
        faculty_id = request.form.get("faculty_id")

        try:
            if action == "add":
                cur.execute(
                    "INSERT INTO users (username, password, role, email) VALUES (%s, %s, 'faculty', %s) RETURNING id",
                    (username, generate_password_hash(password), email)
                )
                uid = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO faculty_details (user_id, faculty_id_code, full_name, department, email) VALUES (%s, %s, %s, %s, %s)",
                    (uid, faculty_id, full_name, dept, email)
                )
                log_event(
                    event_type=USER_CREATED,
                    description=f"Admin created faculty user '{username}'.",
                    actor_id=session["user_id"],
                    actor_role="admin",
                    target_type="user",
                    target_id=str(uid),
                    request=request
                )
                flash(f"Faculty {full_name} registered successfully.", "success")

            elif action == "edit" and faculty_id:
                cur.execute("SELECT user_id FROM faculty_details WHERE id = %s", (faculty_id,))
                row = cur.fetchone()
                if row:
                    user_id = row["user_id"]
                    cur.execute("UPDATE users SET username = %s, email = %s WHERE id = %s", (username, email, user_id))
                    if password:
                        cur.execute("UPDATE users SET password = %s WHERE id = %s", (generate_password_hash(password), user_id))
                    cur.execute("UPDATE faculty_details SET full_name = %s, department = %s WHERE id = %s", (full_name, dept, faculty_id))
                    flash(f"Faculty {full_name} updated successfully.", "success")

            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Error processing faculty: {e}", "danger")

    cur.execute("""
        SELECT fd.*, u.username, u.email
        FROM faculty_details fd
        JOIN users u ON fd.user_id = u.id
    """)
    faculty = cur.fetchall()

    cur.execute("SELECT DISTINCT department FROM faculty_details WHERE department IS NOT NULL AND department != '' ORDER BY department")
    departments = [r['department'] for r in cur.fetchall()]

    cur.close()
    conn.close()
    return render_template("admin_faculty.html", faculty=faculty, departments=departments)


@admin_bp.route("/admin/faculty/delete/<int:id>", methods=["POST"])
@admin_required
def delete_faculty(id):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM faculty_details WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            user_id = row["user_id"]
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("DELETE FROM faculty_details WHERE id = %s", (id,))
            conn.commit()
            log_event(
                event_type=USER_DELETED,
                description=f"Admin deleted faculty user_id {user_id}.",
                actor_id=session["user_id"],
                actor_role="admin",
                target_type="user",
                target_id=str(user_id),
                request=request
            )
            return jsonify({"success": True})
        return jsonify({"error": "Faculty not found"}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@admin_bp.route("/admin/students", methods=["GET", "POST"])
@admin_required
def manage_students():
    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        action        = request.form.get("action", "add")
        enrollment_no = request.form.get("enrollment_no")
        full_name     = request.form.get("full_name")
        email         = request.form.get("email")
        branch        = request.form.get("branch")
        semester      = request.form.get("semester")
        username      = request.form.get("username")
        password      = request.form.get("password")

        try:
            if action == "add":
                cur.execute(
                    "INSERT INTO users (username, password, role, email) VALUES (%s, %s, 'student', %s) RETURNING id",
                    (username, generate_password_hash(password), email)
                )
                uid = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO student_details (enrollment_no, user_id, full_name, branch_code, semester) VALUES (%s, %s, %s, %s, %s)",
                    (enrollment_no, uid, full_name, branch, semester)
                )
                log_event(
                    event_type=USER_CREATED,
                    description=f"Admin created student user '{username}'.",
                    actor_id=session["user_id"],
                    actor_role="admin",
                    target_type="user",
                    target_id=str(uid),
                    request=request
                )
                flash(f"Student {full_name} enrolled successfully.", "success")

            elif action == "edit" and enrollment_no:
                cur.execute("SELECT user_id FROM student_details WHERE enrollment_no = %s", (enrollment_no,))
                row = cur.fetchone()
                if row:
                    user_id = row["user_id"]
                    cur.execute("UPDATE users SET username = %s, email = %s WHERE id = %s", (username, email, user_id))
                    if password:
                        cur.execute("UPDATE users SET password = %s WHERE id = %s", (generate_password_hash(password), user_id))
                    cur.execute(
                        "UPDATE student_details SET full_name = %s, branch_code = %s, semester = %s WHERE enrollment_no = %s",
                        (full_name, branch, semester, enrollment_no)
                    )
                    flash(f"Student {full_name} records updated.", "success")

            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Error processing student: {e}", "danger")

    cur.execute("""
        SELECT sd.*, u.username, u.email
        FROM student_details sd
        JOIN users u ON sd.user_id = u.id
        ORDER BY sd.enrollment_no ASC
    """)
    students = cur.fetchall()

    # Dynamic Branches from database
    cur.execute("SELECT DISTINCT branch_code, major FROM student_details WHERE branch_code IS NOT NULL AND branch_code != '' ORDER BY branch_code")
    branches_raw = cur.fetchall()
    branch_map = {}
    for r in branches_raw:
        code = r['branch_code']
        name = r['major'] or code
        if code not in branch_map or (branch_map[code] == code and name != code):
            branch_map[code] = name
    branches = [{"code": k, "name": v} for k, v in sorted(branch_map.items(), key=lambda x: x[1])]

    cur.close()
    conn.close()
    return render_template("admin_students.html", students=students, branches=branches)


@admin_bp.route("/admin/students/delete/<string:enrollment_no>", methods=["POST"])
@admin_required
def delete_student(enrollment_no):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM student_details WHERE enrollment_no = %s", (enrollment_no,))
        row = cur.fetchone()
        if row:
            user_id = row["user_id"]
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cur.execute("DELETE FROM student_details WHERE enrollment_no = %s", (enrollment_no,))
            conn.commit()
            log_event(
                event_type=USER_DELETED,
                description=f"Admin deleted student '{enrollment_no}' (user_id {user_id}).",
                actor_id=session["user_id"],
                actor_role="admin",
                target_type="user",
                target_id=str(user_id),
                request=request
            )
            return jsonify({"success": True})
        return jsonify({"error": "Student not found"}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


@admin_bp.route("/admin/subjects", methods=["GET", "POST"])
@admin_required
def manage_subjects():
    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        action       = request.form.get("action", "add")
        sub_id       = request.form.get("subject_id")
        sub_code     = request.form.get("subject_code")
        sub_name     = request.form.get("subject_name")
        branch       = request.form.get("branch")
        semester     = request.form.get("semester")
        faculty_id   = request.form.get("faculty_id") or None

        try:
            if action == "add":
                cur.execute(
                    "INSERT INTO subjects (subject_code, subject_name, branch, semester, faculty_id) VALUES (%s, %s, %s, %s, %s)",
                    (sub_code, sub_name, branch, semester, faculty_id)
                )
                log_event(
                    event_type=SUBJECT_CREATED,
                    description=f"Admin created subject '{sub_name}' ({sub_code}).",
                    actor_id=session["user_id"],
                    actor_role="admin",
                    target_type="subject",
                    target_id=sub_code,
                    request=request
                )
                flash(f"Subject {sub_name} created successfully.", "success")
            
            elif action == "edit" and sub_id:
                cur.execute(
                    "UPDATE subjects SET subject_code=%s, subject_name=%s, branch=%s, semester=%s, faculty_id=%s WHERE id=%s",
                    (sub_code, sub_name, branch, semester, faculty_id, sub_id)
                )
                log_event(
                    event_type=SUBJECT_EDITED,
                    description=f"Admin updated subject {sub_code}.",
                    actor_id=session["user_id"],
                    actor_role="admin",
                    target_type="subject",
                    target_id=sub_code,
                    request=request
                )
                flash(f"Subject {sub_name} updated successfully.", "success")

            conn.commit()
        except Exception as e:
            conn.rollback()
            flash(f"Error processing subject: {e}", "danger")

    cur.execute("""
        SELECT s.*, fd.full_name as faculty_name
        FROM subjects s
        LEFT JOIN faculty_details fd ON s.faculty_id = fd.id
        ORDER BY s.subject_code ASC
    """)
    subjects = cur.fetchall()

    cur.execute("SELECT id, full_name, department FROM faculty_details ORDER BY full_name")
    faculty = cur.fetchall()

    cur.execute("SELECT DISTINCT branch_code, major FROM student_details WHERE branch_code IS NOT NULL AND branch_code != '' ORDER BY branch_code")
    branches_raw = cur.fetchall()
    branch_map = {}
    for r in branches_raw:
        code = r['branch_code']
        name = r['major'] or code
        if code not in branch_map or (branch_map[code] == code and name != code):
            branch_map[code] = name
    
    # Sort branches by name
    branches = [{"code": k, "name": v} for k, v in sorted(branch_map.items(), key=lambda x: x[1])]

    cur.close()
    conn.close()
    return render_template("admin_subjects.html", subjects=subjects, faculty=faculty, branches=branches)


@admin_bp.route("/admin/subjects/delete/<int:id>", methods=["POST"])
@admin_required
def delete_subject(id):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT subject_code FROM subjects WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            code = row["subject_code"]
            cur.execute("DELETE FROM subjects WHERE id = %s", (id,))
            conn.commit()
            log_event(
                event_type=SUBJECT_DELETED,
                description=f"Admin deleted subject {code}.",
                actor_id=session["user_id"],
                actor_role="admin",
                target_type="subject",
                target_id=code,
                request=request
            )
            return jsonify({"success": True})
        return jsonify({"error": "Subject not found"}), 404
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        conn.close()


# ─── CSV Template Downloads ───────────────────────────────────────────────────

@admin_bp.route("/admin/students/csv_template")
@admin_required
def student_csv_template():
    header = [
        "student_name", "enrollment", "email", "major", "branch_code", "branch_id",
        "year_of_induction", "current_year_college", "semester_no",
        "course_1", "course_2", "course_3", "course_4", "course_5"
    ]
    sample = [
        "Aarav Bose", "BTAD24O1001", "24AD10Aa001@mitsgwl.ac.in",
        "Artificial Intelligence and Data Science", "AD", "1",
        "2024", "3", "5",
        "01243501", "01243502", "01243503", "01243504", "01243505"
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(sample)
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=students_bulk_template.csv"}
    )

@admin_bp.route("/admin/faculty/csv_template")
@admin_required
def faculty_csv_template():
    header = [
        "faculty_name", "gender", "faculty_id", "email", "department_name",
        "branch_code", "branch_id", "courses_taught"
    ]
    sample = [
        "Dr. Deepa Das", "Female", "MITS001F", "deepa001@mitsgwl.ac.in",
        "Civil Engineering", "CE", "06", "12243503, 05252304"
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(sample)
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=faculty_bulk_template.csv"}
    )

@admin_bp.route("/admin/subjects/csv_template")
@admin_required
def subject_csv_template():
    header = ["subject_code", "subject_name", "branch", "semester"]
    sample = ["CS401", "Analysis of Algorithms", "CSE", "4"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerow(sample)
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=subjects_bulk_template.csv"}
    )


# ── Bulk Upload Task Infrastructure ──────────────────────────────────────────

@admin_bp.route("/admin/bulk_status/<task_id>")
@admin_required
def get_bulk_status(task_id):
    active_tasks = [tid for tid, t in BULK_TASKS.items() if not t.get('completed')]
    print(f"[DEBUG] Polling: {task_id} | Total Tasks: {len(BULK_TASKS)} | Active: {len(active_tasks)}")
    
    task = BULK_TASKS.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(task)

def process_student_bulk(task_id, rows, valid_courses):
    """
    Optimized high-speed background worker for student bulk upload.
    Uses PostgreSQL CTE (Common Table Expressions) to insert all users and profiles 
    in a single atomic batch trip, drastically reducing network latency.
    """
    conn = get_connection()
    cur  = conn.cursor()
    task = BULK_TASKS[task_id]
    
    try:
        default_pw_hash = generate_password_hash("Student@123")
        
        # 1. Filter out duplicates in memory first
        cur.execute("SELECT enrollment_no FROM student_details")
        existing_enrollments = {r["enrollment_no"] for r in cur.fetchall()}
        cur.execute("SELECT username FROM users")
        existing_users = {r["username"] for r in cur.fetchall()}

        clean_rows = []
        for i, row in enumerate(rows, start=2):
            enrollment = (row.get("enrollment") or "").strip()
            if not enrollment or enrollment in existing_enrollments or enrollment in existing_users:
                task["skipped"] += 1
                continue
            
            clean_rows.append({
                "enrollment": enrollment,
                "name": (row.get("student_name") or "").strip()[:200],
                "email": (row.get("email") or "").strip()[:200] or None,
                "major": (row.get("major") or "").strip()[:200] or None,
                "branch_code": (row.get("branch_code") or "").strip()[:50] or None,
                "branch_id": (row.get("branch_id") or "").strip()[:50] or None,
                "year_ind": int(row.get("year_of_induction") or 0) if row.get("year_of_induction") else None,
                "curr_year": int(row.get("current_year_college") or 0) if row.get("current_year_college") else None,
                "semester": int(row.get("semester_no") or 0) if row.get("semester_no") else None,
                "courses": [
                    (row.get(f"course_{c}") or "").strip()
                    for c in range(1, 6)
                    if (row.get(f"course_{c}") or "").strip() in valid_courses
                ]
            })

        # 0. Fetch existing exams for auto-assignment
        cur.execute("""
            SELECT e.course_code, s.branch, s.semester 
            FROM exams e 
            JOIN subjects s ON e.subject_id = s.id
        """)
        exams_lookup = cur.fetchall()
        auto_enroll_map = {}
        for ex in exams_lookup:
            # Map by (branch, semester)
            key = (str(ex['branch']).strip(), ex['semester'])
            if key not in auto_enroll_map:
                auto_enroll_map[key] = []
            auto_enroll_map[key].append(ex['course_code'])

        if not clean_rows:
            task["progress"] = 100
            task["completed"] = True
            task["status"] = "finished"
            return

        # We'll do batches of 500
        batch_size = 500
        for b_start in range(0, len(clean_rows), batch_size):
            batch = clean_rows[b_start : b_start + batch_size]
            
            # Prepare data for multi-row INSERT
            user_data = [(r["enrollment"], default_pw_hash, r["email"]) for r in batch]
            cur.execute("SAVEPOINT batch_sp")
            try:
                returned_users = execute_values(cur, 
                    "INSERT INTO users (username, password, role, email) VALUES %s RETURNING id, username", 
                    user_data, template="(%s, %s, 'student', %s)", fetch=True
                )
                user_id_map = {r["username"]: r["id"] for r in returned_users}
                
                # B. Bulk Insert Profiles
                profile_data = [
                    (user_id_map[r["enrollment"]], r["enrollment"], r["name"], r["email"], r["major"], r["branch_code"], r["branch_id"], r["year_ind"], r["curr_year"], r["semester"])
                    for r in batch
                ]
                execute_values(cur,
                    "INSERT INTO student_details (user_id, enrollment_no, full_name, email, major, branch_code, branch_id, year_of_induction, current_year_college, semester) VALUES %s",
                    profile_data
                )
                
                # C. Bulk Insert Course Links (Manual + Auto)
                course_links = []
                for r in batch:
                    # Collect all assignments (manual from CSV + auto based on Branch/Sem)
                    all_course_codes = set(r["courses"])
                    
                    # Auto lookup
                    b_code = str(r["branch_code"]).strip() if r["branch_code"] else None
                    sem = r["semester"]
                    if b_code and sem and (b_code, sem) in auto_enroll_map:
                        for auto_c in auto_enroll_map[(b_code, sem)]:
                            all_course_codes.add(auto_c)
                    
                    for c_code in all_course_codes:
                        course_links.append((r["enrollment"], c_code))
                
                if course_links:
                    execute_values(cur, 
                        "INSERT INTO student_subjects (enrollment_no, course_code) VALUES %s ON CONFLICT DO NOTHING",
                        course_links
                    )
                
                cur.execute("RELEASE SAVEPOINT batch_sp")
                task["success"] += len(batch)
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT batch_sp")
                task["errors"].append(f"Batch {b_start//batch_size + 1} failed: {str(e)}")

            task["current_row"] = min(b_start + batch_size, len(clean_rows))
            task["progress"] = int((task["current_row"] / len(rows)) * 100)

        conn.commit()
    except Exception as e:
        task["errors"].append(f"Critical System Error: {str(e)}")
    finally:
        cur.close()
        conn.close()
        task["completed"] = True
        task["status"] = "finished"
        task["progress"] = 100

def process_faculty_bulk(task_id, rows, valid_subjects):
    """Optimized high-speed background worker for faculty bulk upload."""
    conn = get_connection()
    cur  = conn.cursor()
    task = BULK_TASKS[task_id]
    
    try:
        default_pw_hash = generate_password_hash("Faculty@123")
        
        cur.execute("SELECT faculty_id_code FROM faculty_details")
        existing_faculty = {r["faculty_id_code"] for r in cur.fetchall()}
        cur.execute("SELECT username FROM users")
        existing_users = {r["username"] for r in cur.fetchall()}

        clean_rows = []
        for i, row in enumerate(rows, start=2):
            fac_id = (row.get("faculty_id") or "").strip()
            if not fac_id or fac_id in existing_faculty or fac_id.lower() in existing_users:
                task["skipped"] += 1
                continue
            
            clean_rows.append({
                "fac_id": fac_id,
                "username": fac_id.lower(),
                "name": (row.get("faculty_name") or "").strip(),
                "gender": (row.get("gender") or "").strip(),
                "email": (row.get("email") or "").strip(),
                "dept": (row.get("department_name") or "").strip(),
                "branch_code": (row.get("branch_code") or "").strip(),
                "branch_id": (row.get("branch_id") or "").strip(),
                "courses_str": (row.get("courses_taught") or "").strip()
            })

        if not clean_rows:
            task["progress"] = 100
            task["completed"] = True
            task["status"] = "finished"
            return

        batch_size = 500
        for b_start in range(0, len(clean_rows), batch_size):
            batch = clean_rows[b_start : b_start + batch_size]
            
            user_data = [(r["username"], default_pw_hash, r["email"]) for r in batch]
            
            cur.execute("SAVEPOINT batch_sp")
            try:
                returned_users = execute_values(cur, 
                    "INSERT INTO users (username, password, role, email) VALUES %s RETURNING id, username", 
                    user_data, template="(%s, %s, 'faculty', %s)", fetch=True
                )
                user_id_map = {r["username"]: r["id"] for r in returned_users}
                
                profile_data = [
                    (user_id_map[r["username"]], r["fac_id"], r["name"], r["gender"], r["dept"], r["branch_code"], r["branch_id"], r["email"])
                    for r in batch
                ]
                returned_facs = execute_values(cur,
                    "INSERT INTO faculty_details (user_id, faculty_id_code, full_name, gender, department, branch_code, branch_id, email) VALUES %s RETURNING id, faculty_id_code",
                    profile_data, fetch=True
                )
                fac_id_map = {r["faculty_id_code"]: r["id"] for r in returned_facs}
                
                for r in batch:
                    if r["courses_str"]:
                        fid = fac_id_map.get(r["fac_id"])
                        s_codes = [s.strip() for s in r["courses_str"].split(",") if s.strip()]
                        for s_code in s_codes:
                            subj_db_id = valid_subjects.get(s_code)
                            if fid and subj_db_id:
                                cur.execute("UPDATE subjects SET faculty_id = %s WHERE id = %s", (fid, subj_db_id))

                cur.execute("RELEASE SAVEPOINT batch_sp")
                task["success"] += len(batch)
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT batch_sp")
                task["errors"].append(f"Batch failed: {str(e)}")

            task["current_row"] = min(b_start + batch_size, len(clean_rows))
            task["progress"] = int((task["current_row"] / len(rows)) * 100)

        conn.commit()
    except Exception as e:
        task["errors"].append(f"Critical System Error: {str(e)}")
    finally:
        cur.close()
        conn.close()
        task["completed"] = True
        task["status"] = "finished"
        task["progress"] = 100


@admin_bp.route("/admin/students/bulk_upload", methods=["POST"])
@admin_required
def bulk_upload_students():
    if "csv_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["csv_file"]
    if not file or not file.filename.endswith(".csv"):
        return jsonify({"error": "Please upload a valid .csv file"}), 400

    content = file.stream.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT course_code FROM exams")
    valid_courses = {r["course_code"] for r in cur.fetchall()}
    cur.close()
    conn.close()

    task_id = str(uuid.uuid4())
    print(f"[DEBUG] Created Student Task: {task_id}")
    log_event(
        event_type=CSV_IMPORT,
        description=f"Admin initiated bulk student import with {len(rows)} rows.",
        actor_id=session["user_id"],
        actor_role="admin",
        metadata={"total_rows": len(rows), "task_id": task_id},
        request=request
    )
    BULK_TASKS[task_id] = {
        "id": task_id, "type": "student", "status": "processing", "progress": 0,
        "current_row": 0, "total_rows": len(rows), "success": 0, "skipped": 0,
        "errors": [], "completed": False
    }

    thread = threading.Thread(target=process_student_bulk, args=(task_id, rows, valid_courses))
    thread.daemon = True
    thread.start()
    return jsonify({"task_id": task_id})


@admin_bp.route("/admin/faculty/bulk_upload", methods=["POST"])
@admin_required
def bulk_upload_faculty():
    if "csv_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["csv_file"]
    if not file or not file.filename.endswith(".csv"):
        return jsonify({"error": "Please upload a valid .csv file"}), 400

    content = file.stream.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT subject_code, id FROM subjects")
    valid_subjects = {r["subject_code"]: r["id"] for r in cur.fetchall()}
    cur.close()
    conn.close()

    task_id = str(uuid.uuid4())
    log_event(
        event_type=CSV_IMPORT,
        description=f"Admin initiated bulk faculty import with {len(rows)} rows.",
        actor_id=session["user_id"],
        actor_role="admin",
        metadata={"total_rows": len(rows), "task_id": task_id},
        request=request
    )
    BULK_TASKS[task_id] = {
        "id": task_id, "type": "faculty", "status": "processing", "progress": 0,
        "current_row": 0, "total_rows": len(rows), "success": 0, "skipped": 0,
        "errors": [], "completed": False
    }

    thread = threading.Thread(target=process_faculty_bulk, args=(task_id, rows, valid_subjects))
    thread.daemon = True
    thread.start()
    return jsonify({"task_id": task_id})


@admin_bp.route("/admin/subjects/bulk_upload", methods=["POST"])
@admin_required
def bulk_upload_subjects():
    if "csv_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["csv_file"]
    if not file or not file.filename.endswith(".csv"):
        return jsonify({"error": "Please upload a valid .csv file"}), 400

    content = file.stream.read().decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))

    task_id = str(uuid.uuid4())
    log_event(
        event_type=CSV_IMPORT,
        description=f"Admin initiated bulk subject import with {len(rows)} rows.",
        actor_id=session["user_id"],
        actor_role="admin",
        metadata={"total_rows": len(rows), "task_id": task_id},
        request=request
    )
    BULK_TASKS[task_id] = {
        "id": task_id, "type": "subject", "status": "processing", "progress": 0,
        "current_row": 0, "total_rows": len(rows), "success": 0, "skipped": 0,
        "errors": [], "completed": False
    }

    thread = threading.Thread(target=process_subject_bulk, args=(task_id, rows))
    thread.daemon = True
    thread.start()
    return jsonify({"task_id": task_id})

def process_subject_bulk(task_id, rows):
    """Background worker for subject bulk upload."""
    conn = get_connection()
    cur  = conn.cursor()
    task = BULK_TASKS[task_id]
    
    try:
        cur.execute("SELECT subject_code FROM subjects")
        existing_codes = {r["subject_code"] for r in cur.fetchall()}

        clean_rows = []
        for row in rows:
            code = (row.get("subject_code") or "").strip()
            name = (row.get("subject_name") or "").strip()
            branch = (row.get("branch") or "").strip()
            semester = (row.get("semester") or "").strip()

            if not code or not name or not branch or not semester:
                task["skipped"] += 1
                continue
            
            if code in existing_codes:
                task["skipped"] += 1
                continue
            
            clean_rows.append((code, name, branch, int(semester)))

        if not clean_rows:
            task["progress"] = 100
            task["completed"] = True
            task["status"] = "finished"
            return

        batch_size = 500
        for b_start in range(0, len(clean_rows), batch_size):
            batch = clean_rows[b_start : b_start + batch_size]
            try:
                execute_values(cur, 
                    "INSERT INTO subjects (subject_code, subject_name, branch, semester) VALUES %s", 
                    batch
                )
                task["success"] += len(batch)
            except Exception as e:
                conn.rollback()
                task["errors"].append(f"Batch failed: {str(e)}")
                break

            task["current_row"] = min(b_start + batch_size, len(clean_rows))
            task["progress"] = int((task["current_row"] / len(rows)) * 100)

        conn.commit()
    except Exception as e:
        task["errors"].append(f"Critical System Error: {str(e)}")
    finally:
        cur.close()
        conn.close()
        task["completed"] = True
        task["status"] = "finished"
        task["progress"] = 100

# ── LOG VIEWER ─────────────────────────────────────────────────────────────

@admin_bp.route("/admin/logs")
@admin_required
def view_audit_logs():
    event_type = request.args.get("event_type", "")
    actor_role = request.args.get("actor_role", "")
    page = request.args.get("page", 1, type=int)
    per_page = 100
    offset = (page - 1) * per_page
    
    query = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    
    if event_type:
        query += " AND event_type = %s"
        params.append(event_type)
    if actor_role:
        query += " AND actor_role = %s"
        params.append(actor_role)
        
    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params.extend([per_page, offset])
    
    conn = get_connection()
    cur = conn.cursor()
    
    count_query = "SELECT COUNT(*) as c FROM audit_logs WHERE 1=1"
    count_params = []
    if event_type:
        count_query += " AND event_type = %s"
        count_params.append(event_type)
    if actor_role:
        count_query += " AND actor_role = %s"
        count_params.append(actor_role)
    cur.execute(count_query, tuple(count_params))
    total_logs = cur.fetchone()["c"]
    
    cur.execute(query, tuple(params))
    logs = cur.fetchall()
    
    # Fetch distinct event types for dropdown
    cur.execute("SELECT DISTINCT event_type FROM audit_logs ORDER BY event_type")
    event_types = [r["event_type"] for r in cur.fetchall()]
    
    cur.close()
    conn.close()
    
    total_pages = max(1, (total_logs + per_page - 1) // per_page)
    
    return render_template(
        "admin_logs.html", 
        logs=[dict(r) for r in logs], 
        page=page, 
        total_pages=total_pages,
        event_types=event_types,
        selected_event=event_type,
        selected_role=actor_role
    )
