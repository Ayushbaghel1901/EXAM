from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from database import get_connection, update_user_password, update_scheduled_exam_statuses
from werkzeug.security import check_password_hash
from datetime import datetime
from routes.logger import log_event, EXAM_STARTED, EXAM_SUBMITTED, DUPLICATE_SUBMIT, BLACKLIST_ADDED

student_bp = Blueprint('student_bp', __name__)

@student_bp.route("/student/dashboard")
def student_dashboard():
    if not session.get("user_id"):
        return redirect(url_for("auth_bp.student_login"))
    if session.get("role") == "faculty":
        return redirect(url_for("faculty_bp.faculty_dashboard"))
    if session.get("role") != "student":
        return redirect(url_for("auth_bp.student_login"))

    update_scheduled_exam_statuses()
    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("""
        SELECT enrollment_no, full_name, branch_code AS branch, semester 
        FROM student_details 
        WHERE user_id = %s
    """, (session["user_id"],))
    student = _cur.fetchone()

    if not student:
        conn.close()
        session.pop("user_id", None)
        session.pop("role", None)
        flash("Student profile not found. Please contact administration.", "danger")
        return redirect(url_for("auth_bp.student_login"))

    enrollment_no = student["enrollment_no"]

    # 2. Fetch assigned subjects
    _cur.execute("""
        SELECT s.subject_name, f.full_name AS faculty_name, f.department
        FROM student_subjects ss
        JOIN exams e ON ss.course_code = e.course_code
        JOIN subjects s ON e.subject_id = s.id
        LEFT JOIN faculty_details f ON s.faculty_id = f.id
        WHERE ss.enrollment_no = %s
    """, (enrollment_no,))
    subjects = _cur.fetchall()

    # 3. Get exam statistics
    _cur.execute("""
        SELECT 
            COUNT(e.course_code) AS total_exams,
            SUM(CASE WHEN ea.completed = 1 THEN 1 ELSE 0 END) AS completed_exams
        FROM exams e
        JOIN student_subjects ss ON e.course_code = ss.course_code
        LEFT JOIN exam_attempts ea 
            ON ea.course_code = e.course_code AND ea.enrollment_no = %s
        WHERE ss.enrollment_no = %s
    """, (enrollment_no, enrollment_no))
    exam_stats = _cur.fetchone()

    total_exams = exam_stats["total_exams"] if exam_stats and exam_stats["total_exams"] else 0
    completed_exams = exam_stats["completed_exams"] if exam_stats and exam_stats["completed_exams"] else 0
    upcoming_exams_count = total_exams - completed_exams

    # 4. FETCH NEWLY SCHEDULED EXAMS (Live feature) - Filtered by enrollment, content, AND NOT COMPLETED ATTEMPT
    _cur.execute("""
        SELECT se.*, fd.full_name AS faculty_name 
        FROM scheduled_exams se
        JOIN student_subjects ss ON se.course_code = ss.course_code
        JOIN exams e ON se.course_code = e.course_code
        JOIN users u ON se.created_by = u.id
        LEFT JOIN faculty_details fd ON fd.user_id = u.id
        LEFT JOIN exam_attempts ea ON se.course_code = ea.course_code AND ea.enrollment_no = ss.enrollment_no
        WHERE ss.enrollment_no = %s 
          AND se.exam_date >= CURRENT_DATE 
          AND se.status != 'completed'
          AND (ea.completed IS NULL OR ea.completed = 0)
        ORDER BY se.exam_date ASC, se.start_time ASC
        LIMIT 5
    """, (enrollment_no,))
    live_scheduled = [dict(r) for r in _cur.fetchall()]

    conn.close()

    return render_template(
        "student_dashboard.html",
        student=dict(student),
        subjects=[dict(row) for row in subjects],
        total_exams=total_exams,
        completed_exams=completed_exams,
        upcoming_exams=upcoming_exams_count,
        live_scheduled=live_scheduled
    )


