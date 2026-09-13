"""Entry point for the systemd unit: `python serve.py`. Keeping this as a
plain script (not the `waitress-serve` console script) ensures this
directory — not the venv's bin/ — ends up as sys.path[0], so app.py's
sibling imports (jobs_db, karara_zip, extract_eaf) resolve correctly."""
import os

from waitress import serve

from app import app

if __name__ == "__main__":
    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
