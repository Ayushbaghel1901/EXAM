from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from database import get_connection, get_user_by_username, get_user_by_email_any_role, update_user_password
import smtplib
import random
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

auth_bp = Blueprint('auth_bp', __name__)

# ─── Email configuration ───────────
MAIL_HOST     = "smtp.gmail.com"
MAIL_PORT     = 587
MAIL_USERNAME = "ayush2005baghel@gmail.com"
MAIL_PASSWORD = "fbfywnxicyrfpcxi"
MAIL_FROM     = "Online Exam Portal <ayush2005baghel@gmail.com>"


# ================= STUDENT LOGIN =================
@auth_bp.route("/student/login", methods=["GET", "POST"])
def student_login():
    if session.get("user_id"):
        if session.get("role") == "student":
            return redirect(url_for("student_bp.student_dashboard"))
        elif session.get("role") == "faculty":
            return redirect(url_for("faculty_bp.faculty_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please fill all fields.", "danger")
            return render_template("student_login.html")

        user = get_user_by_username(username, "student")

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = "student"
            return redirect(url_for("student_bp.student_dashboard"))
        else:
            flash("Invalid Enrollment Number or Password.", "danger")

    return render_template("student_login.html")

# ================= TEACHER LOGIN =================
@auth_bp.route("/faculty/login", methods=["GET", "POST"])
def faculty_login():
    if session.get("user_id"):
        if session.get("role") == "faculty":
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        elif session.get("role") == "student":
            return redirect(url_for("student_bp.student_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please fill all fields.", "danger")
            return render_template("faculty_login.html")

        user = get_user_by_username(username, "faculty")

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = "faculty"
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        else:
            flash("Invalid Credentials.", "danger")

    return render_template("faculty_login.html")

# ================= ADMIN LOGIN =================
@auth_bp.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("user_id"):
        if session.get("role") == "admin":
            return redirect(url_for("admin_bp.admin_dashboard"))
        elif session.get("role") == "faculty":
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        elif session.get("role") == "student":
            return redirect(url_for("student_bp.student_dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please fill all fields.", "danger")
            return render_template("admin_login.html")

        user = get_user_by_username(username, "admin")

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = "admin"
            return redirect(url_for("admin_bp.admin_dashboard"))
        else:
            flash("Invalid Credentials.", "danger")

    return render_template("admin_login.html")

# ================= FORGOT PASSWORD =================
@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Please enter your registered email address.", "danger")
            return render_template("forgot_password.html")

        user = get_user_by_email_any_role(email)
        if not user:
            flash("No account found with that email address.", "danger")
            return render_template("forgot_password.html")

        # Generate 6-digit OTP
        otp = str(random.randint(100000, 999999))
        session["otp"]           = otp
        session["otp_email"]     = email
        session["otp_user_id"]   = user["id"]
        session["otp_timestamp"] = time.time()

        # Send OTP email
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Your OTP for Password Reset"
            msg["From"]    = MAIL_FROM
            msg["To"]      = email

            html_body = f"""
            <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:30px;
                        background:#0f172a;color:#e2e8f0;border-radius:16px;">
              <h2 style="text-align:center;color:#818cf8;">Online Exam Portal</h2>
              <p style="text-align:center;font-size:15px;color:#94a3b8;">Password Reset Request</p>
              <div style="background:#1e293b;border-radius:12px;padding:20px;margin:20px 0;text-align:center;">
                <p style="margin:0 0 8px;font-size:14px;color:#94a3b8;">Your One-Time Password (OTP) is:</p>
                <span style="font-size:36px;font-weight:bold;letter-spacing:8px;
                            color:#818cf8;">{otp}</span>
                <p style="margin:12px 0 0;font-size:13px;color:#64748b;">Valid for 10 minutes only.</p>
              </div>
              <p style="font-size:13px;color:#64748b;text-align:center;">
                If you did not request this, please ignore this email.
              </p>
            </div>
            """
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as server:
                server.starttls()
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.sendmail(MAIL_USERNAME, email, msg.as_string())

            flash("OTP sent to your registered email address. Check your inbox.", "success")
        except Exception as e:
            print(f"[DEV] OTP for {email}: {otp}")
            flash(f"Could not send email. DEV OTP printed to console.", "danger")

        return redirect(url_for("auth_bp.verify_otp"))

    return render_template("forgot_password.html")

@auth_bp.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if "otp" not in session:
        flash("Please start the password reset process first.", "danger")
        return redirect(url_for("auth_bp.forgot_password"))

    if request.method == "POST":
        entered_otp = request.form.get("otp", "").strip()

        if time.time() - session.get("otp_timestamp", 0) > 600:
            session.pop("otp", None)
            flash("OTP has expired. Please request a new one.", "danger")
            return redirect(url_for("auth_bp.forgot_password"))

        if entered_otp == session.get("otp"):
            session["otp_verified"] = True
            session.pop("otp", None)
            return redirect(url_for("auth_bp.reset_password"))
        else:
            flash("Incorrect OTP. Please try again.", "danger")

    return render_template("verify_otp.html", email=session.get("otp_email", ""))

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if not session.get("otp_verified"):
        flash("Please verify your OTP first.", "danger")
        return redirect(url_for("auth_bp.forgot_password"))

    if request.method == "POST":
        new_password     = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not new_password or not confirm_password:
            flash("Please fill in all fields.", "danger")
            return render_template("reset_password.html")

        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html")

        if len(new_password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("reset_password.html")

        user_id = session.get("otp_user_id")
        update_user_password(user_id, new_password)

        for key in ["otp", "otp_email", "otp_user_id", "otp_timestamp", "otp_verified"]:
            session.pop(key, None)

        flash("Password reset successfully! You can now log in.", "success")
        return redirect(url_for("auth_bp.student_login"))

    return render_template("reset_password.html")

# ================= LOGOUT =================
@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth_bp.student_login"))
