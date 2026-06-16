import os
import psycopg2
import secrets
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Response, Cookie, Depends, Request
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from database import get_db
from dotenv import load_dotenv
from security import is_admin_email

load_dotenv()   # MUST be before os.getenv
# =================================
# Router
# =================================
router = APIRouter(tags=["auth"])

# =================================
# Config
# =================================
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
SESSION_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
FRONTEND_LOGIN_URL = os.getenv("FRONTEND_LOGIN_URL")

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# =================================
# Models
# =================================
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


# =================================
# Password helpers
# =================================
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


# =================================
# Session helpers
# =================================
def create_session_token(user_id: int) -> str:
    now = datetime.utcnow()
    payload = {
        "user_id": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=SESSION_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(session: str = Cookie(None)):
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(session, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload["user_id"]
        issued_at = payload.get("iat")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # Force-logout check: if an admin invalidated this user's sessions
    # after this token was issued, reject it even though it's not expired.
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT sessions_invalidated_at, status FROM users WHERE id = %s",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    invalidated_at, status = row[0], row[1]

    if status == "blocked":
        raise HTTPException(status_code=403, detail="Account is blocked")

    if invalidated_at and issued_at is not None:
        try:
            invalidated_dt = datetime.fromisoformat(str(invalidated_at)).replace(tzinfo=timezone.utc)
            issued_dt = datetime.fromtimestamp(int(issued_at), tz=timezone.utc)
            if issued_dt < invalidated_dt:
                raise HTTPException(status_code=401, detail="Session revoked, please log in again")
        except HTTPException:
            raise
        except Exception:
            # If parsing fails, fail safe by not blocking (avoid locking everyone out
            # over a malformed timestamp), but this should be rare.
            pass

    return user_id


# =================================
# Email helpers
# =================================
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_verification_token() -> str:
    return secrets.token_urlsafe(32)


def _build_verify_link(token: str) -> str:
    base_url = os.getenv("BASE_URL")
    return f"{base_url}/verify-email%stoken={token}"


def send_verification_email(email: str, token: str) -> None:
    verify_link = _build_verify_link(token)

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = 587
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")  # Gmail App Password
    from_email = os.getenv("SMTP_FROM_EMAIL")

    msg = EmailMessage()
    msg["Subject"] = "Verify your account"
    msg["From"] = from_email
    msg["To"] = email
    msg.set_content(f"Welcome!\n\nPlease verify your email by clicking the link below:\n\n{verify_link}")

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            print("✅ Verification email sent to", email)
    except Exception as e:
        print("❌ Email sending failed:", e)


def send_password_reset_email(email: str, token: str) -> None:
    base_url = os.getenv("BASE_URL")
    reset_link = f"{base_url}/reset-password%stoken={token}"

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = 587
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    from_email = os.getenv("SMTP_FROM_EMAIL")

    msg = EmailMessage()
    msg["Subject"] = "Reset your password"
    msg["From"] = from_email
    msg["To"] = email
    msg.set_content(
        f"An administrator has triggered a password reset for your account.\n\n"
        f"Click the link below to set a new password:\n\n{reset_link}\n\n"
        f"If you did not expect this, please contact support."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
            print("✅ Password reset email sent to", email)
    except Exception as e:
        print("❌ Email sending failed:", e)


# =================================
# Auth events logging (for admin analytics + IP abuse detection)
# =================================
def _client_ip(request: Request) -> str:
    # Prefer X-Forwarded-For if behind proxy, else client.host
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


def log_auth_event(
    conn: psycopg2.extensions.connection,
    *,
    user_id: int | None,
    email: str | None,
    ip: str | None,
    action: str,
    success: bool,
    detail: str | None = None,
):
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO auth_events (user_id, email, ip, action, success, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, email, ip, action, 1 if success else 0, detail),
        )
        conn.commit()
    except Exception:
        # logging must never break auth
        pass


# =================================
# DB helpers
# =================================
def get_user_by_email(conn: psycopg2.extensions.connection, email: str):
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id,
            email,
            email_verified,
            verification_token,
            password_hash,
            google_sub,
            auth_provider
        FROM users
        WHERE email = %s
        """,
        (email,),
    )

    return cur.fetchone()


def get_user_by_google_sub(conn: psycopg2.extensions.connection, google_sub: str):
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id,
            email,
            email_verified,
            verification_token,
            password_hash,
            google_sub,
            auth_provider
        FROM users
        WHERE google_sub = %s
        """,
        (google_sub,),
    )

    return cur.fetchone()

# =================================
# Google Auth
# =================================
@router.post("/auth/google")
def google_auth(data: GoogleAuthRequest, response: Response, request: Request):
    ip = _client_ip(request)

    try:
        payload = id_token.verify_oauth2_token(data.token, google_requests.Request(), GOOGLE_CLIENT_ID)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    email = payload.get("email")
    google_sub = payload.get("sub")

    conn = get_db()
    cur = conn.cursor()

    user = get_user_by_google_sub(conn, google_sub)

    if not user:
        existing = get_user_by_email(conn, email)
        if existing:
            user_id = existing[0]
            cur.execute(
                """
                UPDATE users
                SET google_sub = %s, auth_provider = 'hybrid', email_verified = 1
                WHERE id = %s
            """,
                (google_sub, user_id),
            )
            conn.commit()
        else:
            cur.execute(
                """
                INSERT INTO users (email, google_sub, auth_provider, email_verified)
                VALUES (%s, %s, 'google', 1)
            """,
                (email, google_sub),
            )
            conn.commit()

        user = get_user_by_google_sub(conn, google_sub)

    token = create_session_token(user[0])
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_EXPIRE_MINUTES * 60,
    )

    log_auth_event(conn, user_id=user[0], email=email, ip=ip, action="google", success=True, detail="google auth")
    conn.close()

    return {"success": True}


