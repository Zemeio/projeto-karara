/*
 * Sync layer for the offline (Capacitor) build of the Karara Sound Index.
 *
 * The app always has a baseline dataset bundled inside the APK (window.SEARCH_DATA
 * from data/entries.js, plus the audio files listed in data/bundled-manifest.json).
 * This module lets it *optionally* pull newer/additional sessions from the server
 * whenever a connection happens to be available, storing them in the app's own
 * writable storage (Directory.Data) so they persist offline afterward too.
 *
 * Deliberately calls the native bridge directly (window.Capacitor.Plugins.*)
 * instead of importing the @capacitor/filesystem / @capacitor/preferences npm
 * packages, so this stays a plain script with no bundler step, matching the
 * rest of the site.
 */
(function () {
  "use strict";

  var DEFAULT_SERVER = "http://35.226.170.192:8080";
  var PREFS_KNOWN_FILES = "karara_known_files"; // JSON: { [sessionId]: string[] of already-synced relative paths }
  var PREFS_LAST_SYNC = "karara_last_sync_at";
  var PREFS_SERVER_URL = "karara_server_url";

  var bundledManifest = null; // { [sessionId]: string[] } loaded once from data/bundled-manifest.json
  var syncedEntries = null; // full entries array from the last successful sync, or null

  function isNative() {
    return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  }

  function plugins() {
    return window.Capacitor.Plugins;
  }

  async function loadBundledManifest() {
    if (bundledManifest) return bundledManifest;
    try {
      var res = await fetch("data/bundled-manifest.json");
      bundledManifest = await res.json();
    } catch (err) {
      bundledManifest = {};
    }
    return bundledManifest;
  }

  async function getKnownFiles() {
    if (!isNative()) return {};
    var res = await plugins().Preferences.get({ key: PREFS_KNOWN_FILES });
    try {
      return res.value ? JSON.parse(res.value) : {};
    } catch (err) {
      return {};
    }
  }

  async function setKnownFiles(known) {
    if (!isNative()) return;
    await plugins().Preferences.set({ key: PREFS_KNOWN_FILES, value: JSON.stringify(known) });
  }

  function arrayBufferToBase64(buffer) {
    var bytes = new Uint8Array(buffer);
    var chunkSize = 0x8000;
    var chunks = [];
    for (var i = 0; i < bytes.length; i += chunkSize) {
      chunks.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize)));
    }
    return btoa(chunks.join(""));
  }

  async function ensureDir(path) {
    try {
      await plugins().Filesystem.mkdir({ path: path, directory: "DATA", recursive: true });
    } catch (err) {
      // already exists — fine
    }
  }

  async function downloadFile(serverUrl, relPath) {
    var res = await fetch(serverUrl + "/" + relPath);
    if (!res.ok) throw new Error("HTTP " + res.status + " for " + relPath);
    var buffer = await res.arrayBuffer();
    var base64 = arrayBufferToBase64(buffer);
    var destPath = "karara-sync/" + relPath;
    await ensureDir("karara-sync/" + relPath.substring(0, relPath.lastIndexOf("/")));
    await plugins().Filesystem.writeFile({ path: destPath, data: base64, directory: "DATA", recursive: true });
  }

  async function getServerUrl() {
    if (!isNative()) return DEFAULT_SERVER;
    var res = await plugins().Preferences.get({ key: PREFS_SERVER_URL });
    return res.value || DEFAULT_SERVER;
  }

  /** Returns the merged entries array to render: synced copy if we have one (it's
   * always a full superset snapshot from the server), else the bundled baseline. */
  async function loadEffectiveData() {
    if (isNative()) {
      try {
        var read = await plugins().Filesystem.readFile({
          path: "karara-sync/entries.json",
          directory: "DATA",
          encoding: "utf8",
        });
        var parsed = JSON.parse(read.data);
        if (Array.isArray(parsed) && parsed.length) {
          syncedEntries = parsed;
          return parsed;
        }
      } catch (err) {
        // no synced copy yet — fall through to bundled
      }
    }
    return window.SEARCH_DATA || [];
  }

  /** Resolves the actual playable src for an entry: a synced on-disk file if we
   * have one, otherwise the bundled relative path shipped inside the APK/site. */
  async function resolveAudioSrc(entry) {
    if (isNative()) {
      var known = await getKnownFiles();
      var sessionKnown = known[entry.session_id] || [];
      if (sessionKnown.indexOf(entry.audio_file) !== -1) {
        try {
          var uriResult = await plugins().Filesystem.getUri({
            path: "karara-sync/" + entry.audio_file,
            directory: "DATA",
          });
          return window.Capacitor.convertFileSrc(uriResult.uri);
        } catch (err) {
          // fall through to bundled path
        }
      }
    }
    return entry.audio_file;
  }

  async function syncNow(onProgress) {
    if (!isNative()) {
      throw new Error("Sync is only available in the installed app.");
    }
    var serverUrl = await getServerUrl();
    var manifestRes = await fetch(serverUrl + "/api/sync/manifest");
    if (!manifestRes.ok) throw new Error("Could not reach the server (HTTP " + manifestRes.status + ")");
    var manifest = await manifestRes.json();

    var bundled = await loadBundledManifest();
    var known = await getKnownFiles();

    var toDownload = [];
    Object.keys(manifest.sessions || {}).forEach(function (sessionId) {
      var bundledFiles = bundled[sessionId] || [];
      var knownFiles = known[sessionId] || [];
      manifest.sessions[sessionId].audio_files.forEach(function (relPath) {
        if (bundledFiles.indexOf(relPath) === -1 && knownFiles.indexOf(relPath) === -1) {
          toDownload.push({ sessionId: sessionId, relPath: relPath });
        }
      });
    });

    var done = 0;
    for (var i = 0; i < toDownload.length; i++) {
      var item = toDownload[i];
      onProgress && onProgress(done, toDownload.length, item.relPath);
      await downloadFile(serverUrl, item.relPath);
      known[item.sessionId] = known[item.sessionId] || [];
      known[item.sessionId].push(item.relPath);
      done++;
    }
    await setKnownFiles(known);

    // entries.json is small — always refetch the full current copy
    var entriesRes = await fetch(serverUrl + "/data/entries.json");
    var entriesText = await entriesRes.text();
    await plugins().Filesystem.writeFile({
      path: "karara-sync/entries.json",
      data: entriesText,
      directory: "DATA",
      encoding: "utf8",
      recursive: true,
    });
    syncedEntries = JSON.parse(entriesText);

    var now = Date.now();
    await plugins().Preferences.set({ key: PREFS_LAST_SYNC, value: String(now) });

    return { downloaded: toDownload.length, totalEntries: syncedEntries.length, syncedAt: now };
  }

  async function lastSyncAt() {
    if (!isNative()) return null;
    var res = await plugins().Preferences.get({ key: PREFS_LAST_SYNC });
    return res.value ? new Date(parseInt(res.value, 10)) : null;
  }

  window.KararaSync = {
    isNative: isNative,
    loadEffectiveData: loadEffectiveData,
    resolveAudioSrc: resolveAudioSrc,
    syncNow: syncNow,
    lastSyncAt: lastSyncAt,
  };
})();