@student_bp.route("/student/exams")
def student_exams():
    if not session.get("user_id") or session.get("role") != "student":
        return redirect(url_for("auth_bp.student_login"))
    
    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("""
        SELECT 
            s.subject_name,
            f.full_name AS faculty_name,
            e.course_code,
            e.exam_date,
            e.start_time,
            e.end_time,
            e.duration_minutes,
            COALESCE(ea.completed, 0) AS is_completed,
            ea.score,
            (SELECT COUNT(*) FROM exam_blacklist eb WHERE eb.course_code = e.course_code AND eb.enrollment_no = ss.enrollment_no) as is_blacklisted
        FROM student_subjects ss
        JOIN exams e ON ss.course_code = e.course_code
        JOIN subjects s ON e.subject_id = s.id
        LEFT JOIN faculty_details f ON s.faculty_id = f.id
        LEFT JOIN exam_attempts ea ON e.course_code = ea.course_code AND ss.enrollment_no = ea.enrollment_no
        WHERE ss.enrollment_no = (SELECT enrollment_no FROM student_details WHERE user_id = %s)
    """, (session["user_id"],))
    exams_data = _cur.fetchall()
    
    # Check scheduling status
    now = datetime.now()
    exams_with_status = []
    for row in exams_data:
        d = dict(row)
        d['status'] = 'open'
        
        def parse_dt(dt_str):
            if not dt_str: return None
            try:
                s = dt_str.replace('T', ' ')
                if len(s) > 19: s = s[:19]
                return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except:
                try: return datetime.strptime(dt_str.replace('T', ' '), "%Y-%m-%d %H:%M")
                except: return None

        start = parse_dt(d['start_time'])
        end = parse_dt(d['end_time'])

        if start and now < start:
            d['status'] = 'upcoming'
        elif end and now > end:
            d['status'] = 'expired'
        exams_with_status.append(d)
    
    _cur.execute("SELECT enrollment_no, full_name, branch_code AS branch, semester FROM student_details WHERE user_id = %s", (session["user_id"],))
    student = _cur.fetchone()
    conn.close()

    return render_template("student_exams.html", exams=exams_with_status, student=dict(student) if student else {})

@student_bp.route("/exam")
def exam_page():
    if not session.get("user_id"):
        return redirect(url_for("auth_bp.student_login"))
    
    course_code = request.args.get("course_code")
    if not course_code:
        flash("Exam not specified.", "danger")
        return redirect(url_for("student_bp.student_dashboard"))

    conn = get_connection()
    _cur = conn.cursor()
    
    # Fetch student details
    _cur.execute("SELECT enrollment_no FROM student_details WHERE user_id = %s", (session["user_id"],))
    student = _cur.fetchone()
    if not student:
        conn.close()
        flash("Student profile not found.", "danger")
        return redirect(url_for("student_bp.student_dashboard"))
        
    enrollment_no = student["enrollment_no"]

    _cur.execute("SELECT * FROM exams WHERE course_code = %s", (course_code,))
    exam = _cur.fetchone()
    if not exam:
        conn.close()
        flash("Exam not found.", "danger")
        return redirect(url_for("student_bp.student_dashboard"))
    
    # Check blacklist
    _cur.execute("SELECT id FROM exam_blacklist WHERE course_code = %s AND enrollment_no = %s", (course_code, enrollment_no))
    if _cur.fetchone():
        conn.close()
        flash("You are blacklisted from this exam. Please contact your faculty.", "danger")
        return redirect(url_for("student_bp.student_dashboard"))

    # Check enrollment
    _cur.execute("SELECT enrollment_no FROM student_subjects WHERE enrollment_no = %s AND course_code = %s", (enrollment_no, course_code))
    if not _cur.fetchone():
        conn.close()
        flash("You are not enrolled for this specific course/exam.", "danger")
        return redirect(url_for("student_bp.student_dashboard"))
        
    now = datetime.now()
    
    # Check results saving / Live monitoring initialization
    _cur.execute("SELECT id, completed FROM exam_attempts WHERE enrollment_no = %s AND course_code = %s", (enrollment_no, course_code))
    attempt = _cur.fetchone()
    
    if not attempt:
        # Create an 'In-Progress' attempt for live proctoring
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        _cur.execute("INSERT INTO exam_attempts (enrollment_no, course_code, score, completed, attempt_time) VALUES (%s, %s, 0, 0, %s)", 
                     (enrollment_no, course_code, now_str))
        conn.commit()
        log_event(
            event_type=EXAM_STARTED,
            description=f"Student '{enrollment_no}' started exam '{course_code}'.",
            actor_id=session["user_id"],
            actor_role="student",
            target_type="exam",
            target_id=course_code,
            metadata={"enrollment_no": enrollment_no},
            request=request
        )
    elif attempt["completed"] == 1:
        conn.close()
        flash("You have already completed this exam.", "warning")
        return redirect(url_for("student_bp.student_dashboard"))

    # Strict scheduling check
    def parse_dt(dt_str):
        if not dt_str: return None
        try:
            s = dt_str.replace('T', ' ')
            if len(s) > 19: s = s[:19]
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except:
            try: return datetime.strptime(dt_str.replace('T', ' '), "%Y-%m-%d %H:%M")
            except: return None

    start = parse_dt(exam["start_time"])
    end = parse_dt(exam["end_time"])

    if start and now < start:
        conn.close()
        flash(f"This exam is scheduled to start at {exam['start_time']}.", "warning")
        return redirect(url_for("student_bp.student_dashboard"))
    if end and now > end:
        conn.close()
        flash("This exam has already expired.", "danger")
        return redirect(url_for("student_bp.student_dashboard"))

    subject_id = exam["subject_id"]
    _cur.execute("SELECT subject_name FROM subjects WHERE id = %s", (subject_id,))
    subject = _cur.fetchone()
    subject_name = subject["subject_name"] if subject else "Unknown Subject"

    try:
        # Check if specific questions are assigned to this exam
        _cur.execute("SELECT COUNT(*) as count FROM exam_questions WHERE course_code = %s", (course_code,))
        exam_q_count = _cur.fetchone()["count"]
        
        if exam_q_count > 0:
            _cur.execute("""
                SELECT q.*, eq.section 
                FROM questions q
                JOIN exam_questions eq ON q.id = eq.question_id
                WHERE eq.course_code = %s
            """, (course_code,))
            questions_db = _cur.fetchall()
        else:
            _cur.execute("SELECT * FROM questions WHERE subject_id = %s", (subject_id,))
            questions_db = _cur.fetchall()
    except Exception:
        questions_db = []

    questions = []
    for q in questions_db:
        _cur.execute("SELECT id, option_text, is_correct FROM options WHERE question_id = %s", (q["id"],))
        options_db = _cur.fetchall()
        
        q_dict = dict(q)
        q_dict["options"] = [dict(row) for row in options_db]
        questions.append(q_dict)

    conn.close()
    return render_template("exam.html", subject_name=subject_name, exam=dict(exam), questions=questions)