# =================================
# Register
# =================================
@router.post("/register")
def register_user(data: RegisterRequest, request: Request):
    ip = _client_ip(request)
    conn = get_db()
    cur = conn.cursor()

    if get_user_by_email(conn, data.email):
        log_auth_event(conn, user_id=None, email=data.email, ip=ip, action="register", success=False, detail="email exists")
        conn.close()
        raise HTTPException(status_code=400, detail="Email already exists")

    token = _make_verification_token()
    cur.execute(
        """
        INSERT INTO users (email, password_hash, auth_provider,
                           email_verified, verification_token, verification_sent_at)
        VALUES (%s, %s, 'local', 0, %s, %s)
    """,
        (data.email, hash_password(data.password), token, _utcnow_iso()),
    )
    conn.commit()

    # log success
    cur.execute("SELECT id FROM users WHERE email = %s", (data.email,))
    row = cur.fetchone()
    uid = row[0] if row else None
    log_auth_event(conn, user_id=uid, email=data.email, ip=ip, action="register", success=True, detail="created")

    conn.close()

    send_verification_email(data.email, token)
    return {"success": True, "needs_verification": True}


# =================================
# Verify Email
# =================================
@router.get("/verify-email")
def verify_email(token: str, response: Response, request: Request):
    ip = _client_ip(request)
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, email
        FROM users
        WHERE verification_token = %s
    """,
        (token,),
    )
    row = cur.fetchone()

    if not row:
        log_auth_event(conn, user_id=None, email=None, ip=ip, action="verify", success=False, detail="invalid token")
        conn.close()
        return RedirectResponse(f"{FRONTEND_LOGIN_URL}%sverified=0")

    user_id, email = row[0], row[1]

    # Mark email as verified
    cur.execute(
        """
        UPDATE users
        SET email_verified = 1,
            verification_token = NULL,
            verification_sent_at = NULL
        WHERE id = %s
    """,
        (user_id,),
    )
    conn.commit()

    # ✅ Create session (log user in)
    session_token = create_session_token(user_id)

    redirect = RedirectResponse(url="/static/premium.html", status_code=302)
    redirect.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_EXPIRE_MINUTES * 60,
    )

    log_auth_event(conn, user_id=user_id, email=email, ip=ip, action="verify", success=True, detail="email verified")
    conn.close()

    return redirect


# =================================
# Login
# =================================
@router.post("/login")
def login_user(data: LoginRequest, response: Response, request: Request):
    ip = _client_ip(request)
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, password_hash, email_verified, status
        FROM users WHERE email = %s
    """,
        (data.email,),
    )
    row = cur.fetchone()

    if not row or not row[1]:
        log_auth_event(conn, user_id=None, email=data.email, ip=ip, action="login", success=False, detail="invalid credentials")
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if row[3] == "blocked":
        log_auth_event(conn, user_id=row[0], email=data.email, ip=ip, action="login", success=False, detail="account blocked")
        conn.close()
        raise HTTPException(status_code=403, detail="Account is blocked")

    if not row[2]:
        log_auth_event(conn, user_id=row[0], email=data.email, ip=ip, action="login", success=False, detail="email not verified")
        conn.close()
        raise HTTPException(status_code=403, detail="Email not verified")

    if not verify_password(data.password, row[1]):
        log_auth_event(conn, user_id=row[0], email=data.email, ip=ip, action="login", success=False, detail="wrong password")
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_session_token(row[0])
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_EXPIRE_MINUTES * 60,
    )

    log_auth_event(conn, user_id=row[0], email=data.email, ip=ip, action="login", success=True, detail="ok")
    conn.close()

    return {"success": True, "is_admin": is_admin_email(data.email)}


