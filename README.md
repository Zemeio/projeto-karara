# Karara Sound Index — server

Turns `.karara` files (a zip containing one ELAN `.eaf` + one audio file)
into searchable, playable clips, served as a static site. Runs on the same
free-tier `syncplay-server` e2-micro VM as the Syncplay server, deliberately
processing **one upload at a time** to stay within the box's limits.

## Architecture

```
        POST /api/upload                    GET /
              |                                |
              v                                v
        +-----------+                   +-------------+
        | app.py    |--- jobs.db ------>|  worker.py  |---> ffmpeg
        | (Flask,   |     (SQLite,      |  (single    |
        |  waitress)|      queue)       |   process)  |
        +-----------+                   +-------------+
              |                                |
              +--------------> /opt/karara-data <-------------+
                             (audio/, data/, jobs.db —
                              persists across deploys)
```

- **`app.py`** — serves the static site (`app/static/`) and the API:
  `POST /api/upload` (bearer-token auth, validates the zip's structure
  before ever queuing it), `GET /api/jobs[/<id>]`, `GET /health`.
- **`worker.py`** — the only process that runs `ffmpeg`. Polls `jobs.db`,
  processes the oldest pending job, and only that one, at a time.
- **`extract_eaf.py`** — the actual EAF-parsing/clip-cutting logic (shared,
  unchanged in behavior from the version validated locally).
- Code (`/opt/karara-app/releases/<sha>`, symlinked as `current`) is
  replaced on every deploy. Data (`/opt/karara-data`) is not — it's a
  separate directory the app/worker own, untouched by deploys.

## Deploys

Push to `main` (touching `app/**`) → GitHub Actions authenticates to GCP via
**Workload Identity Federation** (no stored key — see `secrets.GCP_*` in the
repo settings) as the `karara-deployer` service account → stages the new
`app/` code at `/opt/karara-app/incoming/<sha>/app` over an IAP-tunneled scp
→ runs `sudo /opt/karara-app/deploy.sh <sha>` on the VM.

`deploy.sh` is the **only** thing that service account can run as root (see
`/etc/sudoers.d/karara-deploy` on the VM) — it builds a venv, flips the
`current` symlink, restarts both services, health-checks `/health`, and
automatically rolls the symlink back if the health check fails. It is
provisioned on the VM by hand, not by CI — CI can only ever change what runs
*inside* the app, never the promotion/rollback machinery itself.

The systemd unit files (`systemd/*.service`) and `deploy.sh` are likewise
provisioned by hand (see "One-time VM setup" below) and are not touched by
the automated pipeline. Update them by editing on the VM directly (or
re-running the relevant provisioning commands) if they ever need to change.

## Operating it

**Upload a session:**
```
curl -X POST https://<vm-ip>:8080/api/upload \
  -H "Authorization: Bearer $KARARA_UPLOAD_TOKEN" \
  -F "file=@my_session.karara"
```
Returns `{"job_id": "...", "status_url": "/api/jobs/..."}` immediately —
processing happens in the background, one job at a time.

**Check on it:** `GET /api/jobs/<job_id>` or `GET /api/jobs` for recent history.

**A `.karara` file** is just a zip containing exactly one `.eaf` and exactly
one audio file (`.wav`/`.mp3`/`.flac`/`.m4a`/`.ogg`) — rename `.zip` to
`.karara` or leave it `.zip`, the extension itself isn't checked, only the
contents are.

## Android app

`mobile/` is a Capacitor-wrapped offline build of this same site, for use
somewhere the server can't be reached reliably — it ships with the dataset
already bundled and can sync incrementally when it does get a connection.
See `mobile/README.md`. Built and published automatically by
`.github/workflows/build-android.yml` to a public release:
`https://github.com/Zemeio/projeto-karara/releases/download/android-latest/app-debug.apk`

## One-time VM setup (already done for `syncplay-server`)

Recorded here so it's reproducible if this ever moves to a new VM:

1. `apt-get install ffmpeg`, plus a 1GB swapfile (`/swapfile`) as an OOM
   safety net — this box has no memory headroom to spare.
2. System user `karara` (`--system --no-create-home --shell /usr/sbin/nologin`)
   — owns `/opt/karara-data` and runs both services. Never used for deploys.
3. `/opt/karara-app/{releases,incoming}` (root-owned; `incoming` is
   `1777`/sticky so any authenticated SSH principal can stage a release
   without pre-provisioning its Unix account) and `/opt/karara-data/{audio,data,incoming,tmp}`
   (owned by `karara`, `750`).
4. `deploy/deploy.sh` copied to `/opt/karara-app/deploy.sh` (root:root, `700`).
5. `systemd/*.service` copied to `/etc/systemd/system/`, `daemon-reload`,
   `enable --now`.
6. `/etc/karara/app.env` (root:root, `600`) — holds `KARARA_UPLOAD_TOKEN=...`,
   loaded by `karara-app.service`. Not in git.
7. GCP side: a Workload Identity Pool (`github-pool`) + OIDC provider
   (`github-provider`), locked to `assertion.repository == 'Zemeio/projeto-karara'`
   via an attribute condition, and a dedicated `karara-deployer` service
   account with exactly two project-level roles (`roles/iap.tunnelResourceAccessor`,
   `roles/compute.osLogin`) plus `roles/iam.workloadIdentityUser` scoped to
   that repo's principal set. No JSON key exists anywhere.