@student_bp.route("/exam/submit", methods=["POST"])
def submit_exam():
    if not session.get("user_id"):
        return {"success": False, "message": "Unauthorized"}, 401

    data = request.json
    course_code = data.get("course_code")
    score = data.get("score")

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT enrollment_no FROM student_details WHERE user_id = %s", (session["user_id"],))
    student = _cur.fetchone()
    
    if not student:
        conn.close()
        return {"success": False, "message": "Student not found"}, 404

    # Check if attempt already exists and update or create
    _cur.execute("SELECT id, completed FROM exam_attempts WHERE enrollment_no = %s AND course_code = %s", 
                 (student["enrollment_no"], course_code))
    existing_attempt = _cur.fetchone()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if existing_attempt:
        if existing_attempt["completed"] == 1:
            conn.close()
            log_event(
                event_type=DUPLICATE_SUBMIT,
                description=f"Student '{student['enrollment_no']}' attempted to submit already completed exam '{course_code}'.",
                actor_id=session["user_id"],
                actor_role="student",
                target_type="exam",
                target_id=course_code,
                request=request
            )
            return {"success": False, "message": "Exam already completed"}, 400
            
        _cur.execute("UPDATE exam_attempts SET score = %s, completed = 1, attempt_time = %s WHERE id = %s", (score, now, existing_attempt["id"]))
    else:
        _cur.execute("INSERT INTO exam_attempts (enrollment_no, course_code, score, completed, attempt_time) VALUES (%s, %s, %s, 1, %s)", 
                     (student["enrollment_no"], course_code, score, now))
    
    conn.commit()
    conn.close()
    
    log_event(
        event_type=EXAM_SUBMITTED,
        description=f"Student '{student['enrollment_no']}' submitted exam '{course_code}' with score {score}.",
        actor_id=session["user_id"],
        actor_role="student",
        target_type="exam",
        target_id=course_code,
        metadata={"score": score},
        request=request
    )

    return {"success": True}


