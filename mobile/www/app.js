(function () {
  "use strict";

  var DATA = [];

  var POS_LABELS = {
    "s": "Substantivo",
    "v": "Verbo",
    "adj": "Adjetivo",
    "adv": "Advérbio",
    "s adj": "Subst./Adj."
  };

  var els = {
    search: document.getElementById("search"),
    sessionFilter: document.getElementById("session-filter"),
    sortMode: document.getElementById("sort-mode"),
    list: document.getElementById("entry-list"),
    summary: document.getElementById("result-summary"),
    empty: document.getElementById("empty-state"),
    player: document.getElementById("player"),
    statCount: document.getElementById("stat-count"),
    statSessions: document.getElementById("stat-sessions"),
    statDuration: document.getElementById("stat-duration"),
    syncBtn: document.getElementById("sync-btn"),
    syncStatus: document.getElementById("sync-status")
  };

  function stripDiacritics(str) {
    return (str || "").normalize("NFKD").replace(/[̀-ͯ]/g, "");
  }

  function formatDuration(ms) {
    var totalSeconds = ms / 1000;
    var m = Math.floor(totalSeconds / 60);
    var s = totalSeconds - m * 60;
    if (m > 0) return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
    return s.toFixed(2) + "s";
  }

  function formatTotalDuration(ms) {
    var totalSeconds = Math.round(ms / 1000);
    var h = Math.floor(totalSeconds / 3600);
    var m = Math.floor((totalSeconds % 3600) / 60);
    var s = totalSeconds % 60;
    var parts = [];
    if (h) parts.push(h + "h");
    parts.push((h && m < 10 ? "0" : "") + m + "m");
    parts.push((s < 10 ? "0" : "") + s + "s");
    return parts.join(" ");
  }

  function segnumOf(entry) {
    var raw = entry.fields["phrase-segnum_en"] || entry.fields["segnum_en"];
    var n = parseInt(raw, 10);
    return isNaN(n) ? entry.start_ms : n;
  }

  function glossOf(entry) {
    return entry.fields["phrase-gls_pt"] || entry.fields["word-gls_pt"] || "";
  }

  function posOf(entry) {
    var raw = (entry.fields["word-pos_pt"] || "").trim();
    if (!raw) return "";
    return POS_LABELS[raw] || raw.toUpperCase();
  }

  function highlight(text, needle) {
    if (!needle || !text) return escapeHtml(text || "");
    var plainIdx = text.toLowerCase().indexOf(needle.toLowerCase());
    if (plainIdx === -1) return escapeHtml(text);
    return (
      escapeHtml(text.slice(0, plainIdx)) +
      "<mark>" + escapeHtml(text.slice(plainIdx, plainIdx + needle.length)) + "</mark>" +
      escapeHtml(text.slice(plainIdx + needle.length))
    );
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function populateSessions() {
    els.sessionFilter.innerHTML = '<option value="">All</option>';
    var sessions = {};
    DATA.forEach(function (e) { sessions[e.session_id] = (sessions[e.session_id] || 0) + 1; });
    Object.keys(sessions).sort().forEach(function (id) {
      var opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id + " (" + sessions[id] + ")";
      els.sessionFilter.appendChild(opt);
    });
  }

  function updateStats() {
    var sessions = {};
    var totalMs = 0;
    DATA.forEach(function (e) { sessions[e.session_id] = true; totalMs += e.duration_ms; });
    els.statCount.textContent = DATA.length;
    els.statSessions.textContent = Object.keys(sessions).length;
    els.statDuration.textContent = formatTotalDuration(totalMs);
  }

  var currentAudioEntry = null;

  function stopPlayback() {
    els.player.pause();
    if (currentAudioEntry) {
      var row = els.list.querySelector('[data-id="' + cssEscape(currentAudioEntry.id) + '"]');
      if (row) row.classList.remove("is-playing");
    }
    currentAudioEntry = null;
  }

  function cssEscape(str) {
    return String(str).replace(/[^a-zA-Z0-9_-]/g, function (c) {
      return "\\" + c;
    });
  }

  async function playEntry(entry, row) {
    if (currentAudioEntry && currentAudioEntry.id === entry.id) {
      stopPlayback();
      return;
    }
    stopPlayback();
    var src = await window.KararaSync.resolveAudioSrc(entry);
    els.player.src = src;
    els.player.play().catch(function () {});
    currentAudioEntry = entry;
    row.classList.add("is-playing");
  }

  els.player.addEventListener("ended", stopPlayback);

  els.player.addEventListener("timeupdate", function () {
    if (!currentAudioEntry) return;
    var row = els.list.querySelector('[data-id="' + cssEscape(currentAudioEntry.id) + '"]');
    if (!row) return;
    var fill = row.querySelector(".progress-fill");
    if (fill && els.player.duration) {
      fill.style.width = (els.player.currentTime / els.player.duration * 100) + "%";
    }
  });

  function renderRow(entry, needle) {
    var li = document.createElement("li");
    li.className = "entry";
    li.setAttribute("data-id", entry.id);
    li.setAttribute("role", "listitem");

    var pos = posOf(entry);
    var gloss = glossOf(entry);
    var segnum = entry.fields["phrase-segnum_en"] || entry.fields["segnum_en"] || "";

    li.innerHTML =
      '<span class="col-num">' + escapeHtml(segnum || "—") + '</span>' +
      '<span class="col-play"><button class="play-btn" type="button" aria-label="Play ' + escapeHtml(gloss || entry.text) + '">' +
        '<svg class="icon-play" width="13" height="13" viewBox="0 0 16 16"><path d="M3 1.5v13l11-6.5z" fill="currentColor"/></svg>' +
        '<svg class="icon-pause" width="13" height="13" viewBox="0 0 16 16" hidden><rect x="3" y="2" width="3.5" height="12" fill="currentColor"/><rect x="9.5" y="2" width="3.5" height="12" fill="currentColor"/></svg>' +
      '</button></span>' +
      '<span class="col-ipa">' + highlight(entry.text, needle) + '</span>' +
      '<span class="col-gloss">' + highlight(gloss, needle) + '</span>' +
      '<span class="col-pos">' + (pos ? '<span class="pos-chip">' + escapeHtml(pos) + '</span>' : '') + '</span>' +
      '<span class="col-dur">' + formatDuration(entry.duration_ms) + '</span>' +
      '<span class="progress-track"><span class="progress-fill"></span></span>';

    li.querySelector(".play-btn").addEventListener("click", function () {
      playEntry(entry, li);
    });

    return li;
  }

  function applyPlayingIcons() {
    els.list.querySelectorAll(".entry").forEach(function (row) {
      var playing = row.classList.contains("is-playing");
      row.querySelector(".icon-play").hidden = playing;
      row.querySelector(".icon-pause").hidden = !playing;
    });
  }

  var observer = new MutationObserver(applyPlayingIcons);
  observer.observe(els.list, { attributes: true, attributeFilter: ["class"], subtree: true });

  function render() {
    var query = els.search.value.trim();
    var queryNorm = stripDiacritics(query).toLowerCase();
    var session = els.sessionFilter.value;
    var sortMode = els.sortMode.value;

    var filtered = DATA.filter(function (e) {
      if (session && e.session_id !== session) return false;
      if (!queryNorm) return true;
      return e.search_blob.indexOf(queryNorm) !== -1;
    });

    filtered.sort(function (a, b) {
      if (sortMode === "alpha") {
        return glossOf(a).localeCompare(glossOf(b), "pt");
      }
      if (sortMode === "duration") {
        return b.duration_ms - a.duration_ms;
      }
      if (a.session_id !== b.session_id) return a.session_id.localeCompare(b.session_id);
      return segnumOf(a) - segnumOf(b);
    });

    els.list.innerHTML = "";
    var frag = document.createDocumentFragment();
    filtered.forEach(function (entry) {
      var row = renderRow(entry, query);
      if (currentAudioEntry && currentAudioEntry.id === entry.id) {
        row.classList.add("is-playing");
      }
      frag.appendChild(row);
    });
    els.list.appendChild(frag);
    applyPlayingIcons();

    els.empty.hidden = filtered.length !== 0;
    els.summary.textContent = filtered.length === DATA.length
      ? "Showing all " + filtered.length + " entries"
      : "Showing " + filtered.length + " of " + DATA.length + " entries";
  }

  els.search.addEventListener("input", render);
  els.sessionFilter.addEventListener("change", render);
  els.sortMode.addEventListener("change", render);

  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== els.search) {
      e.preventDefault();
      els.search.focus();
    }
    if (e.key === "Escape" && document.activeElement === els.search) {
      els.search.value = "";
      render();
    }
  });

  async function refreshSyncStatusLabel() {
    var last = await window.KararaSync.lastSyncAt();
    if (last) {
      els.syncStatus.textContent = "Last synced " + last.toLocaleString();
    } else if (window.KararaSync.isNative()) {
      els.syncStatus.textContent = "Not synced yet — showing the built-in dataset.";
    } else {
      els.syncStatus.textContent = "";
    }
  }

  function wireSyncButton() {
    if (!window.KararaSync.isNative()) {
      els.syncBtn.disabled = true;
      els.syncBtn.title = "Sync is only available in the installed app";
      return;
    }
    els.syncBtn.addEventListener("click", async function () {
      els.syncBtn.disabled = true;
      els.syncStatus.textContent = "Syncing…";
      try {
        var result = await window.KararaSync.syncNow(function (done, total, current) {
          els.syncStatus.textContent = "Syncing " + (done + 1) + "/" + total + "…";
        });
        DATA = await window.KararaSync.loadEffectiveData();
        populateSessions();
        updateStats();
        render();
        els.syncStatus.textContent = result.downloaded > 0
          ? "Synced " + result.downloaded + " new clip(s). " + result.totalEntries + " entries total."
          : "Already up to date (" + result.totalEntries + " entries).";
      } catch (err) {
        els.syncStatus.textContent = "Sync failed: " + err.message;
      } finally {
        els.syncBtn.disabled = false;
      }
    });
  }

  async function init() {
    DATA = await window.KararaSync.loadEffectiveData();
    populateSessions();
    updateStats();
    render();
    wireSyncButton();
    refreshSyncStatusLabel();
  }

  init();
})();
