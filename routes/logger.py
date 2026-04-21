"""
routes/logger.py
─────────────────────────────────────────────────────────────────
Centralized audit logging utility for the Exam Portal.

Usage:
    from routes.logger import log_event

    log_event(
        event_type  = "LOGIN_SUCCESS",
        description = "Faculty Ayush logged in successfully.",
        actor_id    = session.get("user_id"),
        actor_role  = session.get("role"),
        target_type = "user",       # optional
        target_id   = "42",         # optional
        metadata    = {"ip": "..."}  # optional extra context
    )

Rules:
    - This function NEVER raises an exception.
      A logging failure must NEVER crash the main app.
    - Do NOT log raw passwords, OTP codes, or secret keys.
    - Metadata should hold structured, searchable context (score, course_code, etc.)
"""

import json
from database import get_connection

# ── All supported event type constants ────────────────────────────────────────
# Auth
LOGIN_SUCCESS          = "LOGIN_SUCCESS"
LOGIN_FAILED           = "LOGIN_FAILED"
LOGOUT                 = "LOGOUT"
OTP_SENT               = "OTP_SENT"
OTP_VERIFIED           = "OTP_VERIFIED"
OTP_FAILED             = "OTP_FAILED"
PASSWORD_RESET         = "PASSWORD_RESET"

# Exam lifecycle (faculty)
EXAM_CREATED           = "EXAM_CREATED"
EXAM_EDITED            = "EXAM_EDITED"
EXAM_DELETED           = "EXAM_DELETED"
EXAM_DUPLICATED        = "EXAM_DUPLICATED"
EXAM_SCHEDULED         = "EXAM_SCHEDULED"

# Exam lifecycle (student)
EXAM_STARTED           = "EXAM_STARTED"
EXAM_SUBMITTED         = "EXAM_SUBMITTED"
EXAM_EXPIRED           = "EXAM_EXPIRED"
DUPLICATE_SUBMIT       = "DUPLICATE_SUBMIT_ATTEMPT"

# Questions
QUESTION_ADDED         = "QUESTION_ADDED"
QUESTION_DELETED       = "QUESTION_DELETED"
CSV_UPLOADED           = "CSV_UPLOADED"

# Blacklist
BLACKLIST_ADDED        = "BLACKLIST_ADDED"
BLACKLIST_REMOVED      = "BLACKLIST_REMOVED"

# Admin
USER_CREATED           = "USER_CREATED"
USER_DELETED           = "USER_DELETED"
SUBJECT_ASSIGNED       = "SUBJECT_ASSIGNED"
SUBJECT_CREATED        = "SUBJECT_CREATED"
SUBJECT_EDITED         = "SUBJECT_EDITED"
SUBJECT_DELETED        = "SUBJECT_DELETED"
CSV_IMPORT             = "CSV_IMPORT"

# Security
UNAUTHORIZED_ACCESS    = "UNAUTHORIZED_ACCESS"
ROLE_MISMATCH          = "ROLE_MISMATCH"
SESSION_INVALID        = "SESSION_INVALID"


def log_event(
    event_type: str,
    description: str,
    actor_id=None,
    actor_role=None,
    target_type=None,
    target_id=None,
    metadata: dict = None,
    request=None          # pass flask.request if available
):
    """
    Write a structured event to the audit_logs table.
    Always wrapped in try/except — will never raise or crash the caller.

    Parameters
    ----------
    event_type   : One of the constants above (e.g. LOGIN_SUCCESS)
    description  : Human-readable sentence describing what happened
    actor_id     : users.id of the person who triggered the event
    actor_role   : 'student', 'faculty', or 'admin'
    target_type  : What entity was acted on: 'exam', 'user', 'question', etc.
    target_id    : The ID of that entity (course_code, enrollment_no, user_id, etc.)
    metadata     : Dict with any extra structured context (score, old_value, etc.)
    request      : The Flask request object — used to capture IP & User-Agent
    """
    try:
        ip_address = None
        user_agent = None

        if request is not None:
            ip_address = request.remote_addr
            ua = request.user_agent
            user_agent = ua.string[:255] if ua and ua.string else None

        conn = get_connection()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO audit_logs
                (event_type, actor_id, actor_role, target_type, target_id,
                 description, ip_address, user_agent, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            event_type,
            actor_id,
            actor_role,
            target_type,
            str(target_id) if target_id is not None else None,
            description,
            ip_address,
            user_agent,
            json.dumps(metadata or {})
        ))
        conn.commit()
        print(f"[AUDIT] {event_type} | {description}")

    except Exception as e:
        # Logging must NEVER crash the application.
        print(f"[LOGGER ERROR] Could not write audit log — {event_type}: {e}")

    finally:
        try:
            conn.close()
        except Exception:
            pass