@student_bp.route("/exam/report_progress", methods=["POST"])
def report_progress():
    if not session.get("user_id"):
        return {"success": False, "message": "Unauthorized"}, 401

    data = request.json
    course_code = data.get("course_code")
    score = data.get("score")
    
    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT enrollment_no FROM student_details WHERE user_id = %s", (session["user_id"],))
    student = _cur.fetchone()
    
    if student:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _cur.execute("""
            UPDATE exam_attempts 
            SET score = %s, attempt_time = %s 
            WHERE enrollment_no = %s AND course_code = %s AND completed = 0
        """, (score, now, student["enrollment_no"], course_code))
        conn.commit()
    
    conn.close()
    return {"success": True}

@student_bp.route("/exam/blacklist", methods=["POST"])
def blacklist_student():
    if not session.get("user_id"):
        return {"success": False, "message": "Unauthorized"}, 401
        
    data = request.json
    course_code = data.get("course_code")
    
    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT enrollment_no FROM student_details WHERE user_id = %s", (session["user_id"],))
    student = _cur.fetchone()
    if not student:
        conn.close()
        return {"success": False, "message": "Student not found"}, 404
        
    # Check if already blacklisted
    _cur.execute("SELECT id FROM exam_blacklist WHERE course_code = %s AND enrollment_no = %s", (course_code, student["enrollment_no"]))
    existing = _cur.fetchone()
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not existing:
        _cur.execute("INSERT INTO exam_blacklist (course_code, enrollment_no) VALUES (%s, %s)", (course_code, student["enrollment_no"]))
        log_event(
            event_type=BLACKLIST_ADDED,
            description=f"Student '{student['enrollment_no']}' self-blacklisted from exam '{course_code}' during attempt.",
            actor_id=session["user_id"],
            actor_role="student",
            target_type="exam",
            target_id=course_code,
            metadata={"enrollment_no": student["enrollment_no"]},
            request=request
        )
    
    # Also mark as completed with 0 score (or keep existing)
    _cur.execute("SELECT id FROM exam_attempts WHERE course_code = %s AND enrollment_no = %s", (course_code, student["enrollment_no"]))
    existing_attempt = _cur.fetchone()
    
    if not existing_attempt:
        _cur.execute("INSERT INTO exam_attempts (enrollment_no, course_code, score, completed, attempt_time) VALUES (%s, %s, 0, 1, %s)", 
                     (student["enrollment_no"], course_code, now))
    
    conn.commit()
    conn.close()
    
    return {"success": True}

@student_bp.route("/result")
def result_page():
    if not session.get("user_id"):
        return redirect(url_for("auth_bp.student_login"))
    return render_template("result.html")


# ═══════════════════════════════════════════════════════════════
#  AVAILABLE EXAMS  –  Student views faculty-scheduled exams
# ═══════════════════════════════════════════════════════════════

