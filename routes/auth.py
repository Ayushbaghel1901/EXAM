from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from database import get_connection, get_user_by_username, get_user_by_email_any_role, update_user_password
from routes.logger import log_event, LOGIN_SUCCESS, LOGIN_FAILED, LOGOUT, OTP_SENT, OTP_VERIFIED, OTP_FAILED, PASSWORD_RESET
import random
import time
import os
from utils.auth_utils import init_otp_session, send_otp_email, send_reset_confirmation_email


auth_bp = Blueprint('auth_bp', __name__)

# Email configuration moved to utils/auth_utils.py


# ================= STUDENT LOGIN =================
@auth_bp.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "GET" and session.get("user_id"):
        # Double check if session is actually valid
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM student_details WHERE user_id = %s", (session["user_id"],))
        profile = cur.fetchone()
        conn.close()

        if profile and session.get("role") == "student":
            return redirect(url_for("student_bp.student_dashboard"))
        else:
            # Surgical clearing
            session.pop("user_id", None)
            session.pop("role", None)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please fill all fields.", "danger")
            return render_template("student_login.html")

        user = get_user_by_username(username, "student")

        if user and check_password_hash(user["password"], password):
            # Verify student details exist
            from database import get_student_by_user_id
            student = get_student_by_user_id(user["id"])
            if not student:
                flash("Student profile not found. Please contact administration.", "danger")
                return render_template("student_login.html")

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = "student"
            log_event(
                event_type=LOGIN_SUCCESS,
                description=f"Student '{username}' logged in successfully.",
                actor_id=user["id"],
                actor_role="student",
                request=request
            )
            return redirect(url_for("student_bp.student_dashboard"))
        else:
            log_event(
                event_type=LOGIN_FAILED,
                description=f"Failed student login attempt for username '{username}'.",
                metadata={"attempted_username": username},
                request=request
            )
            flash("Invalid Enrollment Number or Password.", "danger")

    return render_template("student_login.html")

# ================= TEACHER LOGIN =================
@auth_bp.route("/faculty/login", methods=["GET", "POST"])
def faculty_login():
    if request.method == "GET" and session.get("user_id"):
        # Double check if session is actually valid (has a profile)
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM faculty_details WHERE user_id = %s", (session["user_id"],))
        profile = cur.fetchone()
        conn.close()

        if profile and session.get("role") == "faculty":
            print(f"[DEBUG] Valid faculty session found for {session.get('username')}, redirecting to dashboard")
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        else:
            print(f"[DEBUG] Profile check failed in GET /faculty/login for user_id {session.get('user_id')}. profile={profile}, role={session.get('role')}")
            # Surgical clearing – avoid session.clear() to preserve flashes
            session.pop("user_id", None)
            session.pop("role", None)
            # Let them land on the login page normally now

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Please fill all fields.", "danger")
            return render_template("faculty_login.html")

        # ONLY faculty login here. Admin has its own MFA-enabled login.
        user = get_user_by_username(username, "faculty")
        role = "faculty"

        if user and check_password_hash(user["password"], password):
            # Verify faculty details exist
            from database import get_faculty_by_user_id
            faculty = get_faculty_by_user_id(user["id"])
            if not faculty:
                print(f"[DEBUG] Login succeeded but profile missing for user_id {user['id']} ({username})")
                flash("Faculty profile not found. Please contact administration.", "danger")
                return render_template("faculty_login.html")

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = role
            print(f"[DEBUG] POST /faculty/login success. user_id={session['user_id']}, role={session['role']}, name={faculty['full_name']}")
            log_event(
                event_type=LOGIN_SUCCESS,
                description=f"Faculty '{username}' logged in successfully.",
                actor_id=user["id"],
                actor_role="faculty",
                request=request
            )
            return redirect(url_for("faculty_bp.faculty_dashboard"))
        else:
            log_event(
                event_type=LOGIN_FAILED,
                description=f"Failed faculty login attempt for username '{username}'.",
                metadata={"attempted_username": username},
                request=request
            )
            flash("Invalid Credentials.", "danger")

    return render_template("faculty_login.html")

# send_otp_email moved to utils/auth_utils.py

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

        # Initialize OTP session and send email using utils
        otp = init_otp_session(user["id"], email)
        
        log_event(
            event_type=OTP_SENT,
            description=f"Password reset OTP sent to '{email}'.",
            actor_id=user["id"],
            actor_role=user.get("role"),
            metadata={"email": email},
            request=request
        )

        if send_otp_email(email, otp):
            flash("OTP sent to your registered email address. Check your inbox.", "success")
        else:
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
            log_event(
                event_type=OTP_VERIFIED,
                description=f"OTP verified successfully for '{session.get('otp_email')}'.",
                actor_id=session.get("otp_user_id"),
                metadata={"email": session.get("otp_email")},
                request=request
            )
            return redirect(url_for("auth_bp.reset_password"))
        else:
            log_event(
                event_type=OTP_FAILED,
                description=f"Incorrect OTP entered for '{session.get('otp_email')}'.",
                actor_id=session.get("otp_user_id"),
                metadata={"email": session.get("otp_email")},
                request=request
            )
            flash("Incorrect OTP. Please try again.", "danger")

    return render_template("verify_otp.html", email=session.get("otp_email", ""))

@auth_bp.route("/resend-otp")
def resend_otp():
    if "otp_email" not in session or "otp_user_id" not in session:
        flash("Session expired. Please start again.", "danger")
        return redirect(url_for("auth_bp.forgot_password"))
    
    # Anti-spam cooldown: 60 seconds
    last_resend = session.get("resend_timestamp", 0)
    time_elapsed = time.time() - last_resend
    
    if time_elapsed < 60:
        flash(f"Please wait {int(60 - time_elapsed)} seconds before requesting a new OTP.", "danger")
        return redirect(url_for("auth_bp.verify_otp"))

    email   = session["otp_email"]
    user_id = session["otp_user_id"]
    
    # Generate NEW OTP using utils
    otp = init_otp_session(user_id, email)
    
    log_event(
        event_type=OTP_SENT,
        description=f"Resent password reset OTP to '{email}'.",
        actor_id=user_id,
        metadata={"email": email, "is_resend": True},
        request=request
    )
    
    if send_otp_email(email, otp):
        flash("A new OTP has been sent to your email.", "success")
    else:
        print(f"[DEV RESEND] OTP for {email}: {otp}")
        flash("Could not send email. Check console for DEV code.", "danger")
        
    return redirect(url_for("auth_bp.verify_otp"))

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
        log_event(
            event_type=PASSWORD_RESET,
            description=f"Password reset successfully for user_id={user_id}.",
            actor_id=user_id,
            metadata={"email": session.get("otp_email")},
            request=request
        )

        # Send confirmation email
        email = session.get("otp_email")
        send_reset_confirmation_email(email)

        for key in ["otp", "otp_email", "otp_user_id", "otp_timestamp", "otp_verified"]:
            session.pop(key, None)

        flash("Password reset successfully! You can now log in.", "success")
        return redirect(url_for("auth_bp.student_login"))

    return render_template("reset_password.html")

# ================= LOGOUT =================
@auth_bp.route("/logout")
def logout():
    log_event(
        event_type=LOGOUT,
        description=f"User '{session.get('username')}' logged out.",
        actor_id=session.get("user_id"),
        actor_role=session.get("role"),
        request=request
    )
    session.clear()
    return redirect(url_for("auth_bp.student_login"))
