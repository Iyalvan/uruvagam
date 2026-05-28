#!/usr/bin/env python3
"""
uruvagam verify.py
--------------------
Checks a completed output directory for expected artefacts and quality signals.

Usage:
  python verify.py outputs/my_run/
  python verify.py outputs/my_run/ --stage audio   # check only audio
  python verify.py outputs/my_run/ --stage video
  python verify.py outputs/my_run/ --stage video --video-dir video_eval/wav2lip
"""

import json
import sys
from pathlib import Path


WORDS_PER_SECOND = 0.4
BUDGET_THRESHOLD_WORDS = 90   # must match agents.py


def verify(output_dir: str, stage: str = "all", audio_dir_name: str = "audio", video_dir_name: str = "video") -> bool:
    base = Path(output_dir)
    if not base.exists():
        print(f"ERROR: {base} does not exist")
        return False

    all_ok = True
    results = []

    def check(label: str, ok: bool, detail: str = "") -> bool:
        nonlocal all_ok
        if not ok:
            all_ok = False
        results.append((ok, label, detail))
        return ok

    def info(label: str, detail: str = ""):
        results.append((None, label, detail))

    # ── content ───────────────────────────────────────────────────────────────
    if stage in ("all", "content"):
        content_path = base / "content.json"
        if check("content.json exists", content_path.exists()):
            content = json.loads(content_path.read_text(encoding="utf-8"))
            check("title present", bool(content.get("title")), content.get("title", ""))
            objectives = content.get("objectives", [])
            check("objectives present", len(objectives) >= 2, f"{len(objectives)} found")
            slides = content.get("slides", [])
            check("slide count >= 6", len(slides) >= 6, f"{len(slides)} slides")

            empty_notes = [i + 1 for i, s in enumerate(slides) if not s.get("speaker_notes", "").strip()]
            check("all slides have speaker_notes", not empty_notes,
                  f"missing on slide(s): {empty_notes}" if empty_notes else "")

            over_budget = [
                (i + 1, len(s.get("speaker_notes", "").split()))
                for i, s in enumerate(slides)
                if len(s.get("speaker_notes", "").split()) > BUDGET_THRESHOLD_WORDS
            ]
            check(
                f"all speaker_notes <= {BUDGET_THRESHOLD_WORDS} words",
                not over_budget,
                f"slide(s) over budget: {over_budget}" if over_budget else "",
            )

            check("PPTX file exists", bool(list(base.glob("*.pptx"))))
            check("preview.html exists", (base / "preview.html").exists())

            quality_path = base / "quality_report.json"
            if quality_path.exists():
                report = json.loads(quality_path.read_text(encoding="utf-8"))
                score = report.get("score", -1)
                n_issues = len(report.get("issues", []))
                passed = report.get("pass", False)
                check("critic score >= 7", passed, f"score={score}/10  issues={n_issues}")
            else:
                info("quality_report.json", "not present (agents not run or --no-agents used)")

    # ── audio ─────────────────────────────────────────────────────────────────
    if stage in ("all", "audio"):
        audio_dir = _resolve_child_dir(base, audio_dir_name)
        if check(f"{audio_dir_name}/ directory exists", audio_dir.exists()):
            wav_files = sorted(audio_dir.glob("slide_*.wav"))
            check("at least 2 audio files", len(wav_files) >= 2, f"{len(wav_files)} found")

            try:
                import soundfile as sf
                short_files = []
                for wav in wav_files:
                    try:
                        dur = sf.info(str(wav)).duration
                        if dur < 5.0:
                            short_files.append((wav.name, round(dur, 2)))
                    except Exception:
                        short_files.append((wav.name, "unreadable"))
                check("all audio files > 5s", not short_files,
                      str(short_files) if short_files else "")
                if not short_files and wav_files:
                    total_dur = sum(sf.info(str(w)).duration for w in wav_files)
                    info("total audio duration", f"{total_dur:.1f}s  ({total_dur/60:.1f} min)")
            except ImportError:
                info("soundfile not available", "skipping duration check")

    # ── video ─────────────────────────────────────────────────────────────────
    if stage in ("all", "video"):
        video_dir = _resolve_child_dir(base, video_dir_name)
        if video_dir.exists():
            mp4_files = sorted(video_dir.glob("*.mp4"))
            info(f"{video_dir_name}/ files", f"{len(mp4_files)} MP4(s): {[f.name for f in mp4_files]}")

            reports = sorted(video_dir.glob("*_lipsync_report.json")) + sorted(video_dir.glob("*_video_report.json"))
            for rpt in reports:
                try:
                    data = json.loads(rpt.read_text(encoding="utf-8"))
                    delta = data.get("duration_delta_seconds", "?")
                    check(
                        f"A/V delta == 0 ({rpt.stem})",
                        abs(float(delta)) < 0.1 if delta != "?" else False,
                        f"delta={delta}s",
                    )
                except Exception:
                    pass
        else:
            info(f"{video_dir_name}/", "not yet generated")

    # ── print summary ─────────────────────────────────────────────────────────
    print(f"\nVerification: {base}\n")
    for ok, label, detail in results:
        if ok is None:
            tag = " INFO "
        elif ok:
            tag = " PASS "
        else:
            tag = " FAIL "
        suffix = f"  {detail}" if detail else ""
        print(f"  [{tag}] {label}{suffix}")

    print()
    if all_ok:
        print("All checks passed.")
    else:
        print("Some checks FAILED — review output above.")
    print()

    return all_ok


def _resolve_child_dir(base: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == base.name:
        return path
    if path.parts and path.parts[0] == "outputs":
        return path
    return base / path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="uruvagam output verifier")
    parser.add_argument("output_dir", help="Path to a uruvagam output directory")
    parser.add_argument(
        "--stage",
        choices=["all", "content", "audio", "video"],
        default="all",
        help="Which stage to check (default: all)",
    )
    parser.add_argument("--audio-dir", default="audio", help="Audio directory to check, relative to output_dir")
    parser.add_argument("--video-dir", default="video", help="Video directory to check, relative to output_dir")
    args = parser.parse_args()
    success = verify(args.output_dir, args.stage, args.audio_dir, args.video_dir)
    sys.exit(0 if success else 1)
