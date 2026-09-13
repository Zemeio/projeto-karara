# Karara Sound Index — Android

An offline-first Capacitor wrapper around the same `www/` site the server
serves — built for use somewhere with little or no internet: the app ships
with the current dataset baked into the APK, and can optionally top itself
up from the server whenever it happens to get a connection.

## Why Capacitor, not a rewrite

`index.html` / `style.css` / `app.js` here are the same search-and-play UI
as the server's site — Capacitor just wraps them in a real, sideloadable
Android app with full filesystem access (no browser storage-quota risk) and
a bundled starting dataset (no first-run download required). `app.js` and
`index.html` differ slightly from `app/static/` on the server: they resolve
data/audio through `sync.js` (bundled-first, synced-second) instead of
always fetching live.

## How data gets onto the phone

1. **Bundled at build time.** `www/data/` and `www/audio/` are a snapshot
   pulled from the live server before each build (see "Updating the bundled
   dataset" below) and shipped inside the APK — the app works fully offline
   from the moment it's installed.
2. **Synced later, opportunistically.** Tapping "Sync" hits
   `GET /api/sync/manifest` on the server, diffs it against what the app
   already has (bundled + previously synced — tracked via Capacitor
   Preferences so nothing already-bundled gets re-downloaded), and pulls
   only the missing audio files plus a fresh `entries.json` into the app's
   own writable storage (`Directory.Data`, survives app updates). One file
   at a time, so a dropped connection just means retrying that file next
   time, not a corrupted local dataset.

The app never needs to run `ffmpeg` itself — it only ever consumes clips
the server has already cut.

## Updating the bundled dataset (before a build)

```
cd mobile
python -c "
import json, urllib.request
from pathlib import Path
base = Path('www')
urllib.request.urlretrieve('http://35.226.170.192:8080/data/entries.json', base/'data/entries.json')
entries = json.loads((base/'data/entries.json').read_text(encoding='utf-8'))
for e in entries:
    dest = base / e['audio_file']
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(f'http://35.226.170.192:8080/{e[\"audio_file\"]}', dest)
manifest = {}
for e in entries: manifest.setdefault(e['session_id'], []).append(e['audio_file'])
(base/'data/bundled-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(len(entries), 'entries bundled')
"
```

Commit the refreshed `www/data/` and `www/audio/`, push to `main` — the
`build-android` workflow rebuilds and republishes the APK automatically.
People who already have the app still get anything new via "Sync" without
needing to reinstall; a fresh install just starts more up to date.

## Getting the APK

Every push to `main` touching `mobile/**` rebuilds it and publishes to a
public release, so anyone can grab it directly (no GitHub login needed —
important for a field download over a weak connection):

```
https://github.com/Zemeio/projeto-karara/releases/download/android-latest/app-debug.apk
```

It's a debug build (unsigned but installable) — Android will require
enabling "install unknown apps" for whatever app is used to open the
downloaded file.

## If the server's IP changes

The VM's IP is ephemeral (see [[syncplay_gcp_vm]] memory / main README).
Three places reference it and all three need updating together:
- `www/sync.js` (`DEFAULT_SERVER`)
- `android/app/src/main/res/xml/network_security_config.xml` (the cleartext
  allow-list — Android blocks plain HTTP to any host not explicitly listed)
- this file

## Local development

No Android SDK is required to *edit* this — only to build. `npx cap sync
android` after any `www/` change keeps the native project's copy current;
actually compiling (`gradlew assembleDebug`) needs a JDK + Android SDK,
which is why the build happens in CI instead of requiring either locally.