@student_bp.route("/available_exams")
def available_exams():
    if not session.get("user_id") or session.get("role") != "student":
        return redirect(url_for("auth_bp.student_login"))

    conn = get_connection()
    _cur = conn.cursor()

    # Student profile
    _cur.execute(
        "SELECT enrollment_no, full_name, branch_code, semester FROM student_details WHERE user_id = %s",
        (session["user_id"],)
    )
    student = _cur.fetchone()

    # Fetch scheduled exams for which the student is enrolled (by course_code)
    # AND that have exam content, today or in future, and NOT COMPLETED.
    _cur.execute("""
        SELECT se.id, se.subject, se.exam_date, se.start_time, se.end_time,
               se.duration, se.total_marks, se.status, se.course_code,
               fd.full_name AS faculty_name,
               (SELECT COUNT(*) FROM exam_blacklist eb WHERE eb.course_code = se.course_code AND eb.enrollment_no = ss.enrollment_no) as is_blacklisted
        FROM scheduled_exams se
        JOIN student_subjects ss ON se.course_code = ss.course_code
        JOIN exams e ON se.course_code = e.course_code
        JOIN users u ON se.created_by = u.id
        LEFT JOIN faculty_details fd ON fd.user_id = u.id
        LEFT JOIN exam_attempts ea ON se.course_code = ea.course_code AND ea.enrollment_no = ss.enrollment_no
        WHERE ss.enrollment_no = %s
          AND se.exam_date >= CURRENT_DATE
          AND se.status != 'completed'
          AND (ea.completed IS NULL OR ea.completed = 0)
        ORDER BY se.exam_date ASC, se.start_time ASC
    """, (student["enrollment_no"],))
    raw = _cur.fetchall()
    conn.close()

    now = datetime.now()
    exams = []
    for row in raw:
        d = dict(row)
        # Build combined datetime for comparison
        try:
            dt_start = datetime.combine(d["exam_date"], d["start_time"])
            dt_end   = datetime.combine(d["exam_date"], d["end_time"])
        except Exception:
            dt_start = dt_end = None

        if dt_start and dt_end:
            if now < dt_start:
                d["btn_state"] = "upcoming"
            elif dt_start <= now <= dt_end:
                d["btn_state"] = "active"
            else:
                d["btn_state"] = "expired"
        else:
            d["btn_state"] = "upcoming"

        # Format for display
        d["exam_date_fmt"] = d["exam_date"].strftime("%d-%m-%Y") if hasattr(d["exam_date"], "strftime") else str(d["exam_date"])
        d["start_time_fmt"] = d["start_time"].strftime("%H:%M") if hasattr(d["start_time"], "strftime") else str(d["start_time"])[:5]
        d["end_time_fmt"]   = d["end_time"].strftime("%H:%M")   if hasattr(d["end_time"],   "strftime") else str(d["end_time"])[:5]
        d["start_iso"] = dt_start.isoformat() if dt_start else ""
        d["end_iso"]   = dt_end.isoformat()   if dt_end   else ""
        exams.append(d)

    return render_template(
        "available_exams.html",
        student=dict(student) if student else {},
        exams=exams,
        now_iso=now.isoformat()
    )


@student_bp.route("/start_exam/<int:exam_id>")
def start_exam(exam_id):
    """Guard-route: verifies timing before allowing exam entry."""
    if not session.get("user_id") or session.get("role") != "student":
        return redirect(url_for("auth_bp.student_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT * FROM scheduled_exams WHERE id = %s", (exam_id,))
    exam = _cur.fetchone()

    if not exam:
        conn.close()
        flash("Exam not found.", "danger")
        return redirect(url_for("student_bp.available_exams"))

    now = datetime.now()
    try:
        dt_start = datetime.combine(exam["exam_date"], exam["start_time"])
        dt_end   = datetime.combine(exam["exam_date"], exam["end_time"])
    except Exception:
        conn.close()
        flash("Invalid exam schedule.", "danger")
        return redirect(url_for("student_bp.available_exams"))

    if now < dt_start:
        conn.close()
        flash(f"This exam hasn't started yet. It starts at {exam['start_time'].strftime('%H:%M')} on {exam['exam_date'].strftime('%d-%m-%Y')}.", "warning")
        return redirect(url_for("student_bp.available_exams"))

    if now > dt_end:
        conn.close()
        flash("This exam has already ended.", "danger")
        return redirect(url_for("student_bp.available_exams"))

    # Exam is live — redirect to exam page with the linked content
    course_code = exam.get('course_code')
    if not course_code:
        conn.close()
        flash("This schedule is not linked to any exam content. Please contact faculty.", "danger")
        return redirect(url_for("student_bp.available_exams"))

    # Verify that exam content (exams entry) actually exists
    _cur = conn.cursor()
    _cur.execute("SELECT 1 FROM exams WHERE course_code = %s", (course_code,))
    if not _cur.fetchone():
        conn.close()
        flash("Exam content (questions) has not been uploaded for this schedule yet.", "danger")
        return redirect(url_for("student_bp.available_exams"))

    # Check enrollment here too for better UX
    _cur = conn.cursor()
    _cur.execute("SELECT enrollment_no FROM student_subjects WHERE enrollment_no = (SELECT enrollment_no FROM student_details WHERE user_id = %s) AND course_code = %s", (session["user_id"], course_code))
    if not _cur.fetchone():
        conn.close()
        flash("You are not enrolled for this specific course/exam subjects.", "danger")
        return redirect(url_for("student_bp.available_exams"))

    conn.close()
    flash(f"Entering {exam['subject']} exam. Good luck!", "success")
    return redirect(url_for("student_bp.exam_page", course_code=course_code))

