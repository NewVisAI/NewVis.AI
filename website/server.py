"""Public website for NewVis AI (a Gensolve product) — https://gensolve.in

Deliberately tiny and independent of the CCTV engine: no torch, no OpenCV, no
camera code. It serves the static marketing pages and accepts "Request a
callback" leads into a local SQLite file.

Run locally:
    pip install -r website/requirements.txt
    uvicorn website.server:app --port 8080

Environment (all optional):
    SITE_DB_PATH        SQLite file for leads          (default: website/data/leads.db)
    LEADS_ADMIN_TOKEN   enables GET /api/leads with "Authorization: Bearer <token>"
    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / SMTP_FROM / LEADS_NOTIFY_TO
                        if set, each new lead is also emailed to LEADS_NOTIFY_TO
    TRUST_PROXY=1       read client IP from X-Forwarded-For (set when behind Caddy/nginx)
    LEADS_PER_HOUR      per-IP submission cap (default 5)
"""

from __future__ import annotations

import csv
import io
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
DB_PATH = os.getenv("SITE_DB_PATH", os.path.join(HERE, "data", "leads.db"))
ADMIN_TOKEN = os.getenv("LEADS_ADMIN_TOKEN", "")
TRUST_PROXY = os.getenv("TRUST_PROXY", "0") == "1"
LEADS_PER_HOUR = int(os.getenv("LEADS_PER_HOUR", "5"))

app = FastAPI(title="NewVis AI — gensolve.in", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Security headers on every response
# ---------------------------------------------------------------------------
# The pages use inline <style>/<script>, hence 'unsafe-inline'. No third-party
# origins are loaded, so everything else is locked to 'self'.
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/static/"):
        h.setdefault("Cache-Control", "public, max-age=86400")
    elif request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    else:
        h.setdefault("Cache-Control", "public, max-age=300")
    return response


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def _page(name: str, status_code: int = 200) -> FileResponse:
    return FileResponse(os.path.join(HERE, name), media_type="text/html", status_code=status_code)


@app.get("/", include_in_schema=False)
def home():
    return _page("index.html")


@app.get("/privacy", include_in_schema=False)
def privacy():
    return _page("privacy.html")


@app.get("/robots.txt", include_in_schema=False)
def robots():
    return FileResponse(os.path.join(STATIC_DIR, "robots.txt"), media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    return FileResponse(os.path.join(STATIC_DIR, "sitemap.xml"), media_type="application/xml")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return _page("404.html", status_code=404)


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
class LeadRequest(BaseModel):
    name: str = Field("", max_length=200)
    email: str = Field("", max_length=200)
    phone: str = Field("", max_length=60)
    org: Optional[str] = Field("", max_length=200)
    use_case: Optional[str] = Field("", max_length=200)
    message: Optional[str] = Field("", max_length=2000)
    website: Optional[str] = Field("", max_length=200)  # honeypot


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^[0-9+()\-.\s]{7,30}$")

_db_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            org TEXT,
            use_case TEXT,
            message TEXT,
            source_ip TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def _client_ip(request: Request) -> str:
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= LEADS_PER_HOUR:
            return True
        q.append(now)
        return False


def _notify(lead: dict) -> None:
    host, to = os.getenv("SMTP_HOST"), os.getenv("LEADS_NOTIFY_TO")
    if not host or not to:
        return
    msg = EmailMessage()
    msg["Subject"] = f"New NewVis callback request — {lead['name']} ({lead['use_case'] or 'n/a'})"
    msg["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or to
    msg["To"] = to
    msg["Reply-To"] = lead["email"]
    msg.set_content("\n".join(f"{k}: {v}" for k, v in lead.items()))
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with cls(host, port, timeout=15) as s:
            if port != 465:
                s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS", ""))
            s.send_message(msg)
    except Exception as e:  # the lead is already stored; never fail the request
        print(f"[leads] email notification failed: {e}", flush=True)


@app.post("/api/leads")
def create_lead(lead: LeadRequest, request: Request):
    if (lead.website or "").strip():
        return {"ok": True}  # bot: pretend success, store nothing

    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(429, "Too many requests — please try again later, or email us directly.")

    row = {
        "name": lead.name.strip()[:120],
        "email": lead.email.strip()[:160],
        "phone": lead.phone.strip()[:30],
        "org": (lead.org or "").strip()[:160],
        "use_case": (lead.use_case or "").strip()[:120],
        "message": (lead.message or "").strip()[:1000],
    }
    if not row["name"] or not row["email"] or not row["phone"] or not row["use_case"]:
        raise HTTPException(422, "Please fill in name, phone, email and your use case.")
    if not EMAIL_RE.match(row["email"]):
        raise HTTPException(422, "Please provide a valid email address.")
    if not PHONE_RE.match(row["phone"]):
        raise HTTPException(422, "Please provide a valid phone number.")

    row["source_ip"] = ip
    row["user_agent"] = request.headers.get("user-agent", "")[:300]
    row["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _db_lock:
        conn = _connect()
        try:
            conn.execute(
                f"INSERT INTO leads ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                tuple(row.values()),
            )
            conn.commit()
        finally:
            conn.close()
    threading.Thread(target=_notify, args=(row,), daemon=True).start()
    return {"ok": True}


def _csv_safe(v):
    """Neutralise spreadsheet formula injection from public form input."""
    if isinstance(v, str) and v[:1] in ("=", "@", "\t", "\r", "+", "-") and not PHONE_RE.match(v):
        return "'" + v
    return v


@app.get("/api/leads")
def list_leads(request: Request, format: str = "json"):
    """Team call-list. Disabled unless LEADS_ADMIN_TOKEN is set."""
    if not ADMIN_TOKEN:
        raise HTTPException(404, "Not found")
    auth = request.headers.get("authorization", "")
    if not secrets.compare_digest(auth, f"Bearer {ADMIN_TOKEN}"):
        raise HTTPException(401, "Unauthorized")
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute("SELECT * FROM leads ORDER BY id DESC")]
    finally:
        conn.close()
    if format == "csv":
        buf = io.StringIO()
        cols = rows[0].keys() if rows else ["id", "name", "email", "phone", "org", "use_case", "message", "created_at"]
        w = csv.DictWriter(buf, fieldnames=list(cols))
        w.writeheader()
        w.writerows({k: _csv_safe(v) for k, v in r.items()} for r in rows)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=leads.csv"})
    return {"count": len(rows), "leads": rows}
