from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response, jsonify
from database import get_connection, update_scheduled_exam_statuses
from datetime import datetime, date as _date
from routes.logger import log_event, EXAM_CREATED, EXAM_DELETED, EXAM_EDITED, EXAM_DUPLICATED, BLACKLIST_ADDED, BLACKLIST_REMOVED, QUESTION_ADDED, CSV_UPLOADED
import os
import random
import io
import pandas as pd
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

faculty_bp = Blueprint('faculty_bp', __name__)

# Load environment variables from .env file
load_dotenv()

# LLM Configuration — FEATURE ON HOLD
# Stub fallbacks while Gemini feature is on hold
def estimate_difficulty_llm(text):
    """Stub: returns 'medium' while Gemini feature is on hold."""
    return "medium"

def select_questions_llm(exam_name, candidates, count, difficulty):
    """Stub: returns random selection while Gemini feature is on hold."""
    return random.sample([c["id"] for c in candidates], min(len(candidates), count))

def _process_questions_file(subject_id, file, forced_difficulty=None):
    """Refined helper to process question CSV/XLSX and return list of (question_id, section) tuples."""

    # Read the file into a DataFrame
    filename = file.filename.lower()
    if filename.endswith('.csv'):
        # Try different encodings for CSV
        try:
            content = file.stream.read().decode("utf-8-sig")
            df = pd.read_csv(io.StringIO(content))
        except UnicodeDecodeError:
            file.stream.seek(0)
            content = file.stream.read().decode("latin-1")
            df = pd.read_csv(io.StringIO(content))
    elif filename.endswith('.xlsx'):
        df = pd.read_excel(file)
    else:
        raise ValueError("Unsupported file format. Please use .csv or .xlsx")

    # Normalize column names to lowercase for flexible mapping
    df.columns = [str(c).strip().lower() for c in df.columns]
    
    conn = get_connection()
    cursor = conn.cursor()
    processed_data = [] # List of tuples: (q_id, section)
    
    for _, row in df.iterrows():
        # Flexible column mapping using priority dict
        r_dict = row.to_dict()
        
        q_text = str(r_dict.get('question_text') or r_dict.get('questions') or r_dict.get('question') or '').strip()
        if not q_text or q_text == 'nan': continue
        
        marks = int(r_dict.get('marks', 1))
        section = str(r_dict.get('section', 'A')).strip().upper()
        if section not in ['A', 'B', 'C']: section = 'A'
        
        # Priority order: forced_difficulty > 'difficulty' column > default 'medium'
        difficulty = forced_difficulty
        if not difficulty:
            difficulty = str(r_dict.get('difficulty') or r_dict.get('level') or '').strip().lower()
            
        if difficulty not in ['easy', 'medium', 'hard']:
            difficulty = 'medium'
        
        # Determine Question Type (MCQ by default, Integer if 'integer_answer' is present)
        int_ans = r_dict.get('correct_integer_answer') or r_dict.get('integer_answer')
        q_type = 'Integer' if pd.notna(int_ans) else 'MCQ'
        
        cursor.execute("""
            INSERT INTO questions (subject_id, question_text, question_type, marks, difficulty, correct_integer_answer) 
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (subject_id, q_text, q_type, marks, difficulty, int(int_ans) if q_type == 'Integer' else None))
        
        q_id = cursor.fetchone()["id"]
        processed_data.append((q_id, section))
        
        if q_type == 'MCQ':
            # Option mapping for MCQ
            correct = str(r_dict.get('correct_answer') or r_dict.get('correct_option') or r_dict.get('answer') or '1').strip()
            
            for i in range(1, 6): # Support up to 5 options
                opt_key = f'option_{i}' if f'option_{i}' in r_dict else f'option{i}'
                opt_text = str(r_dict.get(opt_key) or '').strip()
                
                if opt_text and opt_text != 'nan':
                    # Check if 'correct' equals the index (1,2,3...) or the text itself
                    is_correct = 1 if (correct == str(i) or correct == opt_text) else 0
                    cursor.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (%s, %s, %s)", 
                                   (q_id, opt_text, is_correct))
    
    conn.commit()
    conn.close()
    return processed_data


@faculty_bp.route("/faculty/dashboard")
def faculty_dashboard():
    u_id = session.get('user_id')
    role = session.get('role')
    print(f"[DEBUG] Entry /faculty/dashboard: user_id={u_id}, role={role}")
    
    if not u_id:
        print("[DEBUG] Redirecting to login: user_id is MISSING")
        return redirect(url_for("auth_bp.faculty_login"))
    if role == "student":
        print("[DEBUG] Redirecting to student dashboard: role is student")
        return redirect(url_for("student_bp.student_dashboard"))
    if role != "faculty":
        print(f"[DEBUG] Redirecting to login: role is NOT faculty (role={role})")
        return redirect(url_for("auth_bp.faculty_login"))
    
    # Removed session.pop('_flashes')
    update_scheduled_exam_statuses()

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()

    if not faculty:
        conn.close()
        # Surgical clearing – avoid session.clear() to preserve flashes
        session.pop("user_id", None)
        session.pop("role", None)
        print(f"[DEBUG] Redirecting to login: Faculty PROFILE NOT FOUND for user_id={u_id}")
        flash("Faculty profile not found. Please contact administration.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    _cur.execute("SELECT COUNT(*) as count FROM student_details")
    total_students = _cur.fetchone()["count"]
    
    _cur.execute("SELECT id, subject_name, branch, semester FROM subjects WHERE faculty_id = %s", (faculty["id"],))
    subjects = _cur.fetchall()
    subject_ids = [s["id"] for s in subjects]

    # Fetch live scheduled exams data
    _cur.execute("SELECT * FROM scheduled_exams WHERE created_by = %s ORDER BY exam_date ASC, start_time ASC", (session["user_id"],))
    live_scheduled_exams = _cur.fetchall()
    live_scheduled_count = len(live_scheduled_exams)

    active_exams_count = 0
    recent_exams = []
    pending_results = 0
    total_results = 0
    recent_activity = []
    score_dist = {"excellent": 0, "passed": 0, "failed": 0}
    at_risk_students = []
    class_avg = 0.0

    if subject_ids:
        placeholders = ','.join('%s' for _ in subject_ids)
        _cur.execute(f"SELECT COUNT(*) as count FROM exams WHERE subject_id IN ({placeholders})", tuple(subject_ids))
        active_exams_count = _cur.fetchone()["count"]

        _cur.execute(f"""
            SELECT e.course_code, e.exam_name, e.exam_date, e.total_marks, e.duration_minutes,
                   s.id AS subject_id, s.subject_name, s.branch, s.semester,
                   e.pass_percentage,
                   (SELECT COUNT(*) FROM exam_attempts ea2 WHERE ea2.course_code = e.course_code AND ea2.completed = 1) AS attempt_count,
                   (SELECT COUNT(*) FROM questions WHERE subject_id = s.id) AS question_count
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
            ORDER BY e.exam_date DESC LIMIT 5
        """, tuple(subject_ids))
        recent_exams = _cur.fetchall()

        _cur.execute(f"""
            SELECT COUNT(*) as count FROM exam_attempts ea
            JOIN exams e ON ea.course_code = e.course_code
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
        """, tuple(subject_ids))
        total_results = _cur.fetchone()["count"]

        _cur.execute(f"""
            SELECT COUNT(DISTINCT e.course_code) as count FROM exams e
            JOIN exam_attempts ea ON e.course_code = ea.course_code
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
        """, tuple(subject_ids))
        exams_with_attempts = _cur.fetchone()["count"]
        pending_results = max(0, active_exams_count - exams_with_attempts)

        _cur.execute(f"""
            SELECT sd.full_name, e.exam_name, ea.score, e.total_marks, ea.completed, s.subject_name
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            JOIN exams e ON ea.course_code = e.course_code
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
            ORDER BY ea.id DESC LIMIT 8
        """, tuple(subject_ids))
        recent_activity = _cur.fetchall()

        # Score distribution for charts
        _cur.execute(f"""
            SELECT ea.score, e.total_marks
            FROM exam_attempts ea
            JOIN exams e ON ea.course_code = e.course_code
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1 AND e.total_marks > 0
        """, tuple(subject_ids))
        all_scores = _cur.fetchall()

        total_pct_sum = 0.0
        for row in all_scores:
            pct = (row["score"] / row["total_marks"]) * 100
            total_pct_sum += pct
            if pct >= 75:
                score_dist["excellent"] += 1
            elif pct >= 40:
                score_dist["passed"] += 1
            else:
                score_dist["failed"] += 1

        if all_scores:
            class_avg = round(total_pct_sum / len(all_scores), 1)

        # At-risk students (last attempt score < 40%)
        _cur.execute(f"""
            SELECT sd.full_name, sd.enrollment_no, ea.score, e.total_marks, e.exam_name, s.subject_name,
                   ROUND(CAST(CAST(ea.score AS FLOAT)/e.total_marks*100 AS NUMERIC), 1) AS pct
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            JOIN exams e ON ea.course_code = e.course_code
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1 AND e.total_marks > 0
              AND CAST(ea.score AS FLOAT)/e.total_marks*100 < 40
            ORDER BY pct ASC LIMIT 5
        """, tuple(subject_ids))
        at_risk_raw = _cur.fetchall()
        at_risk_students = [dict(r) for r in at_risk_raw]

    conn.close()
    return render_template("faculty_dashboard.html",
        faculty=dict(faculty),
        total_students=total_students,
        active_exams=active_exams_count,
        live_scheduled_count=live_scheduled_count,
        recent_exams=[dict(r) for r in recent_exams],
        subjects=[dict(r) for r in subjects],
        pending_results=pending_results,
        total_results=total_results,
        recent_activity=[dict(r) for r in recent_activity],
        score_dist=score_dist,
        at_risk_students=at_risk_students,
        class_avg=class_avg,
        live_scheduled_exams=[dict(r) for r in live_scheduled_exams],
    )


@faculty_bp.route("/faculty/exams")
def faculty_exams():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    if not faculty:
        conn.close()
        session.pop("user_id", None)
        session.pop("role", None)
        flash("Faculty profile not found.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))
        
    _cur.execute("SELECT id, subject_name, subject_code, branch, semester FROM subjects WHERE faculty_id = %s", (faculty["id"],))
    subjects = _cur.fetchall()
    subject_ids = [s["id"] for s in subjects]

    all_exams = []
    if subject_ids:
        placeholders = ','.join('%s' for _ in subject_ids)
        _cur.execute(f"""
            SELECT e.course_code, e.exam_name, e.exam_date, e.start_time, e.end_time, e.total_marks, e.duration_minutes,
                   e.pass_percentage, e.results_published,
                   s.id AS subject_id, s.subject_name, s.branch, s.semester, s.subject_code,
                   (SELECT COUNT(*) FROM exam_attempts ea WHERE ea.course_code = e.course_code AND ea.completed = 1) AS attempt_count,
                   (SELECT COUNT(*) FROM exam_questions eq WHERE eq.course_code = e.course_code) AS question_count
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
            ORDER BY e.exam_date DESC
        """, tuple(subject_ids))
        all_exams_raw = _cur.fetchall()
        
        for e in all_exams_raw:
            ed = dict(e)
            # Fetch difficulty breakdown for this specific exam
            _cur.execute("""
                SELECT 
                    SUM(CASE WHEN q.difficulty = 'easy' THEN 1 ELSE 0 END) as easy_q,
                    SUM(CASE WHEN q.difficulty = 'medium' THEN 1 ELSE 0 END) as medium_q,
                    SUM(CASE WHEN q.difficulty = 'hard' THEN 1 ELSE 0 END) as hard_q,
                    SUM(CASE WHEN q.difficulty IS NULL OR q.difficulty = '' OR q.difficulty = 'error_api' OR q.difficulty = 'error_no_key' THEN 1 ELSE 0 END) as unknown_q
                FROM exam_questions eq
                JOIN questions q ON eq.question_id = q.id
                WHERE eq.course_code = %s
            """, (e["course_code"],))
            diff_stats = _cur.fetchone()
            
            ed["difficulty_dist"] = {
                "easy": diff_stats["easy_q"] or 0,
                "medium": diff_stats["medium_q"] or 0,
                "hard": diff_stats["hard_q"] or 0,
                "unknown": diff_stats["unknown_q"] or 0
            }
            all_exams.append(ed)

    conn.close()
    return render_template("faculty_exams.html", faculty=faculty, subjects=subjects, all_exams=all_exams)

@faculty_bp.route("/faculty/create_exam", methods=["POST"])
def create_exam():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))
        
    subject_id = request.form.get("subject_id")
    course_code = request.form.get("course_code")
    exam_name = request.form.get("exam_name", "").strip()
    total_marks = request.form.get("total_marks", 100, type=int)
    duration = request.form.get("duration", 60, type=int)
    pass_percentage = request.form.get("pass_percentage", 40, type=int)
    
    if not course_code:
        flash("Course Code is required.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
    
    exam_date_input = request.form.get("exam_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    
    auto_easy = request.form.get("auto_easy", 0, type=int)
    auto_medium = request.form.get("auto_medium", 0, type=int)
    auto_hard = request.form.get("auto_hard", 0, type=int)
    
    exam_date = exam_date_input if exam_date_input else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not subject_id or not exam_name:
        flash("Subject and Exam Name are required.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
        
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO exams (course_code, subject_id, exam_name, exam_date, start_time, end_time, total_marks, duration_minutes, pass_percentage)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (course_code, subject_id, exam_name, exam_date, start_time or None, end_time or None, total_marks, duration, pass_percentage))
        
        # Handle Bulk File Upload if present
        uploaded_info = [] # Store (q_id, section)
        if 'csv_file' in request.files:
            file = request.files['csv_file']
            if file and (file.filename.lower().endswith('.csv') or file.filename.lower().endswith('.xlsx')):
                new_info = _process_questions_file(subject_id, file)
                uploaded_info.extend(new_info)
                for qid, section in new_info:
                    cursor.execute("INSERT INTO exam_questions (course_code, question_id, section) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (course_code, qid, section))
        
        uploaded_q_ids = [info[0] for info in uploaded_info]

        if auto_easy > 0 or auto_medium > 0 or auto_hard > 0:
            def fetch_random_q(diff, count):
                if count <= 0: return []
                exclude_ids = uploaded_q_ids if uploaded_q_ids else [-1]
                placeholders = ','.join('%s' for _ in exclude_ids)
                cursor.execute(f"SELECT id FROM questions WHERE subject_id = %s AND difficulty = %s AND id NOT IN ({placeholders})", (subject_id, diff, *exclude_ids))
                qs = cursor.fetchall()
                q_ids = [q["id"] for q in qs]
                if not q_ids: return []
                return random.sample(q_ids, min(len(q_ids), count))
                
            # We want to organize these into logical sections: Easy -> A, Medium -> B, Hard -> C
            picks = []
            picks.extend([(qid, "A") for qid in fetch_random_q("easy", auto_easy)])
            picks.extend([(qid, "B") for qid in fetch_random_q("medium", auto_medium)])
            picks.extend([(qid, "C") for qid in fetch_random_q("hard", auto_hard)])
            
            for qid, sec in picks:
                cursor.execute("INSERT INTO exam_questions (course_code, question_id, section) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (course_code, qid, sec))

        # 4. Auto-enroll students matching the subject's branch/semester
        cursor.execute("SELECT branch, semester FROM subjects WHERE id = %s", (subject_id,))
        sub_info = cursor.fetchone()
        if sub_info:
            cursor.execute("""
                INSERT INTO student_subjects (enrollment_no, course_code)
                SELECT enrollment_no, %s
                FROM student_details
                WHERE branch_code = %s AND semester = %s
                ON CONFLICT DO NOTHING
            """, (course_code, sub_info['branch'], sub_info['semester']))
            enrolled_count = cursor.rowcount
            print(f"Auto-enrolled {enrolled_count} students to exam {course_code}")

        conn.commit()
        conn.close()
        log_event(
            event_type=EXAM_CREATED,
            description=f"Exam '{exam_name}' ({course_code}) was created.",
            actor_id=session["user_id"],
            actor_role="faculty",
            target_type="exam",
            target_id=course_code,
            metadata={"subject_id": subject_id, "total_marks": total_marks},
            request=request
        )
        flash("Exam created successfully!", "success")

    except Exception as e:
        flash(f"Error creating exam: {str(e)}", "danger")

    return redirect(url_for("faculty_bp.faculty_exams"))

@faculty_bp.route("/faculty/exam/delete/<string:course_code>", methods=["POST"])
def delete_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Verify ownership
        _cur.execute("""
            SELECT e.course_code FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
        """, (course_code, session["user_id"]))
        exam = _cur.fetchone()

        if exam:
            _cur.execute("DELETE FROM exams WHERE course_code = %s", (course_code,))
            conn.commit()
            log_event(
                event_type=EXAM_DELETED,
                description=f"Exam '{course_code}' was deleted.",
                actor_id=session["user_id"],
                actor_role="faculty",
                target_type="exam",
                target_id=course_code,
                request=request
            )
            flash("Exam deleted successfully.", "success")
        else:
            flash("Exam not found or access denied.", "danger")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_exams"))

@faculty_bp.route("/faculty/exam/edit/<string:course_code>", methods=["POST"])
def edit_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    exam_name = request.form.get("exam_name", "").strip()
    total_marks = request.form.get("total_marks", 0, type=int)
    pass_percentage = request.form.get("pass_percentage", 0, type=int)
    duration_minutes = request.form.get("duration_minutes", 0, type=int)
    start_time_str = request.form.get("start_time", "").strip()
    end_time_str = request.form.get("end_time", "").strip()

    if not all([exam_name, total_marks, pass_percentage, duration_minutes, start_time_str, end_time_str]):
        flash("All fields are required.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))

    try:
        start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
        end_time = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
    except:
         flash("Invalid date format. Use YYYY-MM-DD HH:MM", "danger")
         return redirect(url_for("faculty_bp.faculty_exams"))

    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Verify ownership
        _cur.execute("""
            SELECT e.course_code FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
        """, (course_code, session["user_id"]))
        exam = _cur.fetchone()

        if exam:
            _cur.execute("""
                UPDATE exams SET exam_name = %s, total_marks = %s, pass_percentage = %s, 
                                 duration_minutes = %s, start_time = %s, end_time = %s
                WHERE course_code = %s
            """, (exam_name, total_marks, pass_percentage, duration_minutes, start_time, end_time, course_code))
            conn.commit()
            log_event(
                event_type=EXAM_EDITED,
                description=f"Exam '{exam_name}' ({course_code}) was edited.",
                actor_id=session["user_id"],
                actor_role="faculty",
                target_type="exam",
                target_id=course_code,
                request=request
            )
            flash("Exam updated successfully.", "success")
        else:
            flash("Exam not found or access denied.", "danger")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_exams"))

@faculty_bp.route("/faculty/exam/duplicate/<string:course_code>", methods=["POST"])
def duplicate_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
    
    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Get faculty ID
        _cur.execute("SELECT id FROM faculty_details WHERE user_id = %s", (session["user_id"],))
        faculty = _cur.fetchone()
        
        # Get original exam
        _cur.execute("""
            SELECT e.* FROM exams e 
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = %s
        """, (course_code, faculty["id"]))
        exam = _cur.fetchone()
        
        if not exam:
            flash("Exam not found or access denied.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))
            
        # Create new course code
        new_code = f"{exam['course_code']}-COPY-{random.randint(100, 999)}"
        new_name = f"{exam['exam_name']} (Copy)"
        
        _cur.execute("""
            INSERT INTO exams (course_code, subject_id, exam_name, exam_date, start_time, end_time, total_marks, duration_minutes, pass_percentage)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (new_code, exam["subject_id"], new_name, exam["exam_date"], exam["start_time"], exam["end_time"], 
              exam["total_marks"], exam["duration_minutes"], exam["pass_percentage"]))
        
        # Duplicate questions
        _cur.execute("SELECT question_id, section FROM exam_questions WHERE course_code = %s", (course_code,))
        questions = _cur.fetchall()
        for q in questions:
            _cur.execute("INSERT INTO exam_questions (course_code, question_id, section) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (new_code, q["question_id"], q["section"]))
            
        conn.commit()
        log_event(
            event_type=EXAM_DUPLICATED,
            description=f"Exam '{course_code}' was duplicated into '{new_code}'.",
            actor_id=session["user_id"],
            actor_role="faculty",
            target_type="exam",
            target_id=new_code,
            metadata={"original_course_code": course_code},
            request=request
        )
        flash(f"Exam duplicated as '{new_name}' with code '{new_code}'", "success")
    except Exception as e:
        flash(f"Error duplicating exam: {str(e)}", "danger")
    finally:
        conn.close()
    
    return redirect(url_for("faculty_bp.faculty_exams"))

@faculty_bp.route("/faculty/exam/<string:course_code>/blacklist_data")
def get_blacklist_data(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Get exam
        _cur.execute("SELECT subject_id FROM exams WHERE course_code = %s", (course_code,))
        exam = _cur.fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404
        
        # Get all students enrolled in this course and their blacklist status
        _cur.execute("""
            SELECT sd.full_name, sd.enrollment_no,
                   (SELECT 1 FROM exam_blacklist eb WHERE eb.course_code = %s AND eb.enrollment_no = sd.enrollment_no) AS is_blacklisted
            FROM student_details sd
            JOIN student_subjects ss ON sd.enrollment_no = ss.enrollment_no
            WHERE ss.course_code = %s
        """, (course_code, course_code))
        students = _cur.fetchall()
        
        return jsonify([dict(s) for s in students])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@faculty_bp.route("/faculty/exam/<string:course_code>/blacklist/toggle/<string:enrollment_no>", methods=["POST"])
def toggle_blacklist(course_code, enrollment_no):
    if not session.get("user_id") or session.get("role") != "faculty":
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_connection()
    try:
        _cur = conn.cursor()
        _cur.execute("SELECT id FROM exam_blacklist WHERE course_code = %s AND enrollment_no = %s", (course_code, enrollment_no))
        eb = _cur.fetchone()
        if eb:
            _cur.execute("DELETE FROM exam_blacklist WHERE course_code = %s AND enrollment_no = %s", (course_code, enrollment_no))
            status = "removed"
            log_event(
                event_type=BLACKLIST_REMOVED,
                description=f"Student '{enrollment_no}' was removed from blacklist for exam '{course_code}'.",
                actor_id=session["user_id"],
                actor_role="faculty",
                target_type="exam",
                target_id=course_code,
                metadata={"enrollment_no": enrollment_no},
                request=request
            )
        else:
            _cur.execute("INSERT INTO exam_blacklist (course_code, enrollment_no) VALUES (%s, %s)", (course_code, enrollment_no))
            status = "added"
            log_event(
                event_type=BLACKLIST_ADDED,
                description=f"Student '{enrollment_no}' was blacklisted from exam '{course_code}'.",
                actor_id=session["user_id"],
                actor_role="faculty",
                target_type="exam",
                target_id=course_code,
                metadata={"enrollment_no": enrollment_no},
                request=request
            )
        conn.commit()
        return jsonify({"status": "success", "new_status": status})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@faculty_bp.route("/faculty/exam/<string:course_code>/stats")
def get_exam_stats(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_connection()
    try:
        _cur = conn.cursor()
        _cur.execute("SELECT total_marks FROM exams WHERE course_code = %s", (course_code,))
        exam = _cur.fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404
        
        tm = exam["total_marks"]
        _cur.execute("""
            SELECT ea.score, sd.full_name, sd.enrollment_no
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            WHERE ea.course_code = %s AND ea.completed = 1
        """, (course_code,))
        attempts_db = _cur.fetchall()
        
        attempts = [dict(a) for a in attempts_db]
        count = len(attempts)
        if count == 0:
            return jsonify({
                "count": 0, "avg": 0, "high": 0, "low": 0, "pass_count": 0, "fail_count": 0, "students": []
            })

        scores = [a["score"] for a in attempts]
        pcts = [round((s/tm*100), 1) if tm > 0 else 0 for s in scores]
        passed = [p for p in pcts if p >= 40]
        
        for i in range(len(attempts)):
             attempts[i]["pct"] = pcts[i]

        return jsonify({
            "count": count,
            "avg": round(sum(pcts)/count, 1),
            "high": max(pcts),
            "low": min(pcts),
            "pass_count": len(passed),
            "fail_count": count - len(passed),
            "students": attempts[:10]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@faculty_bp.route("/faculty/exam/toggle_results/<string:course_code>", methods=["POST"])
def toggle_results(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return jsonify({"error": "Unauthorized"}), 401
        
    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Verify ownership
        _cur.execute("""
            SELECT results_published FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
        """, (course_code, session["user_id"]))
        exam = _cur.fetchone()
        
        if exam:
            new_status = not exam["results_published"]
            _cur.execute("UPDATE exams SET results_published = %s WHERE course_code = %s", (new_status, course_code))
            conn.commit()
            return jsonify({"status": "success", "new_status": new_status})
        else:
            return jsonify({"error": "Access denied"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@faculty_bp.route("/faculty/subject/<int:subject_id>/questions")
def manage_questions(subject_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT * FROM subjects WHERE id = %s AND faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)", (subject_id, session["user_id"]))
    subject = _cur.fetchone()
    
    if not subject:
        conn.close()
        flash("Subject not found or you don't have access.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))

    _cur.execute("SELECT * FROM questions WHERE subject_id = %s ORDER BY id DESC", (subject_id,))
    questions_db = _cur.fetchall()
    
    questions = []
    for q in questions_db:
        _cur.execute("SELECT * FROM options WHERE question_id = %s", (q["id"],))
        options_db = _cur.fetchall()
        q_dict = dict(q)
        q_dict["options"] = [dict(o) for o in options_db]
        questions.append(q_dict)

    conn.close()
    return render_template("faculty_questions.html", subject=dict(subject), questions=questions)

@faculty_bp.route("/faculty/subject/<int:subject_id>/add_question", methods=["POST"])
def add_question(subject_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    question_text = request.form.get("question_text", "").strip()
    marks = request.form.get("marks", 1, type=int)
    question_type = "MCQ"
    difficulty = request.form.get("difficulty", "medium").strip()
    
    if not question_text:
        flash("Question text is required.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
        
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO questions (subject_id, question_text, question_type, marks, difficulty)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (subject_id, question_text, question_type, marks, difficulty))
        question_id = cursor.fetchone()["id"]
        
        for i in range(1, 5):
            opt_text = request.form.get(f"option_{i}", "").strip()
            is_correct = 1 if request.form.get("correct_option") == str(i) else 0
            if opt_text:
                cursor.execute("""
                    INSERT INTO options (question_id, option_text, is_correct)
                    VALUES (%s, %s, %s)
                """, (question_id, opt_text, is_correct))
                
        conn.commit()
        log_event(
            event_type=QUESTION_ADDED,
            description=f"Added a new question to subject_id {subject_id}.",
            actor_id=session["user_id"],
            actor_role="faculty",
            target_type="subject",
            target_id=str(subject_id),
            metadata={"question_id": question_id},
            request=request
        )
        flash("Question added successfully!", "success")
    except Exception as e:
        flash(f"Error adding question: {str(e)}", "danger")
    finally:
        conn.close()
        
    return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

@faculty_bp.route("/faculty/subject/<int:subject_id>/upload_file", methods=["POST"])
def upload_questions_subject_file(subject_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT id FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    
    if not faculty:
        conn.close()
        return redirect(url_for("auth_bp.faculty_login"))

    _cur.execute("SELECT * FROM subjects WHERE id = %s AND faculty_id = %s", (subject_id, faculty["id"]))
    subject = _cur.fetchone()
    
    if not subject:
        conn.close()
        flash("Subject not found or access denied.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))

    if 'csv_file' not in request.files:
        conn.close()
        flash("No file uploaded.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

    file = request.files['csv_file']
    if file.filename == '':
        conn.close()
        flash("No file selected.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

    try:
        new_info = _process_questions_file(subject_id, file)
        log_event(
            event_type=CSV_UPLOADED,
            description=f"Uploaded {len(new_info)} questions via file to subject_id {subject_id}.",
            actor_id=session["user_id"],
            actor_role="faculty",
            target_type="subject",
            target_id=str(subject_id),
            metadata={"question_count": len(new_info), "filename": file.filename},
            request=request
        )
        flash(f"Successfully added {len(new_info)} questions via {file.filename.split('.')[-1].upper()}.", "success")
    except Exception as e:
        flash(f"Error processing file: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

@faculty_bp.route("/faculty/students")
def faculty_students():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    if faculty:
        faculty = dict(faculty)
    if not faculty:
        conn.close()
        return redirect(url_for("auth_bp.faculty_login"))

    _cur.execute("""
        SELECT sd.full_name, sd.enrollment_no, sd.branch_code as student_branch, sd.semester as student_semester,
               s.subject_name, s.branch as subject_branch, e.course_code as subject_code
        FROM student_details sd
        LEFT JOIN student_subjects ss ON sd.enrollment_no = ss.enrollment_no
        LEFT JOIN exams e ON ss.course_code = e.course_code
        LEFT JOIN subjects s ON e.subject_id = s.id AND s.faculty_id = %s
        ORDER BY sd.semester ASC, sd.full_name ASC, s.subject_name ASC
    """, (faculty["id"],))
    raw_students = _cur.fetchall()
    
    student_dict = {}
    for row in raw_students:
        s_id = row['enrollment_no']
        if s_id not in student_dict:
            student_dict[s_id] = {
                'enrollment_no': s_id,
                'full_name': row['full_name'],
                'student_branch': row['student_branch'],
                'student_semester': row['student_semester'],
                'subjects': []
            }
        if row['subject_name']:
            student_dict[s_id]['subjects'].append({'name': row['subject_name'], 'code': row['subject_code']})
            
    students = []
    for s in student_dict.values():
        s['subject_name'] = ', '.join([sub['name'] for sub in s['subjects']]) if s['subjects'] else None
        s['subject_code'] = s['subjects'][0]['code'] if s['subjects'] else None
        students.append(s)
    
    # Get unique filter options
    unique_branches = sorted(list(set(row['student_branch'] for row in students if row['student_branch'])))
    unique_semesters = sorted(list(set(row['student_semester'] for row in students if row['student_semester'])))
    unique_subjects = sorted(list(set(sub['name'] for row in students for sub in row['subjects'] if sub['name'])))

    # Get subjects allocated to this faculty (for the Add Student modal)
    _cur.execute("""
        SELECT e.course_code, s.subject_name, s.branch, s.semester 
        FROM subjects s
        LEFT JOIN exams e ON s.id = e.subject_id
        WHERE s.faculty_id = %s
    """, (faculty["id"],))
    all_subjects = _cur.fetchall()
    
    conn.close()
    return render_template("faculty_students.html", 
                         faculty=faculty, 
                         students=students, 
                         subjects=all_subjects,
                         filter_branches=unique_branches,
                         filter_semesters=unique_semesters,
                         filter_subjects=unique_subjects)

# ── STUDENT MANAGEMENT ROUTES ──

@faculty_bp.route("/faculty/students/add_manual", methods=["POST"])
def add_student_manual():
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    full_name     = request.form.get("full_name", "").strip()
    enrollment_no = request.form.get("enrollment_no", "").strip()
    password      = request.form.get("password", "Password@123").strip()
    branch        = request.form.get("branch", "").strip()
    semester      = request.form.get("semester", 1, type=int)
    course_code   = request.form.get("course_code", "").strip()

    if not all([full_name, enrollment_no]):
        flash("Name and Enrollment No are required.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    conn = get_connection()
    try:
        cur = conn.cursor()
        # 1. Create a User entry if doesn't exist
        cur.execute("SELECT id FROM users WHERE username = %s", (enrollment_no,))
        user = cur.fetchone()
        
        if not user:
            hashed = generate_password_hash(password)
            cur.execute("""
                INSERT INTO users (username, password, role) 
                VALUES (%s, %s, 'student') RETURNING id
            """, (enrollment_no, hashed))
            user_id = cur.fetchone()["id"]
        else:
            user_id = user["id"]

        # 2. Create Student Details if doesn't exist
        cur.execute("SELECT enrollment_no FROM student_details WHERE enrollment_no = %s", (enrollment_no,))
        exists = cur.fetchone()
        if not exists:
            cur.execute("""
                INSERT INTO student_details (enrollment_no, user_id, full_name, branch_code, semester)
                VALUES (%s, %s, %s, %s, %s)
            """, (enrollment_no, user_id, full_name, branch, semester))
        
        # 3. Enroll in subjects based on branch/semester (Auto-assignment)
        cur.execute("SELECT id FROM faculty_details WHERE user_id = %s", (session["user_id"],))
        f_id = cur.fetchone()["id"]
        
        cur.execute("""
            SELECT e.course_code 
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE s.faculty_id = %s AND s.branch = %s AND s.semester = %s
        """, (f_id, branch, semester))
        auto_courses = [r["course_code"] for r in cur.fetchall()]
        
        # Merge with explicitly provided course_code
        all_codes = set(auto_courses)
        if course_code:
            all_codes.add(course_code)
            
        for c in all_codes:
            cur.execute("""
                INSERT INTO student_subjects (enrollment_no, course_code)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (enrollment_no, c))

        conn.commit()
        flash(f"Student {full_name} added successfully.", "success")
    except Exception as e:
        flash(f"Error adding student: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_students"))

@faculty_bp.route("/faculty/students/upload_csv", methods=["POST"])
def upload_students_csv():
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    course_code = request.form.get("course_code", "").strip()
    if 'csv_file' not in request.files:
        flash("No file uploaded.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    file = request.files['csv_file']
    if file.filename == '':
        flash("No file selected.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    try:
        filename = file.filename.lower()
        if filename.endswith('.csv'):
            try:
                content = file.stream.read().decode("utf-8-sig")
                df = pd.read_csv(io.StringIO(content))
            except UnicodeDecodeError:
                file.stream.seek(0)
                content = file.stream.read().decode("latin-1")
                df = pd.read_csv(io.StringIO(content))
        elif filename.endswith('.xlsx'):
            df = pd.read_excel(file)
        else:
            flash("Unsupported file format.", "danger")
            return redirect(url_for("faculty_bp.faculty_students"))

        df.columns = [str(c).strip().lower() for c in df.columns]
        
        conn = get_connection()
        cur = conn.cursor()
        count = 0
        
        for _, row in df.iterrows():
            r = row.to_dict()
            name = str(r.get('full_name') or r.get('student_name') or '').strip()
            enroll = str(r.get('enrollment_no') or r.get('enrollment') or '').strip()
            pwd = str(r.get('password') or 'Password@123').strip()
            br = str(r.get('branch') or r.get('branch_code') or '').strip()
            sem = int(r.get('semester') or r.get('semester_no', 1))

            if not name or not enroll: continue

            # Create User
            cur.execute("SELECT id FROM users WHERE username = %s", (enroll,))
            user = cur.fetchone()
            if not user:
                hashed = generate_password_hash(pwd)
                cur.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, 'student') RETURNING id", (enroll, hashed))
                uid = cur.fetchone()["id"]
            else:
                uid = user["id"]

            # Create Details
            cur.execute("INSERT INTO student_details (enrollment_no, user_id, full_name, branch_code, semester) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (enrollment_no) DO NOTHING", 
                        (enroll, uid, name, br, sem))
            
            # Link Subjects (Manual + Auto)
            cur.execute("SELECT id FROM faculty_details WHERE user_id = %s", (session["user_id"],))
            f_id = cur.fetchone()["id"]
            
            cur.execute("""
                SELECT e.course_code 
                FROM exams e
                JOIN subjects s ON e.subject_id = s.id
                WHERE s.faculty_id = %s AND s.branch = %s AND s.semester = %s
            """, (f_id, br, sem))
            auto_codes = [row_ex["course_code"] for row_ex in cur.fetchall()]
            
            all_links = set(auto_codes)
            if course_code:
                all_links.add(course_code)
                
            for c_lnk in all_links:
                cur.execute("INSERT INTO student_subjects (enrollment_no, course_code) VALUES (%s, %s) ON CONFLICT DO NOTHING", (enroll, c_lnk))
            
            count += 1

        conn.commit()
        conn.close()
        flash(f"Successfully processed {count} student records.", "success")
    except Exception as e:
        flash(f"Error processing file: {str(e)}", "danger")

    return redirect(url_for("faculty_bp.faculty_students"))

@faculty_bp.route("/faculty/student/delete/<string:enrollment_no>", methods=["POST"])
def delete_student(enrollment_no):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        cur = conn.cursor()
        # Faculty can only remove association from student_subjects, not the whole student record
        # However, the UI says "Remove student from your cohort"
        # We need to find which course_codes for this student belong to this faculty
        cur.execute("""
            DELETE FROM student_subjects 
            WHERE enrollment_no = %s 
            AND course_code IN (
                SELECT e.course_code FROM exams e
                JOIN subjects s ON e.subject_id = s.id
                WHERE s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
            )
        """, (enrollment_no, session["user_id"]))
        
        conn.commit()
        flash("Student removed from your courses.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_students"))

@faculty_bp.route("/faculty/results")
def faculty_results():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    if not faculty:
        conn.close()
        return redirect(url_for("auth_bp.faculty_login"))

    _cur.execute("""
        SELECT ea.id, e.course_code, sd.full_name, sd.enrollment_no,
               e.exam_name, e.total_marks, ea.score,
               s.subject_name, s.branch, s.semester,
               CASE WHEN e.total_marks > 0
                    THEN ROUND(CAST(CAST(ea.score AS FLOAT)/e.total_marks*100 AS NUMERIC), 1)
                    ELSE 0 END AS pct
        FROM exam_attempts ea
        JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
        JOIN exams e ON ea.course_code = e.course_code
        JOIN subjects s ON e.subject_id = s.id
        WHERE s.faculty_id = %s AND ea.completed = 1
        ORDER BY e.exam_name, ea.score DESC
    """, (faculty["id"],))
    results = _cur.fetchall()

    class_avg = 0.0
    pass_rate = 0.0
    highest_score = 0.0
    lowest_score = 100.0
    score_dist = {"excellent": 0, "passed": 0, "failed": 0}

    if results:
        pcts = [r["pct"] or 0.0 for r in results]
        for p in pcts:
            if p >= 75: score_dist["excellent"] += 1
            elif p >= 40: score_dist["passed"] += 1
            else: score_dist["failed"] += 1
        class_avg = round(sum(pcts) / len(pcts), 1)
        pass_rate = round((score_dist["excellent"] + score_dist["passed"]) / len(results) * 100, 1)
        highest_score = round(max(pcts), 1)
        lowest_score = round(min(pcts), 1)

    results_dicts = [dict(r) for r in results]
    conn.close()
    return render_template("faculty_results.html",
        faculty=dict(faculty), results=results_dicts,
        class_avg=class_avg, pass_rate=pass_rate,
        highest_score=highest_score, lowest_score=lowest_score,
        score_dist=score_dist,
    )

@faculty_bp.route("/faculty/exam/report/<string:course_code>")
def exam_report(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        _cur = conn.cursor()
        _cur.execute("""
            SELECT e.*, s.subject_name, s.branch, s.semester 
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
        """, (course_code, session["user_id"]))
        exam = _cur.fetchone()

        if not exam:
            flash("Exam not found.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        _cur.execute("""
            SELECT ea.*, sd.full_name, sd.enrollment_no
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            WHERE ea.course_code = %s AND ea.completed = 1
            ORDER BY ea.score DESC
        """, (course_code,))
        results = _cur.fetchall()
        results_dicts = [dict(r) for r in results]
        
        tm = exam["total_marks"]
        for r in results_dicts:
            r["pct"] = round((r["score"]/tm*100), 1) if tm > 0 else 0

        return render_template("faculty_exam_report.html", exam=dict(exam), results=results_dicts)
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
    finally:
        conn.close()

@faculty_bp.route("/faculty/results/export")
def export_results_csv():
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    import csv as csv_module
    from io import StringIO

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT id FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    if not faculty:
        conn.close()
        return redirect(url_for("auth_bp.faculty_login"))

    _cur.execute("""
        SELECT sd.full_name, sd.enrollment_no,
               e.exam_name, s.subject_name, s.branch, s.semester,
               e.total_marks, ea.score,
               CASE WHEN e.total_marks > 0
                    THEN ROUND(CAST(CAST(ea.score AS FLOAT) / e.total_marks * 100 AS NUMERIC), 1)
                    ELSE 0 END AS percentage
        FROM exam_attempts ea
        JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
        JOIN exams e ON ea.course_code = e.course_code
        JOIN subjects s ON e.subject_id = s.id
        WHERE s.faculty_id = %s AND ea.completed = 1
        ORDER BY e.exam_name, ea.score DESC
    """, (faculty["id"],))
    results = _cur.fetchall()
    conn.close()

    si = StringIO()
    writer = csv_module.writer(si)
    writer.writerow(["Student Name", "Enrollment No", "Exam", "Subject", "Branch", "Semester", "Total Marks", "Score", "Percentage (%)"])
    for r in results:
        writer.writerow([r["full_name"], r["enrollment_no"], r["exam_name"],
                         r["subject_name"], r["branch"], r["semester"],
                         r["total_marks"], r["score"], r["percentage"]])

    return Response(si.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=exam_results.csv"})

@faculty_bp.route("/faculty/question/delete/<int:question_id>", methods=["POST"])
def delete_question(question_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("SELECT id FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    
    _cur.execute("""
        SELECT q.id, q.subject_id FROM questions q
        JOIN subjects s ON q.subject_id = s.id
        WHERE q.id = %s AND s.faculty_id = %s
    """, (question_id, faculty["id"]))
    q = _cur.fetchone()

    if q:
        subject_id = q["subject_id"]
        _cur.execute("DELETE FROM questions WHERE id = %s", (question_id,))
        conn.commit()
        conn.close()
        flash("Question deleted successfully.", "success")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
    
    conn.close()
    flash("Question not found or access denied.", "danger")
    return redirect(url_for("faculty_bp.faculty_exams"))

@faculty_bp.route("/faculty/exam/<string:course_code>/questions/manage")
def manage_exam_questions_link(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
        
    conn = get_connection()
    _cur = conn.cursor()
    _cur.execute("""
        SELECT e.*, s.subject_name 
        FROM exams e
        JOIN subjects s ON e.subject_id = s.id
        WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
    """, (course_code, session["user_id"]))
    exam = _cur.fetchone()
    
    if not exam:
        conn.close()
        flash("Exam not found or access denied.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
        
    # Get currently assigned questions
    _cur.execute("""
        SELECT q.*, eq.section 
        FROM questions q
        JOIN exam_questions eq ON q.id = eq.question_id
        WHERE eq.course_code = %s
    """, (course_code,))
    assigned_questions = _cur.fetchall()
    
    # Get available questions (same subject, not assigned)
    assigned_ids = [q["id"] for q in assigned_questions]
    if assigned_ids:
        placeholders = ','.join('%s' for _ in assigned_ids)
        _cur.execute(f"SELECT * FROM questions WHERE subject_id = %s AND id NOT IN ({placeholders})", (exam["subject_id"], *assigned_ids))
    else:
        _cur.execute("SELECT * FROM questions WHERE subject_id = %s", (exam["subject_id"],))
    available_questions = _cur.fetchall()
    
    conn.close()
    return render_template("faculty_exam_questions.html", exam=dict(exam), assigned=assigned_questions, available=available_questions)

@faculty_bp.route("/faculty/exam/<string:course_code>/questions/add", methods=["POST"])
def add_questions_to_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return jsonify({"success": False}), 401
    
    data = request.json
    question_ids = data.get("question_ids", [])
    section = data.get("section", "A")
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for qid in question_ids:
            cursor.execute("INSERT INTO exam_questions (course_code, question_id, section) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (course_code, qid, section))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@faculty_bp.route("/faculty/exam/<string:course_code>/questions/remove", methods=["POST"])
def remove_questions_from_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return jsonify({"success": False}), 401
    
    data = request.json
    question_ids = data.get("question_ids", [])
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if question_ids:
            placeholders = ','.join('%s' for _ in question_ids)
            cursor.execute(f"DELETE FROM exam_questions WHERE course_code = %s AND question_id IN ({placeholders})", (course_code, *question_ids))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

@faculty_bp.route("/faculty/exam/<string:course_code>/upload_questions", methods=["POST"])
def upload_questions_to_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Verify exam ownership
        _cur.execute("""
            SELECT e.course_code, e.subject_id 
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
        """, (course_code, session["user_id"]))
        exam = _cur.fetchone()

        if not exam:
            flash("Exam not found or access denied.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        if 'csv_file' not in request.files:
            flash("No file uploaded.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        file = request.files['csv_file']
        if file.filename == '':
            flash("No file selected.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        # Process the file
        new_info = _process_questions_file(exam["subject_id"], file)
        
        # Link to exam
        for qid, section in new_info:
            _cur.execute("INSERT INTO exam_questions (course_code, question_id, section) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", 
                         (course_code, qid, section))
        
        conn.commit()
        log_event(
            event_type=CSV_UPLOADED,
            description=f"Uploaded {len(new_info)} questions to exam {course_code} via file.",
            actor_id=session["user_id"],
            actor_role="faculty",
            target_type="exam",
            target_id=course_code,
            metadata={"question_count": len(new_info), "filename": file.filename},
            request=request
        )
        flash(f"Successfully uploaded {len(new_info)} questions to exam {course_code}.", "success")
    except Exception as e:
        flash(f"Error processing file: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_exams"))


# ═══════════════════════════════════════════════════════════════
#  SCHEDULE EXAM  –  Faculty schedules a new exam (simple form)
# ═══════════════════════════════════════════════════════════════

@faculty_bp.route("/schedule_exam", methods=["GET", "POST"])
def schedule_exam():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    _cur = conn.cursor()
    
    # 1. Get detailed faculty record
    _cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = _cur.fetchone()
    if not faculty:
        conn.close()
        flash("Faculty profile not found.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    # 2. FETCH DYNAMIC SUBJECTS assigned to this faculty
    _cur.execute("SELECT id, subject_name FROM subjects WHERE faculty_id = %s", (faculty["id"],))
    faculty_subjects = _cur.fetchall()
    assigned_subjects = [s["subject_name"] for s in faculty_subjects]
    
    # 3. FETCH EXISTING EXAMS (Templates) created by this faculty
    # We filter by subject_ids belonging to this faculty
    subject_ids = [s["id"] for s in faculty_subjects]
    existing_exams = []
    if subject_ids:
        placeholders = ','.join('%s' for _ in subject_ids)
        _cur.execute(f"""
            SELECT e.course_code, e.exam_name, s.subject_name 
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
        """, tuple(subject_ids))
        existing_exams = _cur.fetchall()

    # 4. Fetch this faculty's existing scheduled exams
    _cur.execute("""
        SELECT * FROM scheduled_exams
        WHERE created_by = %s
        ORDER BY exam_date DESC, start_time DESC
    """, (session["user_id"],))
    scheduled = [dict(r) for r in _cur.fetchall()]
    conn.close()

    if request.method == "POST":
        subject     = request.form.get("subject", "").strip()
        course_code = request.form.get("course_code", "").strip()
        exam_date   = request.form.get("exam_date", "").strip()
        start_time  = request.form.get("start_time", "").strip()
        end_time    = request.form.get("end_time", "").strip()
        duration    = request.form.get("duration", 60, type=int)
        total_marks = request.form.get("total_marks", 100, type=int)

        if subject not in assigned_subjects:
            flash(f"Invalid subject selected.", "danger")
            return redirect(url_for("faculty_bp.schedule_exam"))

        if not all([exam_date, start_time, end_time, course_code]):
            flash("All fields including Exam Template (Course Code) are required.", "danger")
            return redirect(url_for("faculty_bp.schedule_exam"))

        try:
            conn2 = get_connection()
            cur2 = conn2.cursor()
            cur2.execute("""
                INSERT INTO scheduled_exams
                    (subject, course_code, exam_date, start_time, end_time, duration, total_marks, status, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled', %s)
            """, (subject, course_code, exam_date, start_time, end_time, duration, total_marks, session["user_id"]))
            
            # Sync the main exams table as well
            # Join exam_date with start_time and end_time for compatibility with student.py's parse_dt
            full_start = f"{exam_date} {start_time}"
            full_end = f"{exam_date} {end_time}"
            cur2.execute("""
                UPDATE exams 
                SET exam_date = %s, start_time = %s, end_time = %s, total_marks = %s, duration_minutes = %s
                WHERE course_code = %s
            """, (exam_date, full_start, full_end, total_marks, duration, course_code))
            
            conn2.commit()
            conn2.close()
            flash(f"{subject} exam ({course_code}) scheduled successfully and synced!", "success")
        except Exception as e:
            flash(f"Error scheduling exam: {str(e)}", "danger")

        return redirect(url_for("faculty_bp.schedule_exam"))

    return render_template(
        "schedule_exam.html",
        faculty=dict(faculty),
        subjects=assigned_subjects,
        existing_exams=existing_exams,
        scheduled=scheduled,
        now_date=_date.today().isoformat(),
    )




@faculty_bp.route("/schedule_exam/delete/<int:exam_id>", methods=["POST"])
def delete_scheduled_exam(exam_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        cur = conn.cursor()
        # Find the course_code first for syncing
        cur.execute("SELECT course_code FROM scheduled_exams WHERE id = %s", (exam_id,))
        exam_row = cur.fetchone()
        
        cur.execute(
            "DELETE FROM scheduled_exams WHERE id = %s AND created_by = %s",
            (exam_id, session["user_id"])
        )
        
        # Optional: We could reset the exams table date, but maybe better to keep it
        # as a record of the last scheduled time.
        
        conn.commit()
        flash("Scheduled exam deleted.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.schedule_exam"))

@faculty_bp.route("/faculty/proctor/<string:course_code>")
def proctor_exam(course_code):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        _cur = conn.cursor()
        # Verify exam exists and belongs to faculty
        _cur.execute("""
            SELECT e.*, s.subject_name 
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = %s AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = %s)
        """, (course_code, session["user_id"]))
        exam = _cur.fetchone()

        if not exam:
            flash("Exam not found or access denied.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        # Get all students enrolled in this course
        _cur.execute("""
            SELECT sd.full_name, sd.enrollment_no,
                   ea.completed, ea.attempt_time, ea.score
            FROM student_details sd
            JOIN student_subjects ss ON sd.enrollment_no = ss.enrollment_no
            LEFT JOIN exam_attempts ea ON sd.enrollment_no = ea.enrollment_no AND ea.course_code = %s
            WHERE ss.course_code = %s
            ORDER BY sd.full_name ASC
        """, (course_code, course_code))
        students_raw = _cur.fetchall()
        
        students = []
        stats = {"total": 0, "active": 0, "completed": 0, "absent": 0}
        
        for s in students_raw:
            sd = dict(s)
            stats["total"] += 1
            if sd["completed"] == 1:
                sd["status"] = "Completed"
                stats["completed"] += 1
            elif sd["completed"] == 0 and sd["attempt_time"]:
                sd["status"] = "Active"
                stats["active"] += 1
            else:
                sd["status"] = "Absent"
                stats["absent"] += 1
            students.append(sd)

        # Get faculty details for layout
        _cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
        faculty = _cur.fetchone()

        return render_template("faculty_proctor.html", 
                               faculty=faculty, 
                               exam=dict(exam), 
                               students=students, 
                               stats=stats)
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
    finally:
        conn.close()
