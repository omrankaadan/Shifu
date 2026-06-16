from fastapi import FastAPI, File, UploadFile, Depends, Request, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import os
import glob
import shutil
import json
from datetime import datetime, timezone

from database import get_db, init_db
from auth import router as auth_router, get_current_user as get_current_user_id
from ai_engine import analyze_pdf
from chat_engine import chat_about_pdf


from dotenv import load_dotenv

load_dotenv()
# -----------------------------
# Uploads directory (GLOBAL)
# -----------------------------
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="AI PDF Analyzer API")

# Ensure DB tables/migrations exist
init_db()
from admin import router as admin_router
app.include_router(admin_router)


# -----------------------------
# Basic rate limiting (best-effort)
# -----------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
import time
from collections import defaultdict, deque

_RATE_BUCKETS = defaultdict(lambda: deque())  # (ip, key) -> deque[timestamps]
_RATE_RULES = [
    ("/login", 10, 60),
    ("/register", 8, 60),
    ("/upload_pdf", 12, 60),
    ("/chat", 30, 60),
]

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        ip = request.client.host if request.client else "unknown"
        path = request.url.path or ""
        now = time.time()
        for prefix, limit, window in _RATE_RULES:
            if path.startswith(prefix):
                key = (ip, prefix)
                q = _RATE_BUCKETS[key]
                while q and (now - q[0]) > window:
                    q.popleft()
                if len(q) >= limit:
                    return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429)
                q.append(now)
                break
        return await call_next(request)

app.add_middleware(RateLimitMiddleware)


# -----------------------------
# CORS (for frontend + cookies)
# -----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # set to your domain(s) in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Serve frontend files
# -----------------------------
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

app.include_router(auth_router)

# -----------------------------
# Feedback (settings menu)
# -----------------------------
@app.post("/feedback")
def submit_feedback(payload: dict, user_id: int = Depends(get_current_user_id)):
    message = str(payload.get("message") or "").strip()
    rating = payload.get("rating")
    page = str(payload.get("page") or "").strip() or None

    if not message:
        return JSONResponse({"success": False, "message": "Message is required."}, status_code=400)

    try:
        r_int = int(rating) if rating is not None and str(rating).strip() != "" else None
        if r_int is not None:
            r_int = max(1, min(5, r_int))
    except Exception:
        r_int = None

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO feedback (user_id, rating, message, page) VALUES (?, ?, ?, ?)",
            (user_id, r_int, message, page),
        )
        conn.commit()
    finally:
        conn.close()

    return {"success": True}

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# -----------------------------
# Helpers
# -----------------------------
def _iso_utc(ts: float | None = None) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()



# -----------------------------
# List PDFs for sidebar
# -----------------------------
@app.get("/my_pdfs")
def my_pdfs(user_id: int = Depends(get_current_user_id)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT filename, upload_time FROM pdfs WHERE user_id = ? ORDER BY upload_time DESC LIMIT 20",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return {"items": [{"filename": r[0], "upload_time": r[1]} for r in rows]}


# -----------------------------
# PDF Upload Route (Premium gating)
# -----------------------------

# -----------------------------
# Fetch latest saved analysis for a PDF filename (sidebar click)
# -----------------------------
@app.get("/pdf_result")
def pdf_result(filename: str, user_id: int = Depends(get_current_user_id)):
    safe_name = os.path.basename(filename)
    # look for analysis files saved on upload: {user_id}_{ts}_{safe_name}.analysis.json
    pattern = os.path.join(UPLOAD_DIR, f"{user_id}_*_{safe_name}.analysis.json")
    matches = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)

    if not matches:
        return JSONResponse({"success": False, "message": "No saved analysis found for this PDF yet."}, status_code=404)

    try:
        with open(matches[0], "r", encoding="utf-8") as f:
            result = json.load(f)
    except Exception:
        return JSONResponse({"success": False, "message": "Saved analysis is corrupted."}, status_code=500)

    return {"success": True, "result": result}




# -----------------------------
# Serve the latest uploaded PDF file for preview (authenticated)
# -----------------------------

@app.get("/pdf_file")
def pdf_file(filename: str, user_id: int = Depends(get_current_user_id)):
    safe_name = os.path.basename(filename)
    pdf_path = _latest_pdf_path(user_id, safe_name)

    if not pdf_path or not os.path.exists(pdf_path):
        return JSONResponse(
            {"success": False, "message": "PDF not found for this user"},
            status_code=404
        )

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{safe_name}"'
        }
    )

