import os
import random
import time
import smtplib
from flask import session
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── Email Configuration ───────────
MAIL_HOST     = os.environ.get("MAIL_HOST", "smtp.gmail.com")
MAIL_PORT     = int(os.environ.get("MAIL_PORT", 587))
MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
MAIL_FROM     = os.environ.get("MAIL_FROM", "Online Exam Portal <noreply@institute.edu.in>")

def init_otp_session(user_id, email):
    """
    Clears existing OTP state and initializes a new 6-digit OTP session.
    """
    # Keys to clear (Prevents state contamination / multiple active OTPs)
    otp_keys = ["otp", "otp_email", "otp_user_id", "otp_timestamp", "otp_verified", "resend_timestamp"]
    for key in otp_keys:
        session.pop(key, None)

    # Generate 6-digit OTP
    otp = str(random.randint(100000, 999999))
    
    # Store in session
    session["otp"]              = otp
    session["otp_email"]        = email
    session["otp_user_id"]      = user_id
    session["otp_timestamp"]    = time.time()
    session["resend_timestamp"] = time.time() # Track cooldown for resend
    
    return otp

def send_otp_email(email, otp):
    """
    Sends a premium-styled security notification containing the OTP.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your OTP for Password Reset"
        msg["From"]    = MAIL_FROM
        msg["To"]      = email

        html_body = f"""
        <div style="font-family:'Segoe UI', sans-serif; max-width: 550px; margin: 40px auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;">
            <div style="background: linear-gradient(135deg, #4f46e5, #7c3aed); padding: 40px 20px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 28px; letter-spacing: -0.5px; font-weight: 700;">Online Exam Portal</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0; font-size: 16px;">Security Verification</p>
            </div>
            <div style="padding: 40px; color: #1e293b; line-height: 1.6;">
                <h2 style="margin-top: 0; font-size: 20px; font-weight: 600; color: #0f172a;">Password Reset Request</h2>
                <p style="font-size: 15px; color: #475569;">Hello,</p>
                <p style="font-size: 15px; color: #475569;">We received a request to reset your password. Use the following <strong>One-Time Password (OTP)</strong> to proceed. This code is valid for <strong>10 minutes</strong>.</p>
                
                <div style="margin: 35px 0; background-color: #f8fafc; border: 2px dashed #cbd5e1; border-radius: 12px; padding: 25px; text-align: center;">
                    <span style="display: block; font-size: 42px; font-weight: 800; color: #4f46e5; letter-spacing: 12px; font-family: monospace;">{otp}</span>
                </div>

                <p style="font-size: 14px; color: #64748b; margin-top: 30px;">If you didn't request a password reset, you can safely ignore this email.</p>
            </div>
            <div style="background-color: #f1f5f9; padding: 20px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="margin: 0; font-size: 13px; color: #94a3b8;">&copy; 2024 Online Exam Portal. All rights reserved.</p>
            </div>
        </div>
        """
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_USERNAME, email, msg.as_string())
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send email to {email}: {e}")
        return False

def send_reset_confirmation_email(email):
    """
    Sends a confirmation email after a successful password reset.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Password Reset Successful"
        msg["From"]    = MAIL_FROM
        msg["To"]      = email

        html_body = f"""
        <div style="font-family:'Segoe UI', sans-serif; max-width: 550px; margin: 40px auto; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.1); border: 1px solid #e2e8f0;">
            <div style="background: #10b981; padding: 30px 20px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 24px;">Success!</h1>
            </div>
            <div style="padding: 40px; color: #1e293b; line-height: 1.6;">
                <h2 style="margin-top: 0; font-size: 20px; font-weight: 600; color: #0f172a;">Password Updated</h2>
                <p style="font-size: 15px; color: #475569;">Hello,</p>
                <p style="font-size: 15px; color: #475569;">This is a confirmation that your password for the <strong>Online Exam Portal</strong> has been successfully reset.</p>
                <p style="font-size: 15px; color: #475569; margin-top: 20px;">If you did not perform this action, please contact your administrator immediately.</p>
            </div>
            <div style="background-color: #f1f5f9; padding: 20px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="margin: 0; font-size: 13px; color: #94a3b8;">&copy; 2024 Online Exam Portal.</p>
            </div>
        </div>
        """
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.sendmail(MAIL_USERNAME, email, msg.as_string())
        return True
    except:
        return False
