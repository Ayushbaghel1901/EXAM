from flask import Blueprint, render_template, session, redirect, url_for, flash
from database import get_connection

faculty_analysis_bp = Blueprint('faculty_analysis_bp', __name__)

@faculty_analysis_bp.route('/faculty/analysis')
def faculty_analysis():
    if not session.get('user_id') or session.get('role') != 'faculty':
        flash('Please login first.', 'danger')
        return redirect(url_for('auth_bp.faculty_login'))
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Get faculty details
    cursor.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = cursor.fetchone()
    if not faculty:
        conn.close()
        flash("Faculty profile not found.", "danger")
        return redirect(url_for("auth_bp.faculty_login"))

    # Get subjects taught by this faculty
    cursor.execute("SELECT id, subject_name FROM subjects WHERE faculty_id = %s", (faculty["id"],))
    subjects = cursor.fetchall()
    subject_ids = [s["id"] for s in subjects]
    
    if not subject_ids:
        conn.close()
        return render_template('faculty_analysis.html', 
                               faculty=dict(faculty), 
                               subjects=[], 
                               exam_performance=[], 
                               student_trends=[], 
                               class_avg=0)

    placeholders = ','.join('%s' for _ in subject_ids)
    
    # 1. Exam Performance (Average Scores per Exam)
    cursor.execute(f"""
        SELECT e.exam_name, e.course_code, AVG(CAST(ea.score AS FLOAT) / e.total_marks * 100) as avg_pct, COUNT(ea.id) as attempts
        FROM exams e
        JOIN exam_attempts ea ON e.course_code = ea.course_code
        WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
        GROUP BY e.course_code, e.exam_name, e.exam_date
        ORDER BY e.exam_date ASC
    """, tuple(subject_ids))
    exam_performance = cursor.fetchall()
    
    # 2. Student-wise Overall Performance
    cursor.execute(f"""
        SELECT sd.full_name, sd.enrollment_no, 
               AVG(CAST(ea.score AS FLOAT) / e.total_marks * 100) as avg_pct,
               COUNT(ea.id) as exams_taken,
               MAX(CAST(ea.score AS FLOAT) / e.total_marks * 100) as best_pct
        FROM student_details sd
        JOIN exam_attempts ea ON sd.enrollment_no = ea.enrollment_no
        JOIN exams e ON ea.course_code = e.course_code
        WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
        GROUP BY sd.enrollment_no, sd.full_name
        ORDER BY avg_pct DESC
    """, tuple(subject_ids))
    student_performance = cursor.fetchall()

    # 3. Subject-wise Comparison
    cursor.execute(f"""
        SELECT s.subject_name, AVG(CAST(ea.score AS FLOAT) / e.total_marks * 100) as avg_pct
        FROM subjects s
        JOIN exams e ON s.id = e.subject_id
        JOIN exam_attempts ea ON e.course_code = ea.course_code
        WHERE s.faculty_id = %s AND ea.completed = 1
        GROUP BY s.id, s.subject_name
    """, (faculty["id"],))
    subject_performance = cursor.fetchall()

    # 4. Class Overall Average
    cursor.execute(f"""
        SELECT AVG(CAST(ea.score AS FLOAT) / e.total_marks * 100) AS overall_avg
        FROM exam_attempts ea
        JOIN exams e ON ea.course_code = e.course_code
        WHERE e.subject_id IN ({placeholders}) AND ea.completed = 1
    """, tuple(subject_ids))
    class_avg_row = cursor.fetchone()
    class_avg = round(class_avg_row["overall_avg"], 1) if class_avg_row and class_avg_row["overall_avg"] else 0

    conn.close()
    
    student_performance_dicts = [dict(row) for row in student_performance]
    passing_count = sum(1 for s in student_performance_dicts if (s["avg_pct"] or 0) >= 40)
    success_rate = round(passing_count / len(student_performance_dicts) * 100, 1) if student_performance_dicts else 0

    return render_template('faculty_analysis.html', 
                           faculty=dict(faculty), 
                           subjects=[dict(row) for row in subjects],
                           exam_performance=[dict(row) for row in exam_performance],
                           student_performance=student_performance_dicts,
                           subject_performance=[dict(row) for row in subject_performance],
                           class_avg=class_avg,
                           success_rate=success_rate)

@faculty_analysis_bp.route('/faculty/analysis/exam/<string:course_code>')
def faculty_exam_report(course_code):
    if not session.get('user_id') or session.get('role') != 'faculty':
        flash('Please login first.', 'danger')
        return redirect(url_for('auth_bp.faculty_login'))
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Get Exam Details and Stats
    cursor.execute("""
        SELECT e.*, s.subject_name,
               (SELECT COUNT(*) FROM exam_attempts WHERE course_code = e.course_code AND completed = 1) as total_attempts,
               (SELECT AVG(CAST(score AS FLOAT) / e.total_marks * 100) FROM exam_attempts WHERE course_code = e.course_code AND completed = 1) as avg_pct,
               (SELECT MAX(CAST(score AS FLOAT) / e.total_marks * 100) FROM exam_attempts WHERE course_code = e.course_code AND completed = 1) as max_pct,
               (SELECT MIN(CAST(score AS FLOAT) / e.total_marks * 100) FROM exam_attempts WHERE course_code = e.course_code AND completed = 1) as min_pct
        FROM exams e
        JOIN subjects s ON e.subject_id = s.id
        WHERE e.course_code = %s
    """, (course_code,))
    exam = cursor.fetchone()
    
    if not exam:
        conn.close()
        flash("Exam not found.", "danger")
        return redirect(url_for("faculty_analysis_bp.faculty_analysis"))

    # 2. Score Distribution (Histogram data)
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN (CAST(score AS FLOAT) / %s * 100) < 40 THEN 1 ELSE 0 END) as fail,
            SUM(CASE WHEN (CAST(score AS FLOAT) / %s * 100) BETWEEN 40 AND 60 THEN 1 ELSE 0 END) as average,
            SUM(CASE WHEN (CAST(score AS FLOAT) / %s * 100) BETWEEN 60 AND 80 THEN 1 ELSE 0 END) as good,
            SUM(CASE WHEN (CAST(score AS FLOAT) / %s * 100) >= 80 THEN 1 ELSE 0 END) as excellent
        FROM exam_attempts
        WHERE course_code = %s AND completed = 1
    """, (exam['total_marks'], exam['total_marks'], exam['total_marks'], exam['total_marks'], course_code))
    distribution = cursor.fetchone()

    # 3. List of Student Performances for this Exam
    cursor.execute("""
        SELECT sd.full_name, sd.enrollment_no, ea.score, 
               (CAST(ea.score AS FLOAT) / %s * 100) as pct,
               ea.attempt_time
        FROM exam_attempts ea
        JOIN student_details sd ON ea.enrollment_no = sd.enrollment_no
        WHERE ea.course_code = %s AND ea.completed = 1
        ORDER BY score DESC
    """, (exam['total_marks'], course_code))
    students = cursor.fetchall()

    cursor.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = cursor.fetchone()
    
    conn.close()
    
    return render_template('faculty_exam_report.html', 
                           exam=dict(exam), 
                           dist=dict(distribution), 
                           students=[dict(row) for row in students],
                           faculty=dict(faculty))

@faculty_analysis_bp.route('/faculty/analysis/power-bi')
def faculty_powerbi():
    if not session.get('user_id') or session.get('role') != 'faculty':
        return redirect(url_for('auth_bp.faculty_login'))
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
    faculty = cursor.fetchone()
    conn.close()
    
    return render_template('faculty_powerbi.html', faculty=dict(faculty))



