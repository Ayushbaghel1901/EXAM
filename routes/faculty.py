from flask import Blueprint, render_template, request, redirect, url_for, session, flash, Response, jsonify
from database import get_connection
from datetime import datetime
import os
import google.generativeai as genai
from dotenv import load_dotenv
import random

faculty_bp = Blueprint('faculty_bp', __name__)

# Load environment variables from .env file
load_dotenv()

# LLM Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def estimate_difficulty_llm(text):
    """Estimate question difficulty using Gemini LLM."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "medium"
        
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Analyze the following academic question and classify its difficulty level as exactly one word: 'easy', 'medium', or 'hard'.
        - easy: Basic recall, simple facts, direct definitions.
        - medium: Application of concepts, multi-step problem solving.
        - hard: Complex analysis, synthesis, proofs, advanced logic.
        
        Question: "{text}"
        
        Respond ONLY with 'easy', 'medium', or 'hard'.
        """
        response = model.generate_content(prompt)
        difficulty = response.text.strip().lower()
        
        if 'hard' in difficulty: return 'hard'
        elif 'medium' in difficulty: return 'medium'
        elif 'easy' in difficulty: return 'easy'
        return 'medium'
        
    except Exception as e:
        print(f"LLM Error: {str(e)}")
        return "medium"

def select_questions_llm(exam_name, candidates, count, difficulty):
    """Pick most relevant questions from candidates for the given exam name/theme."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or not candidates or count <= 0:
        return random.sample([c["id"] for c in candidates], min(len(candidates), count))
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        # We send IDs and text. Keep it reasonably small to avoid token limits.
        # If candidates are too many, we first pre-filter or give a representative sample.
        limited_candidates = candidates[:50] # Limit to avoid huge prompt
        q_list_text = "\n".join([f"ID:{c['id']} - {c['question_text'][:200]}" for c in limited_candidates])
        
        prompt = f"""
        Subject: {exam_name}
        Difficulty Level requested: {difficulty}
        Select exactly {count} question IDs from the following list that best fit this subject and difficulty.
        
        List:
        {q_list_text}
        
        Respond ONLY with the selected IDs separated by commas, e.g. 1,5,10.
        """
        response = model.generate_content(prompt)
        text = response.text.strip()
        import re
        ids = [int(i) for i in re.findall(r'\b\d+\b', text)]
        
        # Validate IDs are in the candidates list
        valid_ids = [c["id"] for c in candidates]
        final_ids = [i for i in ids if i in valid_ids][:count]
        
        # If LLM failed to provide enough or valid IDs, fallback to random
        if len(final_ids) < count:
            remaining = list(set(valid_ids) - set(final_ids))
            final_ids.extend(random.sample(remaining, min(len(remaining), count - len(final_ids))))
            
        return final_ids
    except Exception as e:
        print(f"LLM Selection Error: {str(e)}")
        return random.sample([c["id"] for c in candidates], min(len(candidates), count))

def _process_questions_csv(subject_id, file, forced_difficulty=None):
    """Helper to process question CSV and return list of (question_id, section) tuples."""
    from io import StringIO
    import csv
    
    # Read the file content
    content = file.stream.read().decode("UTF8")
    stream = StringIO(content, newline=None)
    csv_input = csv.DictReader(stream)
    
    conn = get_connection()
    cursor = conn.cursor()
    processed_data = [] # List of tuples: (q_id, section)
    
    for row in csv_input:
        # Flexible column mapping
        q_text = (row.get('questions') or row.get('question_text') or row.get('question') or '').strip()
        marks = int(row.get('marks', 1))
        section = row.get('section', 'A').strip().upper()
        if section not in ['A', 'B', 'C']: section = 'A'
        
        # Priority order: forced_difficulty > 'difficulty' column > 'level of difficulty' column > default 'medium'
        difficulty = forced_difficulty
        if not difficulty:
            difficulty = (row.get('difficulty') or row.get('level of difficulty') or '').strip().lower()
            
        if difficulty not in ['easy', 'medium', 'hard']:
            difficulty = 'medium'
        
        if not q_text: continue
        
        cursor.execute("INSERT INTO questions (subject_id, question_text, question_type, marks, difficulty) VALUES (?, ?, 'MCQ', ?, ?)", 
                       (subject_id, q_text, marks, difficulty))
        q_id = cursor.lastrowid
        processed_data.append((q_id, section))
        
        # Option mapping
        correct = row.get('correct answer') or row.get('correct_option') or row.get('answer') or '1'
        
        for i in range(1, 5):
            # Try both 'option1' and 'option_1'
            opt_text = (row.get(f'option{i}') or row.get(f'option_{i}') or '').strip()
            if opt_text:
                is_correct = 1 if str(correct) == str(i) or str(correct).strip() == opt_text else 0
                cursor.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (?, ?, ?)", (q_id, opt_text, is_correct))
    
    conn.commit()
    conn.close()
    return processed_data


@faculty_bp.route("/faculty/question/auto_difficulty", methods=["POST"])
def auto_difficulty():
    if not session.get("user_id") or session.get("role") != "faculty":
        return {"error": "unauthorized"}, 401
    
    data = request.json
    text = data.get("text", "")
    
    result = estimate_difficulty_llm(text)
    if result.startswith("error_"):
        return {"error": result}, 500
        
    return {"difficulty": result}

@faculty_bp.route("/faculty/dashboard")
def faculty_dashboard():
    if not session.get("user_id"):
        return redirect(url_for("auth_bp.faculty_login"))
    if session.get("role") == "student":
        return redirect(url_for("student_bp.student_dashboard"))
    if session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
    
    session.pop('_flashes', None) # Clear any leftover login error flashes

    conn = get_connection()
    faculty = conn.execute("SELECT * FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()

    if not faculty:
        conn.close()
        flash("Faculty profile not found. Please contact administration.", "danger")
        session.clear()
        return redirect(url_for("auth_bp.faculty_login"))

    total_students = conn.execute("SELECT COUNT(*) FROM student_details").fetchone()[0]
    subjects = conn.execute("SELECT id, subject_name, branch, semester FROM subjects WHERE faculty_id = ?", (faculty["id"],)).fetchall()
    subject_ids = [s["id"] for s in subjects]

    active_exams_count = 0
    recent_exams = []
    pending_results = 0
    total_results = 0
    recent_activity = []
    score_dist = {"excellent": 0, "passed": 0, "failed": 0}
    at_risk_students = []
    class_avg = 0.0

    if subject_ids:
        placeholders = ','.join('?' for _ in subject_ids)
        active_exams_count = conn.execute(
            f"SELECT COUNT(*) FROM exams WHERE subject_id IN ({placeholders})", tuple(subject_ids)
        ).fetchone()[0]

        recent_exams = conn.execute(f"""
            SELECT e.course_code, e.exam_name, e.exam_date, e.total_marks, e.duration_minutes,
                   s.id AS subject_id, s.subject_name, s.branch, s.semester,
                   e.pass_percentage,
                   (SELECT COUNT(*) FROM exam_attempts ea2 WHERE ea2.course_code = e.course_code AND ea2.completed = 1) AS attempt_count,
                   (SELECT COUNT(*) FROM questions WHERE subject_id = s.id) AS question_count
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
            ORDER BY e.exam_date DESC LIMIT 5
        """, tuple(subject_ids)).fetchall()

        total_results = conn.execute(f"""
            SELECT COUNT(*) FROM exam_attempts ea
            JOIN exams e ON ea.course_code = e.course_code
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
        """, tuple(subject_ids)).fetchone()[0]

        exams_with_attempts = conn.execute(f"""
            SELECT COUNT(DISTINCT e.course_code) FROM exams e
            JOIN exam_attempts ea ON e.course_code = ea.course_code
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
        """, tuple(subject_ids)).fetchone()[0]
        pending_results = max(0, active_exams_count - exams_with_attempts)

        recent_activity = conn.execute(f"""
            SELECT sd.full_name, e.exam_name, ea.score, e.total_marks, ea.completed, s.subject_name
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            JOIN exams e ON ea.course_code = e.course_code
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
            ORDER BY ea.id DESC LIMIT 8
        """, tuple(subject_ids)).fetchall()

        # Score distribution for charts
        all_scores = conn.execute(f"""
            SELECT ea.score, e.total_marks
            FROM exam_attempts ea
            JOIN exams e ON ea.course_code = e.course_code
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1 AND e.total_marks > 0
        """, tuple(subject_ids)).fetchall()

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
        at_risk_raw = conn.execute(f"""
            SELECT sd.full_name, sd.enrollment_no, ea.score, e.total_marks, e.exam_name, s.subject_name,
                   ROUND(CAST(ea.score AS FLOAT)/e.total_marks*100, 1) AS pct
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            JOIN exams e ON ea.course_code = e.course_code
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1 AND e.total_marks > 0
              AND CAST(ea.score AS FLOAT)/e.total_marks*100 < 40
            ORDER BY pct ASC LIMIT 5
        """, tuple(subject_ids)).fetchall()
        at_risk_students = [dict(r) for r in at_risk_raw]

    conn.close()
    return render_template("faculty_dashboard.html",
        faculty=dict(faculty),
        total_students=total_students,
        active_exams=active_exams_count,
        recent_exams=[dict(r) for r in recent_exams],
        subjects=[dict(r) for r in subjects],
        pending_results=pending_results,
        total_results=total_results,
        recent_activity=[dict(r) for r in recent_activity],
        score_dist=score_dist,
        at_risk_students=at_risk_students,
        class_avg=class_avg,
    )

@faculty_bp.route("/faculty/exams")
def faculty_exams():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    faculty = conn.execute("SELECT * FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
    subjects = conn.execute("SELECT id, subject_name, subject_code, branch, semester FROM subjects WHERE faculty_id = ?", (faculty["id"],)).fetchall()
    subject_ids = [s["id"] for s in subjects]

    all_exams = []
    if subject_ids:
        placeholders = ','.join('?' for _ in subject_ids)
        all_exams_raw = conn.execute(f"""
            SELECT e.course_code, e.exam_name, e.exam_date, e.start_time, e.end_time, e.total_marks, e.duration_minutes,
                   e.pass_percentage,
                   s.id AS subject_id, s.subject_name, s.branch, s.semester, s.subject_code,
                   (SELECT COUNT(*) FROM exam_attempts ea WHERE ea.course_code = e.course_code AND ea.completed = 1) AS attempt_count,
                   (SELECT COUNT(*) FROM exam_questions eq WHERE eq.course_code = e.course_code) AS question_count
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.subject_id IN ({placeholders})
            ORDER BY e.exam_date DESC
        """, tuple(subject_ids)).fetchall()
        
        for e in all_exams_raw:
            ed = dict(e)
            # Fetch difficulty breakdown for this specific exam
            diff_stats = conn.execute("""
                SELECT 
                    SUM(CASE WHEN q.difficulty = 'easy' THEN 1 ELSE 0 END) as easy_q,
                    SUM(CASE WHEN q.difficulty = 'medium' THEN 1 ELSE 0 END) as medium_q,
                    SUM(CASE WHEN q.difficulty = 'hard' THEN 1 ELSE 0 END) as hard_q,
                    SUM(CASE WHEN q.difficulty IS NULL OR q.difficulty = '' OR q.difficulty = 'error_api' OR q.difficulty = 'error_no_key' THEN 1 ELSE 0 END) as unknown_q
                FROM exam_questions eq
                JOIN questions q ON eq.question_id = q.id
                WHERE eq.course_code = ?
            """, (e["course_code"],)).fetchone()
            
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

    use_ai = request.form.get("use_ai_selection") == "on"
    
    is_dynamic = request.form.get("is_dynamic") == "on"

    if not subject_id or not exam_name:
        flash("Subject and Exam Name are required.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
        
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO exams (course_code, subject_id, exam_name, exam_date, start_time, end_time, total_marks, duration_minutes, pass_percentage, dynamic_easy, dynamic_medium, dynamic_hard, is_dynamic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (course_code, subject_id, exam_name, exam_date, start_time or None, end_time or None, total_marks, duration, pass_percentage, 
              auto_easy if is_dynamic else 0, auto_medium if is_dynamic else 0, auto_hard if is_dynamic else 0, 1 if is_dynamic else 0))
        
        
        # Handle Bulk CSV Upload if present
        uploaded_info = [] # Store (q_id, section)
        if 'csv_file' in request.files:
            file = request.files['csv_file']
            if file and file.filename.endswith('.csv'):
                new_info = _process_questions_csv(subject_id, file)
                uploaded_info.extend(new_info)
                # Link uploaded questions to this exam
                for qid, section in new_info:
                    cursor.execute("INSERT OR IGNORE INTO exam_questions (course_code, question_id, section) VALUES (?, ?, ?)", (course_code, qid, section))
        
        # Extract just IDs for exclusion in auto-selection
        uploaded_q_ids = [info[0] for info in uploaded_info]


        if not is_dynamic and (auto_easy > 0 or auto_medium > 0 or auto_hard > 0):
            def fetch_random_q(diff, count):
                if count <= 0: return []
                exclude_ids = uploaded_q_ids if uploaded_q_ids else [-1]
                placeholders = ','.join('?' for _ in exclude_ids)
                qs = cursor.execute(f"SELECT id FROM questions WHERE subject_id = ? AND difficulty = ? AND id NOT IN ({placeholders})", (subject_id, diff, *exclude_ids)).fetchall()
                q_ids = [q["id"] for q in qs]
                if not q_ids: return []
                return random.sample(q_ids, min(len(q_ids), count))
                
            selected_ids = []
            selected_ids.extend(fetch_random_q("easy", auto_easy))
            selected_ids.extend(fetch_random_q("medium", auto_medium))
            selected_ids.extend(fetch_random_q("hard", auto_hard))
            
            for qid in selected_ids:
                cursor.execute("INSERT OR IGNORE INTO exam_questions (course_code, question_id) VALUES (?, ?)", (course_code, qid))

        conn.commit()
        conn.close()
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
        # Verify ownership
        exam = conn.execute("""
            SELECT e.course_code FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = ? AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = ?)
        """, (course_code, session["user_id"])).fetchone()

        if exam:
            conn.execute("DELETE FROM exams WHERE course_code = ?", (course_code,))
            conn.commit()
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
        # Verify ownership
        exam = conn.execute("""
            SELECT e.course_code FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = ? AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = ?)
        """, (course_code, session["user_id"])).fetchone()

        if exam:
            conn.execute("""
                UPDATE exams SET exam_name = ?, total_marks = ?, pass_percentage = ?, 
                                 duration_minutes = ?, start_time = ?, end_time = ?
                WHERE course_code = ?
            """, (exam_name, total_marks, pass_percentage, duration_minutes, start_time, end_time, course_code))
            conn.commit()
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
        # Get faculty ID
        faculty = conn.execute("SELECT id FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
        
        # Get original exam
        exam = conn.execute("""
            SELECT e.* FROM exams e 
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = ? AND s.faculty_id = ?
        """, (course_code, faculty["id"])).fetchone()
        
        if not exam:
            flash("Exam not found or access denied.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))
            
        # Create new course code
        new_code = f"{exam['course_code']}-COPY-{random.randint(100, 999)}"
        new_name = f"{exam['exam_name']} (Copy)"
        
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO exams (course_code, subject_id, exam_name, exam_date, start_time, end_time, total_marks, duration_minutes, pass_percentage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (new_code, exam["subject_id"], new_name, exam["exam_date"], exam["start_time"], exam["end_time"], 
              exam["total_marks"], exam["duration_minutes"], exam["pass_percentage"]))
        
        # Duplicate questions
        questions = conn.execute("SELECT question_id, section FROM exam_questions WHERE course_code = ?", (course_code,)).fetchall()
        for q in questions:
            cursor.execute("INSERT INTO exam_questions (course_code, question_id, section) VALUES (?, ?, ?)", (new_code, q["question_id"], q["section"]))
            
        conn.commit()
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
        # Get subject for this exam
        exam = conn.execute("SELECT subject_id FROM exams WHERE course_code = ?", (course_code,)).fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404
            
        subject_id = exam["subject_id"]
        
        # Get all students enrolled in this course and their blacklist status
        students = conn.execute("""
            SELECT sd.full_name, sd.enrollment_no,
                   (SELECT 1 FROM exam_blacklist eb WHERE eb.course_code = ? AND eb.enrollment_no = sd.enrollment_no) AS is_blacklisted
            FROM student_details sd
            JOIN student_subjects ss ON sd.enrollment_no = ss.enrollment_no
            WHERE ss.course_code = ?
        """, (course_code, course_code)).fetchall()
        
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
        # Check if already blacklisted
        eb = conn.execute("SELECT id FROM exam_blacklist WHERE course_code = ? AND enrollment_no = ?", (course_code, enrollment_no)).fetchone()
        if eb:
            conn.execute("DELETE FROM exam_blacklist WHERE course_code = ? AND enrollment_no = ?", (course_code, enrollment_no))
            status = "unblacklisted"
        else:
            conn.execute("INSERT INTO exam_blacklist (course_code, enrollment_no) VALUES (?, ?)", (course_code, enrollment_no))
            status = "blacklisted"
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
        exam = conn.execute("SELECT total_marks FROM exams WHERE course_code = ?", (course_code,)).fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404
        
        tm = exam["total_marks"]
        attempts_db = conn.execute("""
            SELECT ea.score, sd.full_name, sd.enrollment_no
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            WHERE ea.course_code = ? AND ea.completed = 1
        """, (course_code,)).fetchall()
        
        attempts = [dict(a) for a in attempts_db]
        count = len(attempts)
        if count == 0:
            return jsonify({
                "count": 0, "avg": 0, "high": 0, "low": 0, "pass_count": 0, "fail_count": 0, "students": []
            })

        scores = [a["score"] for a in attempts]
        pcts = [round((s/tm*100), 1) if tm > 0 else 0 for s in scores]
        
        # Pass/fail
        passed = [p for p in pcts if p >= 40]
        
        # Map pcts to attempt list
        for i in range(len(attempts)):
             attempts[i]["pct"] = pcts[i]

        return jsonify({
            "count": count,
            "avg": round(sum(pcts)/count, 1),
            "high": max(pcts),
            "low": min(pcts),
            "pass_count": len(passed),
            "fail_count": count - len(passed),
            "students": attempts[:10] # Top 10 or recent for the stats table
        })
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
    subject = conn.execute("SELECT * FROM subjects WHERE id = ? AND faculty_id = (SELECT id FROM faculty_details WHERE user_id = ?)", (subject_id, session["user_id"])).fetchone()
    
    if not subject:
        conn.close()
        flash("Subject not found or you don't have access.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))

    questions_db = conn.execute("SELECT * FROM questions WHERE subject_id = ? ORDER BY id DESC", (subject_id,)).fetchall()
    
    questions = []
    for q in questions_db:
        options = conn.execute("SELECT * FROM options WHERE question_id = ?", (q["id"],)).fetchall()
        q_dict = dict(q)
        q_dict["options"] = [dict(o) for o in options]
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
            VALUES (?, ?, ?, ?, ?)
        """, (subject_id, question_text, question_type, marks, difficulty))
        question_id = cursor.lastrowid
        
        for i in range(1, 5):
            opt_text = request.form.get(f"option_{i}", "").strip()
            is_correct = 1 if request.form.get("correct_option") == str(i) else 0
            if opt_text:
                cursor.execute("""
                    INSERT INTO options (question_id, option_text, is_correct)
                    VALUES (?, ?, ?)
                """, (question_id, opt_text, is_correct))
                
        conn.commit()
        flash("Question added successfully!", "success")
    except Exception as e:
        flash(f"Error adding question: {str(e)}", "danger")
    finally:
        conn.close()
        
    return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

