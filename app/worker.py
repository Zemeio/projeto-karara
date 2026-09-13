"""
Single-process, single-job-at-a-time queue worker: polls jobs_db for pending
uploads, unpacks each .karara file, runs the ffmpeg extraction, and cleans up.

Deliberately serial (one ffmpeg run at a time) — this runs on a resource-
constrained e2-micro free-tier VM, and one job saturates it plenty.
"""
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from jobs_db import JobsDB
from karara_zip import extract as extract_karara_zip, InvalidKararaFile
import extract_eaf

DATA_DIR = Path(os.environ.get("KARARA_DATA_DIR", "/opt/karara-data"))
JOBS_DB_PATH = DATA_DIR / "jobs.db"
POLL_INTERVAL_SECONDS = float(os.environ.get("KARARA_POLL_INTERVAL", "5"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(levelname)s %(message)s")
log = logging.getLogger("karara-worker")


def process_one(db: JobsDB, job: dict):
    job_id = job["id"]
    stored_path = Path(job["stored_path"])
    log.info("processing job %s (%s)", job_id, job["original_filename"])

    with tempfile.TemporaryDirectory(prefix=f"karara-{job_id}-", dir=DATA_DIR / "tmp") as tmp:
        tmp_dir = Path(tmp)
        try:
            eaf_path, audio_path = extract_karara_zip(stored_path, tmp_dir)
            summary = extract_eaf.run(eaf_path, audio_path, DATA_DIR)
        except InvalidKararaFile as exc:
            log.warning("job %s invalid: %s", job_id, exc)
            db.mark_error(job_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a bad job must never kill the worker loop
            log.exception("job %s failed", job_id)
            db.mark_error(job_id, f"{type(exc).__name__}: {exc}")
            return

    db.mark_done(job_id, summary["session_id"], summary["entries_count"])
    log.info("job %s done: session=%s entries=%d", job_id, summary["session_id"], summary["entries_count"])

    stored_path.unlink(missing_ok=True)


def main():
    (DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    db = JobsDB(JOBS_DB_PATH)
    log.info("karara-worker started, watching %s", JOBS_DB_PATH)

    while True:
        job = db.claim_next()
        if job is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        process_one(db, job)


if __name__ == "__main__":
    sys.exit(main())