# =================================
# Session Check
# =================================
@router.get("/me")
def get_me(user_id: int = Depends(get_current_user)):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, email, email_verified, auth_provider
        FROM users
        WHERE id = %s
        """,
        (user_id,),
    )
    user = cur.fetchone()

    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    email = user[1]
    admin = is_admin_email(email)

    conn.close()

    return {
        "id": user[0],
        "email": email,
        "email_verified": user[2],
        "auth_provider": user[3],
        "is_admin": admin,
    }

# =================================
# Logout
# =================================
@router.post("/logout")
def logout(response: Response, request: Request):
    ip = _client_ip(request)
    response.delete_cookie("session")
    return {"success": True}


@router.post("/resend-verification")
def resend_verification(data: ResendVerificationRequest, request: Request):
    ip = _client_ip(request)
    conn = get_db()
    cur = conn.cursor()

    user = get_user_by_email(conn, data.email)

    if not user:
        log_auth_event(conn, user_id=None, email=data.email, ip=ip, action="resend", success=False, detail="email not found")
        conn.close()
        raise HTTPException(status_code=404, detail="Email not found")

    user_id, email, email_verified, old_token, *_ = user

    if email_verified:
        log_auth_event(conn, user_id=user_id, email=email, ip=ip, action="resend", success=False, detail="already verified")
        conn.close()
        raise HTTPException(status_code=400, detail="Email already verified")

    # Rate limit: 1 email every 2 minutes
    cur.execute(
        """
        SELECT verification_sent_at
        FROM users WHERE id = %s
    """,
        (user_id,),
    )
    row = cur.fetchone()

    if row and row[0]:
        last_sent = datetime.fromisoformat(row[0])
        if datetime.now(timezone.utc) - last_sent < timedelta(minutes=2):
            log_auth_event(conn, user_id=user_id, email=email, ip=ip, action="resend", success=False, detail="rate limited")
            conn.close()
            raise HTTPException(status_code=429, detail="Please wait before requesting another verification email")

    # Generate new token
    new_token = _make_verification_token()

    cur.execute(
        """
        UPDATE users
        SET verification_token = %s,
            verification_sent_at = %s
        WHERE id = %s
    """,
        (new_token, _utcnow_iso(), user_id),
    )
    conn.commit()

    log_auth_event(conn, user_id=user_id, email=email, ip=ip, action="resend", success=True, detail="sent")

    conn.close()

    send_verification_email(email, new_token)

    return {"success": True, "message": "Verification email resent"}
