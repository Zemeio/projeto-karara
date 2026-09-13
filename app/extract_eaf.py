"""
Extract time-aligned annotations from an ELAN (.eaf) file into individual
audio clips, and (re)build the searchable JSON index consumed by the site.

Importable as a module (see `run()`, used by worker.py) or runnable as a
CLI for local testing:

    python extract_eaf.py <path-to-eaf> <path-to-audio> --data-dir <dir> [--session-id NAME] [--force]
"""
import argparse
import json
import re
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path


def slugify(text: str, max_len: int = 40) -> str:
    if not text:
        return "clip"
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (ascii_text or "clip")[:max_len]


def strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def parse_eaf(eaf_path: Path):
    tree = ET.parse(eaf_path)
    root = tree.getroot()

    time_slots = {}
    for ts in root.iter("TIME_SLOT"):
        val = ts.get("TIME_VALUE")
        if val is not None:
            time_slots[ts.get("TIME_SLOT_ID")] = int(val)

    alignable_types = set()
    for lt in root.iter("LINGUISTIC_TYPE"):
        if lt.get("TIME_ALIGNABLE") == "true":
            alignable_types.add(lt.get("LINGUISTIC_TYPE_ID"))

    tiers = {}
    for tier in root.iter("TIER"):
        tid = tier.get("TIER_ID")
        tiers[tid] = {
            "linguistic_type": tier.get("LINGUISTIC_TYPE_REF"),
            "lang": tier.get("LANG_REF") or "",
            "participant": tier.get("PARTICIPANT") or "",
            "parent_ref": tier.get("PARENT_REF"),
            "element": tier,
        }

    annotations = {}
    for tier_id, tinfo in tiers.items():
        for ann in tinfo["element"].iter("ANNOTATION"):
            aligned = ann.find("ALIGNABLE_ANNOTATION")
            refd = ann.find("REF_ANNOTATION")
            if aligned is not None:
                val_el = aligned.find("ANNOTATION_VALUE")
                annotations[aligned.get("ANNOTATION_ID")] = {
                    "tier_id": tier_id,
                    "value": (val_el.text or "").strip() if val_el is not None else "",
                    "ts1": aligned.get("TIME_SLOT_REF1"),
                    "ts2": aligned.get("TIME_SLOT_REF2"),
                    "ref": None,
                }
            elif refd is not None:
                val_el = refd.find("ANNOTATION_VALUE")
                annotations[refd.get("ANNOTATION_ID")] = {
                    "tier_id": tier_id,
                    "value": (val_el.text or "").strip() if val_el is not None else "",
                    "ts1": None,
                    "ts2": None,
                    "ref": refd.get("ANNOTATION_REF"),
                }

    def resolve_root(ann_id):
        seen = set()
        current = ann_id
        while current not in seen:
            seen.add(current)
            info = annotations.get(current)
            if info is None:
                return None
            if info["ts1"] is not None:
                return current
            if info["ref"] is None:
                return None
            current = info["ref"]
        return None

    tier_annotation_counts = {}
    for ann in annotations.values():
        if ann["ts1"] is not None:
            tier_annotation_counts[ann["tier_id"]] = tier_annotation_counts.get(ann["tier_id"], 0) + 1

    segment_tiers = set()
    for tier_id, tinfo in tiers.items():
        if tinfo["linguistic_type"] in alignable_types and tier_annotation_counts.get(tier_id, 0) > 1:
            if "title" in (tinfo["linguistic_type"] or "").lower():
                continue
            segment_tiers.add(tier_id)

    extra_fields = {}
    for ann_id, info in annotations.items():
        if info["tier_id"] in segment_tiers and info["ts1"] is not None:
            continue
        root_id = resolve_root(ann_id)
        if root_id is None or root_id == ann_id:
            continue
        if not info["value"]:
            continue
        tinfo = tiers[info["tier_id"]]
        field_key = f'{tinfo["linguistic_type"]}_{tinfo["lang"]}'.strip("_")
        extra_fields.setdefault(root_id, {}).setdefault(field_key, []).append(info["value"])

    entries = []
    for ann_id, info in annotations.items():
        if info["tier_id"] not in segment_tiers or info["ts1"] is None:
            continue
        tinfo = tiers[info["tier_id"]]
        start_ms = time_slots.get(info["ts1"])
        end_ms = time_slots.get(info["ts2"])
        if start_ms is None or end_ms is None:
            continue
        fields = extra_fields.get(ann_id, {})
        entries.append({
            "ann_id": ann_id,
            "tier_id": info["tier_id"],
            "lang": tinfo["lang"],
            "participant": tinfo["participant"],
            "text": info["value"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "duration_ms": end_ms - start_ms,
            "fields": {k: " ".join(v) for k, v in fields.items()},
        })

    entries.sort(key=lambda e: e["start_ms"])
    return entries


def build_search_blob(entry) -> str:
    parts = [entry["text"], entry["participant"]]
    parts.extend(entry["fields"].values())
    blob = " ".join(p for p in parts if p)
    return f"{blob} {strip_diacritics(blob)}".lower()


def export_clips(entries, audio_path: Path, session_dir: Path, data_dir: Path, force: bool):
    session_dir.mkdir(parents=True, exist_ok=True)
    for i, entry in enumerate(entries, start=1):
        segnum = entry["fields"].get("phrase-segnum_en") or entry["fields"].get("segnum_en")
        gloss = entry["fields"].get("phrase-gls_pt") or entry["fields"].get("word-gls_pt") or entry["text"]
        prefix = str(segnum).zfill(4) if segnum else str(i).zfill(4)
        filename = f'{prefix}_{slugify(gloss)}.mp3'
        out_path = session_dir / filename
        entry["audio_file"] = out_path.relative_to(data_dir).as_posix()

        if out_path.exists() and not force:
            continue

        start_s = entry["start_ms"] / 1000.0
        duration_s = max(entry["duration_ms"], 1) / 1000.0
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{start_s:.3f}",
            "-i", str(audio_path),
            "-t", f"{duration_s:.3f}",
            "-vn", "-acodec", "libmp3lame", "-q:a", "2", "-ar", "44100",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for {filename}: {result.stderr.strip()}")


def merge_entries(data_dir: Path, session_id: str, new_entries: list):
    data_subdir = data_dir / "data"
    data_subdir.mkdir(parents=True, exist_ok=True)
    entries_json = data_subdir / "entries.json"
    entries_js = data_subdir / "entries.js"

    existing = []
    if entries_json.exists():
        existing = json.loads(entries_json.read_text(encoding="utf-8"))
    existing = [e for e in existing if e.get("session_id") != session_id]

    for entry in new_entries:
        entry["session_id"] = session_id
        entry["id"] = f'{session_id}__{entry["ann_id"]}'
        entry["search_blob"] = build_search_blob(entry)

    merged = existing + new_entries
    merged.sort(key=lambda e: (e["session_id"], e["start_ms"]))

    entries_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    entries_js.write_text(
        "// Auto-generated by extract_eaf.py — do not edit by hand.\n"
        f"window.SEARCH_DATA = {json.dumps(merged, ensure_ascii=False)};\n",
        encoding="utf-8",
    )
    return merged


def run(eaf_path: Path, audio_path: Path, data_dir: Path, session_id: str = None, force: bool = False) -> dict:
    """Parse `eaf_path`, cut clips from `audio_path` into `data_dir`/audio/<session>,
    merge the resulting entries into `data_dir`/data/entries.json(.js), and
    return a small summary dict. Raises on any failure."""
    eaf_path = Path(eaf_path)
    audio_path = Path(audio_path)
    data_dir = Path(data_dir)
    session_id = session_id or eaf_path.stem

    entries = parse_eaf(eaf_path)
    if not entries:
        raise ValueError("No time-aligned annotations found in this .eaf file")

    session_dir = data_dir / "audio" / session_id
    export_clips(entries, audio_path, session_dir, data_dir, force)
    merged = merge_entries(data_dir, session_id, entries)

    return {
        "session_id": session_id,
        "entries_count": len(entries),
        "total_entries_count": len(merged),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eaf_file", type=Path)
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory containing audio/ and data/ (created if missing)")
    parser.add_argument("--session-id", help="Defaults to the .eaf file's stem")
    parser.add_argument("--force", action="store_true", help="Re-export clips even if they already exist")
    args = parser.parse_args()

    if not args.eaf_file.exists():
        sys.exit(f"EAF file not found: {args.eaf_file}")
    if not args.audio_file.exists():
        sys.exit(f"Audio file not found: {args.audio_file}")

    summary = run(args.eaf_file, args.audio_file, args.data_dir, args.session_id, args.force)
    print(f"Session '{summary['session_id']}': {summary['entries_count']} segment(s) "
          f"({summary['total_entries_count']} total across all sessions).")


if __name__ == "__main__":
    main()