@faculty_bp.route("/faculty/subject/<int:subject_id>/upload_csv", methods=["POST"])
def upload_questions_subject_csv(subject_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    faculty = conn.execute("SELECT id FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
    
    subject = conn.execute("SELECT * FROM subjects WHERE id = ? AND faculty_id = ?", (subject_id, faculty["id"])).fetchone()
    conn.close()
    
    if not subject:
        flash("Subject not found or access denied.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))

    if 'csv_file' not in request.files:
        flash("No file uploaded.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

    file = request.files['csv_file']
    if file.filename == '':
        flash("No file selected.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

    if not file.filename.endswith('.csv'):
        flash("Invalid file format. Please upload a CSV file.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

    try:
        new_info = _process_questions_csv(subject_id, file)
        flash(f"Successfully added {len(new_info)} questions via CSV.", "success")
    except Exception as e:
        flash(f"Error processing CSV: {str(e)}", "danger")

    return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

@faculty_bp.route("/faculty/students")
def faculty_students():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    faculty = conn.execute("SELECT * FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
    
    # Get all students in the system, and left join with the faculty's subjects/exams
    raw_students = conn.execute("""
        SELECT sd.full_name, sd.enrollment_no, sd.branch as student_branch, sd.semester as student_semester,
               s.subject_name, s.branch as subject_branch, e.course_code as subject_code
        FROM student_details sd
        LEFT JOIN student_subjects ss ON sd.enrollment_no = ss.enrollment_no
        LEFT JOIN exams e ON ss.course_code = e.course_code
        LEFT JOIN subjects s ON e.subject_id = s.id AND s.faculty_id = ?
        ORDER BY sd.semester ASC, sd.full_name ASC, s.subject_name ASC
    """, (faculty["id"],)).fetchall()
    
    # Consolidate duplicate rows for students taking multiple subjects
    student_dict = {}
    for row in raw_students:
        s_id = row['enrollment_no']
        if s_id not in student_dict:
            student_dict[s_id] = {
                'enrollment_no': s_id,
                'full_name': row['full_name'],
                'enrollment_no': row['enrollment_no'],
                'student_branch': row['student_branch'],
                'student_semester': row['student_semester'],
                'subjects': []
            }
        
        # We handle displaying multiple subjects by appending them to a list
        if row['subject_name']:
            student_dict[s_id]['subjects'].append({
                'name': row['subject_name'],
                'code': row['subject_code']
            })
            
    # For compatibility with existing template logic that expects single 'subject_name' attributes,
    # we'll format the subjects list into strings. If empty, it'll fall back to template's 'Not Assigned'
    students = []
    for s in student_dict.values():
        s['subject_name'] = ', '.join([sub['name'] for sub in s['subjects']]) if s['subjects'] else None
        s['subject_code'] = s['subjects'][0]['code'] if s['subjects'] else None
        students.append(s)
    
    # Get exams taught by this faculty for the dropdowns (to get course_code)
    subjects = conn.execute("""
        SELECT e.course_code, s.subject_name, s.branch, s.semester 
        FROM exams e
        JOIN subjects s ON e.subject_id = s.id
        WHERE s.faculty_id = ?
    """, (faculty["id"],)).fetchall()
    
    conn.close()
    return render_template("faculty_students.html", faculty=faculty, students=students, subjects=subjects)

@faculty_bp.route("/faculty/results")
def faculty_results():
    if not session.get("user_id") or session.get("role") != "faculty":
        flash("Please login first.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    faculty = conn.execute("SELECT * FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()

    results = conn.execute("""
        SELECT ea.id, e.course_code, sd.full_name, sd.enrollment_no,
               e.exam_name, e.total_marks, ea.score,
               s.subject_name, s.branch, s.semester,
               CASE WHEN e.total_marks > 0
                    THEN ROUND(CAST(ea.score AS FLOAT)/e.total_marks*100, 1)
                    ELSE 0 END AS pct
        FROM exam_attempts ea
        JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
        JOIN exams e ON ea.course_code = e.course_code
        JOIN subjects s ON e.subject_id = s.id
        WHERE s.faculty_id = ? AND ea.completed = 1
        ORDER BY e.exam_name, ea.score DESC
    """, (faculty["id"],)).fetchall()

    # Aggregate analytics
    class_avg = 0.0
    pass_rate = 0.0
    highest_score = 0.0
    lowest_score = 100.0
    score_dist = {"excellent": 0, "passed": 0, "failed": 0}

    if results:
        pcts = []
        for r in results:
            p = r["pct"] or 0.0
            pcts.append(p)
            if p >= 75: score_dist["excellent"] += 1
            elif p >= 40: score_dist["passed"] += 1
            else: score_dist["failed"] += 1

        class_avg = round(sum(pcts) / len(pcts), 1)
        passed = score_dist["excellent"] + score_dist["passed"]
        pass_rate = round(passed / len(results) * 100, 1)
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
        # Get exam details
        exam = conn.execute("""
            SELECT e.*, s.subject_name, s.branch, s.semester 
            FROM exams e
            JOIN subjects s ON e.subject_id = s.id
            WHERE e.course_code = ? AND s.faculty_id = (SELECT id FROM faculty_details WHERE user_id = ?)
        """, (course_code, session["user_id"])).fetchone()

        if not exam:
            flash("Exam not found.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        # Get all attempts
        results = conn.execute("""
            SELECT ea.*, sd.full_name, sd.enrollment_no
            FROM exam_attempts ea
            JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
            WHERE ea.course_code = ? AND ea.completed = 1
            ORDER BY ea.score DESC
        """, (course_code,)).fetchall()

        results_dicts = [dict(r) for r in results]
        
        # Calculate analytics
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
    faculty = conn.execute("SELECT * FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
    results = conn.execute("""
        SELECT sd.full_name, sd.enrollment_no,
               e.exam_name, s.subject_name, s.branch, s.semester,
               e.total_marks, ea.score,
               CASE WHEN e.total_marks > 0
                    THEN ROUND(CAST(ea.score AS FLOAT) / e.total_marks * 100, 1)
                    ELSE 0 END AS percentage
        FROM exam_attempts ea
        JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
        JOIN exams e ON ea.course_code = e.course_code
        JOIN subjects s ON e.subject_id = s.id
        WHERE s.faculty_id = ? AND ea.completed = 1
        ORDER BY e.exam_name, ea.score DESC
    """, (faculty["id"],)).fetchall()
    conn.close()

    si = StringIO()
    writer = csv_module.writer(si)
    writer.writerow(["Student Name", "Enrollment No", "Exam", "Subject", "Branch", "Semester", "Total Marks", "Score", "Percentage (%)"])
    for r in results:
        writer.writerow([r["full_name"], r["enrollment_no"], r["exam_name"],
                         r["subject_name"], r["branch"], r["semester"],
                         r["total_marks"], r["score"], r["percentage"]])

    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=exam_results.csv"}
    )


    pcts = [a["pct"] for a in attempts]
    pass_mark = exam["pass_percentage"] or 40
    passed = sum(1 for p in pcts if p >= pass_mark)
    return jsonify({
        "count": len(pcts),
        "avg": round(sum(pcts)/len(pcts), 1),
        "high": max(pcts),
        "low": min(pcts),
        "pass_count": passed,
        "fail_count": len(pcts) - passed,
        "students": [dict(a) for a in attempts[:8]]
    })





import csv
from io import StringIO
from werkzeug.security import generate_password_hash


@faculty_bp.route("/faculty/question/delete/<int:question_id>", methods=["POST"])
def delete_question(question_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    faculty = conn.execute("SELECT id FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
    # Verify ownership before delete
    q = conn.execute("""
        SELECT q.id, q.subject_id FROM questions q
        JOIN subjects s ON q.subject_id = s.id
        WHERE q.id = ? AND s.faculty_id = ?
    """, (question_id, faculty["id"])).fetchone()

    if q:
        subject_id = q["subject_id"]
        conn.execute("DELETE FROM options WHERE question_id = ?", (question_id,))
        conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
        conn.commit()
        flash("Question deleted.", "success")
        conn.close()
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
    else:
        flash("Question not found or access denied.", "danger")
        conn.close()
        return redirect(url_for("faculty_bp.faculty_exams"))

@faculty_bp.route("/faculty/students/add", methods=["POST"])
def add_student_manual():
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    full_name = request.form.get("full_name", "").strip()
    enrollment_no = request.form.get("enrollment_no", "").strip()
    branch = request.form.get("branch", "").strip()
    semester = request.form.get("semester", type=int)
    course_code = request.form.get("course_code", "").strip()
    
    if not all([full_name, enrollment_no, branch, semester]):
        flash("Essential fields are required.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    conn = get_connection()
    try:
        cursor = conn.cursor()
        # Check if student already exists
        student = cursor.execute("SELECT enrollment_no FROM student_details WHERE enrollment_no = ?", (enrollment_no,)).fetchone()
        
        if not student:
            # Check if user already exists
            user = cursor.execute("SELECT id FROM users WHERE username = ?", (enrollment_no,)).fetchone()
            if not user:
                # Create user
                hashed_pw = generate_password_hash("student123")
                cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, 'student')", (enrollment_no, hashed_pw))
                user_id = cursor.lastrowid
            else:
                user_id = user["id"]
                
            # Create student details
            cursor.execute("""
                INSERT INTO student_details (user_id, full_name, enrollment_no, branch, semester)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, full_name, enrollment_no, branch, semester))

        # Enroll in subject's exam (mapping subject_id to course_code)
        exam = cursor.execute("SELECT course_code FROM exams WHERE subject_id = ?", (subject_id,)).fetchone()
        if exam:
            cursor.execute("INSERT OR IGNORE INTO student_subjects (enrollment_no, course_code) VALUES (?, ?)", 
                           (enrollment_no, exam["course_code"]))
            conn.commit()
            flash(f"Student {full_name} added and enrolled successfully!", "success")
        else:
            conn.commit()
            flash(f"Student {full_name} added but no active exam found for this subject to enroll in.", "warning")
            
    except Exception as e:
        flash(f"Error adding student: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_students"))

@faculty_bp.route("/faculty/students/upload_csv", methods=["POST"])
def upload_students_csv():
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    if 'csv_file' not in request.files:
        flash("No file uploaded.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    file = request.files['csv_file']
    subject_id = request.form.get("subject_id", type=int)

    if file.filename == '':
        flash("No file selected.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))
        
    if not subject_id:
        flash("Subject selection is required.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    if not file.filename.endswith('.csv'):
        flash("Invalid file format. Please upload a CSV file.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    try:
        stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.DictReader(stream)
        
        conn = get_connection()
        cursor = conn.cursor()
        added_count = 0
        
        for row in csv_input:
            full_name = row.get('full_name', '').strip()
            enrollment_no = row.get('enrollment_no', '').strip()
            branch = row.get('branch', '').strip()
            semester = row.get('semester', '').strip()
            
            if not all([full_name, enrollment_no, branch, semester]):
                continue
                
            # Check student
            student = cursor.execute("SELECT enrollment_no FROM student_details WHERE enrollment_no = ?", (enrollment_no,)).fetchone()
            if not student:
                # Check user
                user = cursor.execute("SELECT id FROM users WHERE username = ?", (enrollment_no,)).fetchone()
                if not user:
                    hashed_pw = generate_password_hash("Password@123")
                    cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, 'student')", (enrollment_no, hashed_pw))
                    user_id = cursor.lastrowid
                else:
                    user_id = user["id"]
                
                cursor.execute("""
                    INSERT INTO student_details (user_id, full_name, enrollment_no, branch, semester)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, full_name, enrollment_no, branch, int(semester)))

            # Enroll in subject's exam
            exam = cursor.execute("SELECT course_code FROM exams WHERE subject_id = ?", (subject_id,)).fetchone()
            if exam:
                cursor.execute("INSERT OR IGNORE INTO student_subjects (enrollment_no, course_code) VALUES (?, ?)", 
                               (enrollment_no, exam["course_code"]))
                added_count += 1
            
        conn.commit()
        flash(f"Successfully processed {added_count} students via CSV.", "success")
    except Exception as e:
        flash(f"Error processing CSV: {str(e)}", "danger")
    finally:
        if 'conn' in locals():
            conn.close()

    return redirect(url_for("faculty_bp.faculty_students"))

@faculty_bp.route("/faculty/students/edit/<string:enrollment_no>", methods=["POST"])
def edit_student(enrollment_no):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    full_name = request.form.get("full_name", "").strip()
    branch = request.form.get("branch", "").strip()
    semester = request.form.get("semester", type=int)

    if not all([full_name, branch, semester]):
        flash("All fields are required.", "danger")
        return redirect(url_for("faculty_bp.faculty_students"))

    conn = get_connection()
    try:
        conn.execute("""
            UPDATE student_details 
            SET full_name = ?, branch = ?, semester = ?
            WHERE enrollment_no = ?
        """, (full_name, branch, semester, enrollment_no))
        conn.commit()
        flash("Student details updated successfully.", "success")
    except Exception as e:
        flash(f"Error updating student: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_students"))

@faculty_bp.route("/faculty/students/delete/<string:enrollment_no>", methods=["POST"])
def delete_student(enrollment_no):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))

    conn = get_connection()
    try:
        # First check if the student is assigned to any exams controlled by this faculty
        faculty_exams = conn.execute("""
            SELECT course_code FROM exams 
            WHERE subject_id IN (SELECT id FROM subjects WHERE faculty_id = (SELECT id FROM faculty_details WHERE user_id = ?))
        """, (session["user_id"],)).fetchall()
        
        course_codes = [e["course_code"] for e in faculty_exams]
        
        if course_codes:
            placeholders = ','.join('?' for _ in course_codes)
            # Remove from faculty's subjects/exams
            conn.execute(f"DELETE FROM student_subjects WHERE enrollment_no = ? AND course_code IN ({placeholders})", [enrollment_no] + course_codes)
            
            # Check if student is still enrolled in ANY other exams
            remaining = conn.execute("SELECT COUNT(*) FROM student_subjects WHERE enrollment_no = ?", (enrollment_no,)).fetchone()[0]
            if remaining == 0:
                # Fully delete the student and user record if they have no other subjects
                student_data = conn.execute("SELECT user_id FROM student_details WHERE enrollment_no = ?", (enrollment_no,)).fetchone()
                if student_data:
                    user_id = student_data["user_id"]
                    conn.execute("DELETE FROM student_details WHERE enrollment_no = ?", (enrollment_no,))
                    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                
            conn.commit()
            flash("Student successfully removed from your cohort.", "success")
        else:
            flash("No courses found to remove student from.", "warning")
    except Exception as e:
        flash(f"Error removing student: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for("faculty_bp.faculty_students"))



@faculty_bp.route("/faculty/question/save/<int:subject_id>", methods=["POST"])
@faculty_bp.route("/faculty/question/save/<int:subject_id>/<int:question_id>", methods=["POST"])
def save_question(subject_id, question_id=None):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
    
    question_text = request.form.get("question_text", "").strip()
    marks = request.form.get("marks", 1, type=int)
    difficulty = request.form.get("difficulty", "medium").strip()
    
    conn = get_connection()
    try:
        # Check faculty access to subject
        faculty = conn.execute("SELECT id FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
        subject = conn.execute("SELECT id FROM subjects WHERE id = ? AND faculty_id = ?", (subject_id, faculty["id"])).fetchone()
        
        if not subject:
            flash("Access denied or subject not found.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))

        if question_id:
            # UPDATE
            conn.execute("UPDATE questions SET question_text = ?, marks = ?, difficulty = ? WHERE id = ?", (question_text, marks, difficulty, question_id))
            # Refresh options
            conn.execute("DELETE FROM options WHERE question_id = ?", (question_id,))
            id_to_use = question_id
        else:
            # INSERT
            cursor = conn.cursor()
            cursor.execute("INSERT INTO questions (subject_id, question_text, marks, difficulty) VALUES (?, ?, ?, ?)", (subject_id, question_text, marks, difficulty))
            id_to_use = cursor.lastrowid
            
        # Insert options
        for i in range(1, 5):
            opt_text = request.form.get(f"option_{i}", "").strip()
            is_correct = 1 if request.form.get("correct_option") == str(i) else 0
            if opt_text:
                conn.execute("INSERT INTO options (question_id, option_text, is_correct) VALUES (?, ?, ?)", (id_to_use, opt_text, is_correct))
        
        conn.commit()
        flash("Question saved successfully!", "success")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
    except Exception as e:
        flash(f"Error saving question: {str(e)}", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
    finally:
        conn.close()

@faculty_bp.route("/faculty/question/delete_inline/<int:question_id>", methods=["POST"])
def delete_question_inline(question_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
    
    conn = get_connection()
    try:
        faculty = conn.execute("SELECT id FROM faculty_details WHERE user_id = ?", (session["user_id"],)).fetchone()
        q = conn.execute("SELECT q.id, q.subject_id FROM questions q JOIN subjects s ON q.subject_id = s.id WHERE q.id = ? AND s.faculty_id = ?", (question_id, faculty["id"])).fetchone()
        
        if q:
            subject_id = q["subject_id"]
            conn.execute("DELETE FROM options WHERE question_id = ?", (question_id,))
            conn.execute("DELETE FROM exam_questions WHERE question_id = ?", (question_id,))
            conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
            conn.commit()
            flash("Question deleted successfully.", "success")
            return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
        else:
            flash("Question not found or access denied.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))
    except Exception as e:
        flash(f"Error deleting question: {str(e)}", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
    finally:
        conn.close()

@faculty_bp.route("/faculty/subject/<int:subject_id>/questions/upload", methods=["POST"])
def upload_questions_csv_alternate(subject_id):
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
        
    if 'csv_file' not in request.files:
        flash("No file uploaded.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
        
    file = request.files['csv_file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash("Invalid file format. Please upload a CSV.", "danger")
        return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))
        
    try:
        results = _process_questions_csv(subject_id, file)
        added_count = len(results)
        flash(f"Successfully uploaded {added_count} questions.", "success")
    except Exception as e:
        flash(f"Error processing CSV: {str(e)}", "danger")
        
    return redirect(url_for("faculty_bp.manage_questions", subject_id=subject_id))

@faculty_bp.route("/faculty/exam/<string:course_code>/upload_questions", methods=["POST"])
def upload_exam_questions_csv(course_code):
    """Upload questions directly into an existing exam."""
    if not session.get("user_id") or session.get("role") != "faculty":
        return redirect(url_for("auth_bp.faculty_login"))
        
    if 'csv_file' not in request.files:
        flash("No file uploaded.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
        
    file = request.files['csv_file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        flash("Invalid file format.", "danger")
        return redirect(url_for("faculty_bp.faculty_exams"))
        
    conn = get_connection()
    try:
        # Get subject_id for this exam
        exam = conn.execute("SELECT subject_id FROM exams WHERE course_code = ?", (course_code,)).fetchone()
        if not exam:
            flash("Exam not found.", "danger")
            return redirect(url_for("faculty_bp.faculty_exams"))
        
        subject_id = exam["subject_id"]
        # Process and link
        results = _process_questions_csv(subject_id, file)
        
        cursor = conn.cursor()
        for qid, section in results:
            cursor.execute("INSERT OR IGNORE INTO exam_questions (course_code, question_id, section) VALUES (?, ?, ?)", (course_code, qid, section))
        
        conn.commit()
        flash(f"Successfully added {len(results)} questions to the exam.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()
        
    return redirect(url_for("faculty_bp.faculty_exams"))


@faculty_bp.route("/faculty/exam/<string:course_code>/auto-classify-questions", methods=["POST"])
def auto_classify_exam_questions(course_code):
    # AI feature disabled for now
    return jsonify({"error": "AI feature disabled"}), 410

@faculty_bp.route("/faculty/api/detect-difficulty", methods=["POST"])
def api_detect_difficulty():
    # AI feature disabled for now
    return jsonify({"error": "AI feature disabled"}), 410