# -----------------------------
# Analyze a specific page of an existing uploaded PDF (uses same premium/free-tries gating)
# -----------------------------
@app.post("/analyze_page")
async def analyze_page(payload: dict, user_id: int = Depends(get_current_user_id)):
    """
    Body: { "filename": "some.pdf", "page": 3, "level": "normal" }
    Returns: { success, result }
    """
    filename = str(payload.get("filename") or "").strip()
    level = str(payload.get("level") or "normal").strip().lower()
    page = payload.get("page")

    if not filename:
        return JSONResponse({"success": False, "message": "filename is required"}, status_code=400)
    try:
        page_int = int(page)
    except Exception:
        return JSONResponse({"success": False, "message": "page must be an integer"}, status_code=400)
    if page_int <= 0:
        return JSONResponse({"success": False, "message": "page must be >= 1"}, status_code=400)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if not cur.fetchone():
        conn.close()
        return JSONResponse(
            {"success": False, "message": "User not found"},
            status_code=400,
        )
    pdf_path = _latest_pdf_path(user_id, filename)
    if not pdf_path:
        conn.close()
        return JSONResponse({"success": False, "message": "PDF not found for this user"}, status_code=404)

    try:
        result = analyze_pdf(pdf_path, level=level, page=page_int)
    except Exception as e:
        conn.close()
        return JSONResponse({"success": False, "message": f"Analysis failed: {e}"}, status_code=500)

    # Persist page analysis (best-effort)
    try:
        analysis_path = f"{pdf_path}.p{page_int}.{level}.analysis.json"
        with open(analysis_path, "w", encoding="utf-8") as af:
            json.dump(result, af, ensure_ascii=False, indent=2)
    except Exception:
        pass

    free_left = free_tries
    if not premium:
        free_left = _consume_try(conn, user_id)

    conn.close()
    return {"success": True, "result": result}



# -----------------------------
# Zero-cost chat over a PDF (extractive)
# -----------------------------
def _latest_pdf_path(user_id: int, filename: str) -> str | None:
    safe_name = os.path.basename(filename)
    pattern = os.path.join(UPLOAD_DIR, f"{user_id}_*_{safe_name}")
    matches = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p), reverse=True)
    return matches[0] if matches else None


@app.post("/chat_pdf")
async def chat_pdf(payload: dict, user_id: int = Depends(get_current_user_id)):
    """
    Body: { "filename": "some.pdf", "message": "..." }
    Returns: { success, answer, sources:[{page, snippet}] }
    """
    filename = str(payload.get("filename") or "").strip()
    message = str(payload.get("message") or "").strip()

    if not filename or not message:
        return JSONResponse({"success": False, "message": "filename and message are required"}, status_code=400)

    pdf_path = _latest_pdf_path(user_id, filename)
    if not pdf_path:
        return JSONResponse({"success": False, "message": "PDF not found for this user"}, status_code=404)

    try:
        result = chat_about_pdf(pdf_path, message, top_k=3)
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Chat failed: {e}"}, status_code=500)

    return {"success": True, **result}

@app.post("/upload_pdf")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    level: str | None = Form(None),
    user_id: int = Depends(get_current_user_id)
):
    conn = get_db()
    cursor = conn.cursor()

    # Verify user exists
    cursor.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,),
    )

    if not cursor.fetchone():
        conn.close()
        return JSONResponse(
            {"success": False, "message": "User not found"},
            status_code=400,
        )

    # Save PDF
    safe_name = os.path.basename(file.filename)
    file_path = os.path.join(
        UPLOAD_DIR,
        f"{user_id}_{int(datetime.now().timestamp())}_{safe_name}"
    )

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Store PDF record
    cursor.execute(
        "INSERT INTO pdfs (user_id, filename) VALUES (?, ?)",
        (user_id, safe_name),
    )
    conn.commit()

    # Optional page number
    page = None
    try:
        form = await request.form()
        raw_page = form.get("page")
        if raw_page is not None:
            page = int(raw_page)
    except Exception:
        pass

    # Analyze PDF
    analysis_result = analyze_pdf(
        file_path,
        level=level,
        page=page
    )

    # Save analysis
    try:
        analysis_path = f"{file_path}.analysis.json"
        with open(analysis_path, "w", encoding="utf-8") as af:
            json.dump(
                analysis_result,
                af,
                ensure_ascii=False,
                indent=2
            )
    except Exception:
        pass

    conn.close()

    return {
        "success": True,
        "result": analysis_result,
    }


