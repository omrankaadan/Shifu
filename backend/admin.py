import os
import csv
import io
import psycopg2
import secrets
import glob
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from database import get_db
from auth import get_current_user, send_password_reset_email, _utcnow_iso

router = APIRouter(prefix="/admin", tags=["admin"])
from security import is_admin_email

# Must match UPLOAD_DIR in app.py (backend_dir/uploads)
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")


def _delete_pdf_files_from_disk(owner_user_id: int, filename: str) -> int:
    """
    Deletes the PDF file(s) and any sidecar analysis JSON files matching
    app.py's naming convention: {user_id}_{timestamp}_{filename}
    Returns number of files removed.
    """
    safe_name = os.path.basename(filename or "")
    if not safe_name:
        return 0

    removed = 0
    pattern = os.path.join(UPLOAD_DIR, f"{owner_user_id}_*_{safe_name}*")
    for path in glob.glob(pattern):
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except Exception:
            pass
    return removed


# ============================
# Admin policy
# ============================


def require_admin(user_id: int) -> str:    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")
        
    email = row[0]
    if not is_admin_email(email):
        raise HTTPException(status_code=403, detail="Admin access required")
    return email


# ============================
# Overview / analytics
# ============================
def _days_back(n: int) -> List[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


@router.get("/overview")
def admin_overview(user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    # Cards
    cur.execute("SELECT COUNT(*) FROM users")
    users_total = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM users WHERE email_verified = 1")
    verified_total = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM pdfs")
    pdfs_total = cur.fetchone()[0] or 0


    # 14d windows
    start_14 = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    cur.execute("SELECT COUNT(*) FROM users WHERE datetime(created_at) >= datetime(%s)", (start_14,))
    users_14d = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM pdfs WHERE datetime(upload_time) >= datetime(%s)", (start_14,))
    pdfs_14d = cur.fetchone()[0] or 0
    # Timeseries (14 days): signups, uploads, payments
    days = _days_back(14)
    timeseries = []
    for day in days:
        cur.execute("SELECT COUNT(*) FROM users WHERE date(created_at) = date(%s)", (day,))
        signups = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM pdfs WHERE date(upload_time) = date(%s)", (day,))
        uploads = cur.fetchone()[0] or 0

    timeseries.append({
        "day": day,
        "signups": signups,
        "uploads": uploads
    })
    # Recent activity (last 20 auth events + last 10 uploads + last 10 payments)
    recent: List[Dict[str, Any]] = []

    cur.execute(
        """
        SELECT created_at, action, email, ip, detail, success
        FROM auth_events
        ORDER BY datetime(created_at) DESC
        LIMIT 20
        """
    )
    for t, action, email, ip, detail, success in cur.fetchall() or []:
        recent.append(
            {
                "time": t,
                "type": f"auth:{action}",
                "actor": email or "—",
                "ip": ip or "—",
                "detail": (detail or ("success" if success else "failed")),
            }
        )

    cur.execute(
        """
        SELECT p.upload_time, u.email, p.filename
        FROM pdfs p
        LEFT JOIN users u ON u.id = p.user_id
        ORDER BY datetime(p.upload_time) DESC
        LIMIT 10
        """
    )
    for t, email, filename in cur.fetchall() or []:
        recent.append({"time": t, "type": "upload:pdf", "actor": email or "—", "ip": "—", "detail": filename or "PDF"})

    # sort by time desc best-effort
    def _key(x):
        try:
            return datetime.fromisoformat(str(x["time"]).replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    recent.sort(key=_key, reverse=True)
    recent = recent[:25]

    conn.close()

    return {
        "success": True,
        "admin": admin_email,
        "cards": {
            "users_total": users_total,
            "verified_total": verified_total,
            "pdfs_total": pdfs_total,
            "users_14d": users_14d,
            "pdfs_14d": pdfs_14d,
        },
        "timeseries": timeseries,
        "recent": recent,
    }


# ============================
# Tables
# ============================
@router.get("/users")
def admin_users(limit: int = 200, user_id: int = Depends(get_current_user)):
    require_admin(user_id)
    limit = max(1, min(int(limit), 2000))

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, email, auth_provider, email_verified, created_at, status
        FROM users
        ORDER BY id DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = cur.fetchall() or []
    conn.close()

    return {
        "success": True,
        "items": [
            {
                "id": r[0],
                "email": r[1],
                "auth_provider": r[2],
                "email_verified": int(r[3] or 0),
                "created_at": r[4],
                "status": r[5] or "active",
            }
            for r in rows
        ],
    }


# ============================
# User action helpers
# ============================
def _get_user_row(cur, target_id: int):
    cur.execute("SELECT id, email, status FROM users WHERE id = %s", (target_id,))
    return cur.fetchone()


def _assert_not_self_and_not_admin_target(target_id: int, user_id: int, target_email: str, block_admin: bool = False):
    if target_id == user_id:
        raise HTTPException(status_code=400, detail="You cannot perform this action on your own account")
    if block_admin and is_admin_email(target_email):
        raise HTTPException(status_code=400, detail="This action cannot target an admin account")


# ============================
# Block / Unblock user
# ============================
ALLOWED_STATUSES = {"active", "blocked"}


@router.post("/users/{target_id}/status")
def set_user_status(target_id: int, payload: Dict[str, Any], user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    new_status = str(payload.get("status", "")).strip().lower()
    if new_status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    conn = get_db()
    cur = conn.cursor()

    row = _get_user_row(cur, target_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    target_email = row[1]
    _assert_not_self_and_not_admin_target(target_id, user_id, target_email, block_admin=(new_status == "blocked"))

    cur.execute("UPDATE users SET status = %s WHERE id = %s", (new_status, target_id))
    cur.execute(
        "INSERT INTO auth_events (user_id, email, ip, action, success, detail) VALUES (%s, %s, %s, %s, %s, %s)",
        (target_id, target_email, None, "admin_set_status", 1, f"{new_status} (by {admin_email})"),
    )
    conn.commit()
    conn.close()

    return {"success": True, "id": target_id, "status": new_status}


# ============================
# Force password reset
# ============================
@router.post("/users/{target_id}/force-password-reset")
def force_password_reset(target_id: int, user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    row = _get_user_row(cur, target_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    target_email = row[1]
    _assert_not_self_and_not_admin_target(target_id, user_id, target_email)

    # Clear the existing password hash so the account can't log in with the old
    # password, and issue a reset token (reusing the verification_token column
    # as a generic one-time token slot).
    reset_token = secrets.token_urlsafe(32)
    cur.execute(
        """
        UPDATE users
        SET password_hash = NULL,
            verification_token = %s,
            verification_sent_at = %s
        WHERE id = %s
        """,
        (reset_token, _utcnow_iso(), target_id),
    )
    cur.execute(
        "INSERT INTO auth_events (user_id, email, ip, action, success, detail) VALUES (%s, %s, %s, %s, %s, %s)",
        (target_id, target_email, None, "admin_force_password_reset", 1, f"reset by {admin_email}"),
    )
    conn.commit()
    conn.close()

    try:
        send_password_reset_email(target_email, reset_token)
    except Exception:
        pass

    return {"success": True, "id": target_id, "email": target_email}


# ============================
# Force logout (revoke sessions)
# ============================
@router.post("/users/{target_id}/force-logout")
def force_logout(target_id: int, user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    row = _get_user_row(cur, target_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    target_email = row[1]
    _assert_not_self_and_not_admin_target(target_id, user_id, target_email)

    cur.execute(
        "UPDATE users SET sessions_invalidated_at = %s WHERE id = %s",
        (_utcnow_iso(), target_id),
    )
    cur.execute(
        "INSERT INTO auth_events (user_id, email, ip, action, success, detail) VALUES (%s, %s, %s, %s, %s, %s)",
        (target_id, target_email, None, "admin_force_logout", 1, f"by {admin_email}"),
    )
    conn.commit()
    conn.close()

    return {"success": True, "id": target_id}


# ============================
# Manually verify email
# ============================
@router.post("/users/{target_id}/verify-email")
def admin_verify_email(target_id: int, user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    row = _get_user_row(cur, target_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    target_email = row[1]

    cur.execute("UPDATE users SET email_verified = 1 WHERE id = %s", (target_id,))
    cur.execute(
        "INSERT INTO auth_events (user_id, email, ip, action, success, detail) VALUES (%s, %s, %s, %s, %s, %s)",
        (target_id, target_email, None, "admin_verify_email", 1, f"by {admin_email}"),
    )
    conn.commit()
    conn.close()

    return {"success": True, "id": target_id}


# ============================
# User PDF history (for modal)
# ============================
@router.get("/users/{target_id}/pdfs")
def admin_user_pdfs(target_id: int, user_id: int = Depends(get_current_user)):
    require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    row = _get_user_row(cur, target_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    cur.execute(
        "SELECT id, filename, upload_time FROM pdfs WHERE user_id = %s ORDER BY id DESC",
        (target_id,),
    )
    items = [{"id": r[0], "filename": r[1], "upload_time": r[2]} for r in (cur.fetchall() or [])]
    conn.close()

    return {"success": True, "user_id": target_id, "email": row[1], "items": items}


# ============================
# Delete a single PDF
# ============================
@router.delete("/pdfs/{pdf_id}")
def delete_pdf(pdf_id: int, user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT p.id, p.filename, p.user_id, u.email FROM pdfs p LEFT JOIN users u ON u.id = p.user_id WHERE p.id = %s",
        (pdf_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="PDF not found")

    _, filename, owner_id, owner_email = row

    files_removed = _delete_pdf_files_from_disk(owner_id, filename)

    cur.execute("DELETE FROM pdfs WHERE id = %s", (pdf_id,))
    cur.execute(
        "INSERT INTO auth_events (user_id, email, ip, action, success, detail) VALUES (%s, %s, %s, %s, %s, %s)",
        (owner_id, owner_email, None, "admin_delete_pdf", 1, f"{filename} (by {admin_email}, {files_removed} file(s) removed)"),
    )
    conn.commit()
    conn.close()

    return {"success": True, "id": pdf_id, "files_removed": files_removed}


# ============================
# Bulk delete all PDFs for a user
# ============================
@router.delete("/users/{target_id}/pdfs")
def delete_all_pdfs_for_user(target_id: int, user_id: int = Depends(get_current_user)):
    admin_email = require_admin(user_id)

    conn = get_db()
    cur = conn.cursor()

    row = _get_user_row(cur, target_id)
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="User not found")

    target_email = row[1]

    cur.execute("SELECT id, filename FROM pdfs WHERE user_id = %s", (target_id,))
    pdf_rows = cur.fetchall() or []
    count = len(pdf_rows)

    files_removed = 0
    for _pdf_id, fname in pdf_rows:
        files_removed += _delete_pdf_files_from_disk(target_id, fname)

    cur.execute("DELETE FROM pdfs WHERE user_id = %s", (target_id,))
    cur.execute(
        "INSERT INTO auth_events (user_id, email, ip, action, success, detail) VALUES (%s, %s, %s, %s, %s, %s)",
        (target_id, target_email, None, "admin_bulk_delete_pdfs", 1, f"{count} record(s), {files_removed} file(s) (by {admin_email})"),
    )
    conn.commit()
    conn.close()

    return {"success": True, "id": target_id, "deleted": count, "files_removed": files_removed}


@router.get("/pdfs")
def admin_pdfs(limit: int = 200, user_id: int = Depends(get_current_user)):
    require_admin(user_id)
    limit = max(1, min(int(limit), 2000))

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.user_id, u.email, p.filename, p.upload_time
        FROM pdfs p
        LEFT JOIN users u ON u.id = p.user_id
        ORDER BY p.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = cur.fetchall() or []
    conn.close()

    return {
        "success": True,
        "items": [
            {"id": r[0], "user_id": r[1], "user_email": r[2], "filename": r[3], "upload_time": r[4]} for r in rows
        ],
    }

# ============================
# IP abuse detection
# ============================
@router.get("/ip-abuse")
def admin_ip_abuse(limit: int = 100, user_id: int = Depends(get_current_user)):
    require_admin(user_id)
    limit = max(1, min(int(limit), 5000))

    conn = get_db()
    cur = conn.cursor()

    now = datetime.now(timezone.utc)
    t1h = (now - timedelta(hours=1)).isoformat()
    t24h = (now - timedelta(hours=24)).isoformat()

    # Aggregate failures by IP
    cur.execute(
        """
        SELECT ip,
               SUM(CASE WHEN success = 0 AND action = 'login' AND datetime(created_at) >= datetime(%s) THEN 1 ELSE 0 END) AS failed_1h,
               SUM(CASE WHEN success = 0 AND action = 'login' AND datetime(created_at) >= datetime(%s) THEN 1 ELSE 0 END) AS failed_24h,
               SUM(CASE WHEN action = 'register' AND success = 1 AND datetime(created_at) >= datetime(%s) THEN 1 ELSE 0 END) AS signups_24h,
               COUNT(DISTINCT CASE WHEN action = 'register' AND datetime(created_at) >= datetime(%s) THEN email END) AS unique_emails_24h
        FROM auth_events
        WHERE ip IS NOT NULL AND ip != ''
          AND datetime(created_at) >= datetime(%s)
        GROUP BY ip
        ORDER BY (failed_24h + unique_emails_24h + signups_24h) DESC
        LIMIT %s
        """,
        (t1h, t24h, t24h, t24h, t24h, limit),
    )

    items = []
    for ip, failed_1h, failed_24h, signups_24h, unique_emails_24h in cur.fetchall() or []:
        failed_1h = int(failed_1h or 0)
        failed_24h = int(failed_24h or 0)
        signups_24h = int(signups_24h or 0)
        unique_emails_24h = int(unique_emails_24h or 0)

        # Simple risk scoring
        score = 0
        score += min(12, failed_1h) * 3
        score += min(40, failed_24h)
        score += min(20, signups_24h) * 2
        score += min(30, unique_emails_24h) * 3

        risk = "low"
        if score >= 55:
            risk = "high"
        elif score >= 25:
            risk = "medium"

        # Only show suspicious-ish entries by default:
        if failed_24h >= 5 or unique_emails_24h >= 6 or signups_24h >= 8 or failed_1h >= 3:
            items.append(
                {
                    "ip": ip,
                    "failed_1h": failed_1h,
                    "failed_24h": failed_24h,
                    "signups_24h": signups_24h,
                    "unique_emails_24h": unique_emails_24h,
                    "risk": risk,
                    "score": score,
                }
            )

    conn.close()
    return {"success": True, "items": items[: min(len(items), limit)]}


# ============================
# CSV export
# ============================
def _csv_response(filename: str, rows: List[List[Any]]):
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        for r in rows:
            buf.seek(0)
            buf.truncate(0)
            w.writerow(r)
            yield buf.getvalue()

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/{kind}.csv")
def export_csv(kind: str, user_id: int = Depends(get_current_user)):
    require_admin(user_id)
    kind = (kind or "").lower().replace(".csv", "")

    conn = get_db()
    cur = conn.cursor()

    if kind == "users":
        cur.execute("SELECT id, email, auth_provider, email_verified, created_at, status FROM users ORDER BY id DESC")
        rows = [["id", "email", "auth_provider", "email_verified", "created_at", "status"]]
        rows += [list(r) for r in (cur.fetchall() or [])]
        conn.close()
        return _csv_response("users.csv", rows)

    if kind == "pdfs":
        cur.execute(
            """
            SELECT p.id, u.email, p.filename, p.upload_time
            FROM pdfs p
            LEFT JOIN users u ON u.id = p.user_id
            ORDER BY p.id DESC
            """
        )
        rows = [["id", "user_email", "filename", "upload_time"]]
        rows += [list(r) for r in (cur.fetchall() or [])]
        conn.close()
        return _csv_response("pdfs.csv", rows)


    if kind in ("auth_events", "events"):
        cur.execute(
            """
            SELECT id, user_id, email, ip, action, success, detail, created_at
            FROM auth_events
            ORDER BY id DESC
            """
        )
        rows = [["id", "user_id", "email", "ip", "action", "success", "detail", "created_at"]]
        rows += [list(r) for r in (cur.fetchall() or [])]
        conn.close()
        return _csv_response("auth_events.csv", rows)

    if kind in ("ip_abuse", "abuse"):
        # build from the same logic as endpoint
        now = datetime.now(timezone.utc)
        t1h = (now - timedelta(hours=1)).isoformat()
        t24h = (now - timedelta(hours=24)).isoformat()

        cur.execute(
            """
            SELECT ip,
                   SUM(CASE WHEN success = 0 AND action = 'login' AND datetime(created_at) >= datetime(%s) THEN 1 ELSE 0 END) AS failed_1h,
                   SUM(CASE WHEN success = 0 AND action = 'login' AND datetime(created_at) >= datetime(%s) THEN 1 ELSE 0 END) AS failed_24h,
                   SUM(CASE WHEN action = 'register' AND success = 1 AND datetime(created_at) >= datetime(%s) THEN 1 ELSE 0 END) AS signups_24h,
                   COUNT(DISTINCT CASE WHEN action = 'register' AND datetime(created_at) >= datetime(%s) THEN email END) AS unique_emails_24h
            FROM auth_events
            WHERE ip IS NOT NULL AND ip != ''
              AND datetime(created_at) >= datetime(%s)
            GROUP BY ip
            ORDER BY (failed_24h + unique_emails_24h + signups_24h) DESC
            """,
            (t1h, t24h, t24h, t24h, t24h),
        )

        rows = [["ip", "failed_1h", "failed_24h", "signups_24h", "unique_emails_24h", "risk", "score"]]
        for ip, failed_1h, failed_24h, signups_24h, unique_emails_24h in cur.fetchall() or []:
            failed_1h = int(failed_1h or 0)
            failed_24h = int(failed_24h or 0)
            signups_24h = int(signups_24h or 0)
            unique_emails_24h = int(unique_emails_24h or 0)
            score = min(12, failed_1h) * 3 + min(40, failed_24h) + min(20, signups_24h) * 2 + min(30, unique_emails_24h) * 3
            risk = "low"
            if score >= 55:
                risk = "high"
            elif score >= 25:
                risk = "medium"
            rows.append([ip, failed_1h, failed_24h, signups_24h, unique_emails_24h, risk, score])

        conn.close()
        return _csv_response("ip_abuse.csv", rows)

    # Overview: daily totals (14d)
    if kind == "overview":
        days = _days_back(14)
        rows = [["day", "signups", "uploads"]]
        for day in days:
            cur.execute("SELECT COUNT(*) FROM users WHERE date(created_at) = date(%s)", (day,))
            signups = cur.fetchone()[0] or 0
            cur.execute("SELECT COUNT(*) FROM pdfs WHERE date(upload_time) = date(%s)", (day,))
            uploads = cur.fetchone()[0] or 0
            rows.append([day, signups, uploads])
        conn.close()
        return _csv_response("overview.csv", rows)

    conn.close()
    raise HTTPException(status_code=404, detail="Unknown export kind")