import json
import os
import secrets
import shutil
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort

from jobs_db import JobsDB
from karara_zip import inspect as inspect_karara_zip, InvalidKararaFile

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("KARARA_DATA_DIR", "/opt/karara-data"))
STATIC_DIR = Path(os.environ.get("KARARA_STATIC_DIR", BASE_DIR / "static"))
UPLOAD_TOKEN = os.environ.get("KARARA_UPLOAD_TOKEN", "")
MAX_UPLOAD_BYTES = int(os.environ.get("KARARA_MAX_UPLOAD_MB", "700")) * 1024 * 1024
MAX_QUEUE_DEPTH = int(os.environ.get("KARARA_MAX_QUEUE_DEPTH", "5"))

AUDIO_DIR = DATA_DIR / "audio"
DATA_SUBDIR = DATA_DIR / "data"
INCOMING_DIR = DATA_DIR / "incoming"
JOBS_DB_PATH = DATA_DIR / "jobs.db"

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
db = JobsDB(JOBS_DB_PATH)


def _require_upload_token():
    if not UPLOAD_TOKEN:
        abort(503, description="Upload endpoint is not configured (no token set on the server)")
    auth = request.headers.get("Authorization", "")
    provided = auth[7:] if auth.startswith("Bearer ") else ""
    if not provided or not secrets.compare_digest(provided, UPLOAD_TOKEN):
        abort(401, description="Missing or invalid bearer token")


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<string:filename>")
def static_root_file(filename):
    if filename not in ("style.css", "app.js", "README.md"):
        abort(404)
    return send_from_directory(STATIC_DIR, filename)


@app.get("/data/<string:filename>")
def data_file(filename):
    if filename not in ("entries.json", "entries.js"):
        abort(404)
    return send_from_directory(DATA_SUBDIR, filename)


@app.get("/audio/<string:session_id>/<string:filename>")
def audio_file(session_id, filename):
    session_dir = AUDIO_DIR / session_id
    return send_from_directory(session_dir, filename)


@app.post("/api/upload")
def upload():
    _require_upload_token()

    if db.pending_and_processing_count() >= MAX_QUEUE_DEPTH:
        abort(429, description="Queue is full, try again shortly")

    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        abort(400, description="Send a multipart form with a 'file' field")

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    stored_path = INCOMING_DIR / f"{uuid.uuid4().hex}.karara"
    uploaded.save(stored_path)

    try:
        inspect_karara_zip(stored_path)
    except InvalidKararaFile as exc:
        stored_path.unlink(missing_ok=True)
        abort(400, description=str(exc))

    job_id = db.enqueue(uploaded.filename, str(stored_path))
    return jsonify({"job_id": job_id, "status": "pending", "status_url": f"/api/jobs/{job_id}"}), 202


@app.get("/api/jobs")
def list_jobs():
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(db.recent(limit))


@app.get("/api/jobs/<string:job_id>")
def get_job(job_id):
    job = db.get(job_id)
    if job is None:
        abort(404)
    return jsonify(job)


def _load_entries():
    entries_json = DATA_SUBDIR / "entries.json"
    if not entries_json.exists():
        return []
    try:
        return json.loads(entries_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


@app.get("/api/sync/manifest")
def sync_manifest():
    """Lets the offline (Android) build diff what it already has against what
    the server currently knows about, so it only downloads what's missing."""
    sessions = {}
    for entry in _load_entries():
        session = sessions.setdefault(entry["session_id"], {"entries_count": 0, "audio_files": []})
        session["entries_count"] += 1
        session["audio_files"].append(entry["audio_file"])
    return jsonify({"sessions": sessions, "generated_at": time.time()})


@app.get("/health")
def health():
    entries_count = len(_load_entries())

    disk_free_mb = shutil.disk_usage(DATA_DIR).free // (1024 * 1024)
    return jsonify({
        "ok": True,
        "entries_count": entries_count,
        "disk_free_mb": disk_free_mb,
        "queue_depth": db.pending_and_processing_count(),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
