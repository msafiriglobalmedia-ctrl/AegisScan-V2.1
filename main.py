from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from aegis_engine import AegisVisionEngine

STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ANNOTATED_DIR = DATA_DIR / "annotated"
for directory in (DATA_DIR, UPLOAD_DIR, ANNOTATED_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DB_PATH_RAW = os.getenv("AEGIS_DATABASE_PATH", "data/aegisscan.db")
DB_PATH = Path(DB_PATH_RAW)
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

APP_VERSION = "2.0.0"
AUTH_ENABLED = os.getenv("AEGIS_AUTH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
ADMIN_USERNAME = os.getenv("AEGIS_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("AEGIS_ADMIN_PASSWORD", "ChangeThis-Immediately-2026!")
SECRET_KEY = os.getenv("AEGIS_SECRET_KEY", "DEV-ONLY-change-this-secret-key-before-deploying")
COOKIE_SECURE = os.getenv("AEGIS_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
MAX_UPLOAD_BYTES = max(1, int(os.getenv("AEGIS_UPLOAD_MAX_MB", "8"))) * 1024 * 1024
ALLOWED_STATUSES = {"New", "Under Review", "Verified", "False Positive", "Resolved"}
engine = AegisVisionEngine()

app = FastAPI(title="AegisScan AI", version=APP_VERSION, description="AI-assisted safety observation and incident management.")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="aegisscan_session",
    same_site="lax",
    https_only=COOKIE_SECURE,
    max_age=60 * 60 * 12,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_file TEXT NOT NULL,
                annotated_file TEXT,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                detections_json TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_version TEXT NOT NULL,
                inference_ms REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'New',
                description TEXT NOT NULL,
                acknowledged_at TEXT,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                incident_id TEXT REFERENCES incidents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'Internal',
                status TEXT NOT NULL DEFAULT 'Pending',
                message TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_auth(request: Request) -> dict[str, str] | None:
    if not AUTH_ENABLED:
        return {"username": "local"}
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class IncidentUpdate(BaseModel):
    status: str


class AlertUpdate(BaseModel):
    status: str = "Acknowledged"


@app.get("/", response_class=HTMLResponse)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        with get_db() as db:
            db.execute("SELECT 1").fetchone()
        db_ok = True
    except sqlite3.Error:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "app": "AegisScan AI", "version": APP_VERSION, "database": "connected" if db_ok else "unavailable"}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    if not AUTH_ENABLED:
        return {"authenticated": True, "username": "local", "auth_enabled": False}
    user = request.session.get("user")
    return {"authenticated": bool(user), "username": user.get("username") if user else None, "auth_enabled": True}


@app.post("/api/auth/login")
def login(payload: LoginPayload, request: Request) -> dict[str, Any]:
    username_ok = hmac.compare_digest(payload.username.encode(), ADMIN_USERNAME.encode())
    password_ok = hmac.compare_digest(payload.password.encode(), ADMIN_PASSWORD.encode())
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Incorrect username or password.")
    request.session.clear()
    request.session["user"] = {"username": ADMIN_USERNAME}
    request.session["csrf"] = secrets.token_urlsafe(24)
    return {"ok": True, "username": ADMIN_USERNAME}


@app.post("/api/auth/logout")
def logout(request: Request, _: dict[str, str] | None = Depends(require_auth)) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


def severity_for(label: str) -> str:
    label = label.lower()
    if any(word in label for word in ("fire", "smoke", "blocked_exit")):
        return "Critical"
    if any(word in label for word in ("spill", "unsafe_ladder")):
        return "High"
    return "Medium"


def safe_image_from_upload(raw: bytes, filename: str) -> tuple[Image.Image, str, int, int]:
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=415, detail="The uploaded file is not a supported image.") from exc
    width, height = image.size
    if width < 32 or height < 32:
        raise HTTPException(status_code=400, detail="Image is too small. Please upload an image at least 32×32 pixels.")
    if width * height > 30_000_000:
        raise HTTPException(status_code=413, detail="Image dimensions are too large. Maximum is 30 megapixels.")
    image_id = str(uuid.uuid4())
    stored_name = f"{image_id}.jpg"
    image.save(UPLOAD_DIR / stored_name, format="JPEG", quality=90, optimize=True)
    return image, stored_name, width, height


def draw_annotations(image: Image.Image, detections: list[dict[str, Any]], destination: Path) -> None:
    output = image.copy()
    draw = ImageDraw.Draw(output)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(14, output.width // 55))
    except OSError:
        font = ImageFont.load_default()
    line_width = max(3, output.width // 300)
    for item in detections:
        x1, y1, x2, y2 = [int(v) for v in item["bbox"]]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(output.width, x2), min(output.height, y2)
        draw.rectangle((x1, y1, x2, y2), outline=(0, 210, 255), width=line_width)
        label = f"{item['label']}  {item['confidence'] * 100:.0f}%"
        text_box = draw.textbbox((x1, max(0, y1 - 24)), label, font=font)
        draw.rectangle(text_box, fill=(5, 20, 38))
        draw.text((x1, max(0, y1 - 24)), label, fill=(255, 255, 255), font=font)
    output.save(destination, format="JPEG", quality=88, optimize=True)


def scan_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["detections"] = json.loads(data.pop("detections_json"))
    data["image_url"] = f"/api/scans/{data['id']}/image"
    data["annotated_url"] = f"/api/scans/{data['id']}/image?view=annotated" if data.get("annotated_file") else None
    return data


@app.post("/api/scans")
async def create_scan(file: UploadFile = File(...), _: dict[str, str] | None = Depends(require_auth)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Please select an image.")
    ext = Path(file.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=415, detail="Only JPG, JPEG, PNG and WEBP images are accepted.")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.")
    image, stored_file, width, height = safe_image_from_upload(raw, file.filename)
    scan_id = str(uuid.uuid4())
    inference = engine.analyze(UPLOAD_DIR / stored_file)
    detections = inference["detections"]
    annotated_name = None
    if inference["ready"] and detections:
        annotated_name = f"{scan_id}.jpg"
        draw_annotations(image, detections, ANNOTATED_DIR / annotated_name)

    if not inference["ready"]:
        scan_status = "model_unavailable"
        risk = "Unknown"
        summary = "Image stored successfully. AI inference was not run because a trained model is not configured."
    elif detections:
        scan_status = "completed"
        highest = max((severity_for(item["normalized_label"]) for item in detections), key={"Medium": 1, "High": 2, "Critical": 3}.get)
        risk = highest
        summary = f"{len(detections)} configured hazard detection(s) found. Review the evidence before taking action."
    else:
        scan_status = "completed"
        risk = "No configured hazard detected"
        summary = "No configured hazard class was detected above the confidence threshold. This does not prove the scene is safe."

    created_at = utc_now()
    with get_db() as db:
        db.execute(
            """INSERT INTO scans (id, created_at, original_name, stored_file, annotated_file, width, height, status, summary, risk_level, detections_json, model_name, model_version, inference_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scan_id, created_at, Path(file.filename).name[:200], stored_file, annotated_name, width, height, scan_status, summary, risk,
             json.dumps(detections), inference["model_name"], inference["model_version"], inference["inference_ms"]),
        )
        for detection in detections:
            incident_id = str(uuid.uuid4())
            severity = severity_for(detection["normalized_label"])
            description = f"AI detected {detection['label']} with {detection['confidence'] * 100:.1f}% confidence. Human verification is required."
            db.execute(
                "INSERT INTO incidents (id, scan_id, created_at, category, severity, confidence, status, description) VALUES (?, ?, ?, ?, ?, ?, 'New', ?)",
                (incident_id, scan_id, created_at, detection["label"], severity, detection["confidence"], description),
            )
            db.execute(
                "INSERT INTO alerts (id, incident_id, created_at, channel, status, message) VALUES (?, ?, ?, 'Internal', 'Pending', ?)",
                (str(uuid.uuid4()), incident_id, created_at, f"Review required: {detection['label']} ({severity}) detected in scan {scan_id[:8]}"),
            )
        row = db.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
    return {"scan": scan_to_dict(row), "model": {"ready": inference["ready"], "message": inference["message"]}}


@app.get("/api/scans")
def list_scans(limit: int = 20, _: dict[str, str] | None = Depends(require_auth)) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    with get_db() as db:
        rows = db.execute("SELECT * FROM scans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [scan_to_dict(row) for row in rows]


@app.get("/api/scans/{scan_id}/image")
def scan_image(scan_id: str, view: str = "original", _: dict[str, str] | None = Depends(require_auth)) -> FileResponse:
    with get_db() as db:
        row = db.execute("SELECT stored_file, annotated_file FROM scans WHERE id = ?", (scan_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Scan not found.")
    filename = row["annotated_file"] if view == "annotated" else row["stored_file"]
    if not filename:
        raise HTTPException(status_code=404, detail="Annotated image is not available.")
    base = ANNOTATED_DIR if view == "annotated" else UPLOAD_DIR
    path = (base / Path(filename).name).resolve()
    if path.parent != base.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@app.get("/api/incidents")
def list_incidents(status: str | None = None, limit: int = 100, _: dict[str, str] | None = Depends(require_auth)) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 300))
    with get_db() as db:
        if status and status in ALLOWED_STATUSES:
            rows = db.execute(
                """SELECT i.*, s.original_name, s.stored_file FROM incidents i JOIN scans s ON s.id=i.scan_id
                   WHERE i.status=? ORDER BY i.created_at DESC LIMIT ?""", (status, limit)
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT i.*, s.original_name, s.stored_file FROM incidents i JOIN scans s ON s.id=i.scan_id
                   ORDER BY i.created_at DESC LIMIT ?""", (limit,)
            ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["image_url"] = f"/api/scans/{item['scan_id']}/image"
        result.append(item)
    return result


@app.patch("/api/incidents/{incident_id}")
def update_incident(incident_id: str, payload: IncidentUpdate, _: dict[str, str] | None = Depends(require_auth)) -> dict[str, Any]:
    if payload.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail=f"Status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}")
    now = utc_now()
    with get_db() as db:
        row = db.execute("SELECT id FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Incident not found.")
        acknowledged = now if payload.status in {"Under Review", "Verified", "False Positive", "Resolved"} else None
        resolved = now if payload.status == "Resolved" else None
        db.execute("UPDATE incidents SET status=?, acknowledged_at=COALESCE(?, acknowledged_at), resolved_at=? WHERE id=?",
                   (payload.status, acknowledged, resolved, incident_id))
        updated = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    return dict(updated)


@app.get("/api/alerts")
def list_alerts(limit: int = 100, _: dict[str, str] | None = Depends(require_auth)) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 300))
    with get_db() as db:
        rows = db.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]


@app.patch("/api/alerts/{alert_id}")
def update_alert(alert_id: str, payload: AlertUpdate, _: dict[str, str] | None = Depends(require_auth)) -> dict[str, Any]:
    if payload.status not in {"Acknowledged", "Pending"}:
        raise HTTPException(status_code=422, detail="Alert status must be Acknowledged or Pending.")
    with get_db() as db:
        db.execute("UPDATE alerts SET status=? WHERE id=?", (payload.status, alert_id))
        row = db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return dict(row)


@app.get("/api/overview")
def overview(_: dict[str, str] | None = Depends(require_auth)) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    with get_db() as db:
        scans = db.execute("SELECT COUNT(*) n FROM scans").fetchone()["n"]
        scans_week = db.execute("SELECT COUNT(*) n FROM scans WHERE created_at >= ?", (since,)).fetchone()["n"]
        incidents = db.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"]
        open_incidents = db.execute("SELECT COUNT(*) n FROM incidents WHERE status NOT IN ('Resolved', 'False Positive')").fetchone()["n"]
        critical = db.execute("SELECT COUNT(*) n FROM incidents WHERE severity='Critical' AND status NOT IN ('Resolved', 'False Positive')").fetchone()["n"]
        pending_alerts = db.execute("SELECT COUNT(*) n FROM alerts WHERE status='Pending'").fetchone()["n"]
        latest = db.execute("""SELECT i.*, s.original_name FROM incidents i JOIN scans s ON s.id=i.scan_id ORDER BY i.created_at DESC LIMIT 5""").fetchall()
    return {"total_scans": scans, "scans_last_7_days": scans_week, "total_incidents": incidents,
            "open_incidents": open_incidents, "critical_open": critical, "pending_alerts": pending_alerts,
            "latest_incidents": [dict(row) for row in latest], "model": engine.status()}


@app.get("/api/analytics")
def analytics(_: dict[str, str] | None = Depends(require_auth)) -> dict[str, Any]:
    with get_db() as db:
        categories = db.execute("SELECT category, COUNT(*) count FROM incidents GROUP BY category ORDER BY count DESC LIMIT 8").fetchall()
        severity = db.execute("SELECT severity, COUNT(*) count FROM incidents GROUP BY severity").fetchall()
        statuses = db.execute("SELECT status, COUNT(*) count FROM incidents GROUP BY status").fetchall()
        daily = db.execute("""SELECT substr(created_at,1,10) day, COUNT(*) count FROM scans
                            WHERE created_at >= ? GROUP BY substr(created_at,1,10) ORDER BY day""",
                           ((datetime.now(timezone.utc)-timedelta(days=13)).isoformat(timespec="seconds"),)).fetchall()
        total = db.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"]
        false_positives = db.execute("SELECT COUNT(*) n FROM incidents WHERE status='False Positive'").fetchone()["n"]
        verified = db.execute("SELECT COUNT(*) n FROM incidents WHERE status IN ('Verified','Resolved')").fetchone()["n"]
    return {"categories": [dict(r) for r in categories], "severity": [dict(r) for r in severity],
            "statuses": [dict(r) for r in statuses], "daily_scans": [dict(r) for r in daily],
            "total_incidents": total, "false_positives": false_positives, "verified_or_resolved": verified}


@app.get("/api/model/status")
def model_status(_: dict[str, str] | None = Depends(require_auth)) -> dict[str, Any]:
    return engine.status()


@app.get("/api/export/incidents.csv")
def export_incidents(_: dict[str, str] | None = Depends(require_auth)) -> StreamingResponse:
    with get_db() as db:
        rows = db.execute("""SELECT i.id, i.created_at, i.category, i.severity, i.confidence, i.status,
                            i.description, s.original_name, i.scan_id FROM incidents i JOIN scans s ON s.id=i.scan_id
                            ORDER BY i.created_at DESC""").fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Incident ID", "Created At (UTC)", "Category", "Severity", "Confidence", "Status", "Description", "Source Image", "Scan ID"])
    for row in rows:
        writer.writerow([row["id"], row["created_at"], row["category"], row["severity"], row["confidence"], row["status"], row["description"], row["original_name"], row["scan_id"]])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=aegisscan-incidents.csv"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
