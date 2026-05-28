#!/usr/bin/env python3
"""
uruvagam — lipsync_poc.py
--------------------------
Run a one-slide Wav2Lip-style lip-sync proof of concept.
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import soundfile as sf
import torch
from lipsync import LipSync


def main():
    parser = argparse.ArgumentParser(description="Run one-slide lip-sync POC")
    parser.add_argument("input", help="Path to content.json or its parent output directory")
    parser.add_argument("--slide", type=int, default=1, help="1-based slide number (default: 1)")
    parser.add_argument("--face-ref", default="assets/face_reference.mp4", help="Source face video")
    parser.add_argument("--checkpoint", default="weights/checkpoints/wav2lip.pth", help="Wav2Lip checkpoint")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"], help="LipSync device")
    parser.add_argument("--audio-dir", default=None, help="Audio directory (default: RUN_DIR/audio)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: RUN_DIR/video)")
    parser.add_argument("--batch-size", type=int, default=8, help="Wav2Lip batch size")
    args = parser.parse_args()

    content_path = _resolve_content_path(args.input)
    run_dir = content_path.parent
    content = json.loads(content_path.read_text(encoding="utf-8"))
    slides = content.get("slides", [])
    if args.slide < 1 or args.slide > len(slides):
        raise SystemExit(f"slide must be between 1 and {len(slides)}")

    audio_dir = Path(args.audio_dir) if args.audio_dir else run_dir / "audio"
    audio_path = audio_dir / f"slide_{args.slide:02d}.wav"
    face_path = Path(args.face_ref)
    checkpoint_path = _normalise_checkpoint(Path(args.checkpoint))
    output_dir = Path(args.output_dir) if args.output_dir else run_dir / "video"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = run_dir / "cache" / "lipsync"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        raise SystemExit(f"audio file not found: {audio_path}")
    if not face_path.exists():
        raise SystemExit(f"face reference not found: {face_path}")
    if not checkpoint_path.exists():
        raise SystemExit(f"checkpoint not found: {checkpoint_path}")

    output_path = output_dir / f"slide_{args.slide:02d}_lipsync.mp4"
    report_path = output_dir / f"slide_{args.slide:02d}_lipsync_report.json"

    started = time.time()
    lip = LipSync(
        model="wav2lip",
        checkpoint_path=str(checkpoint_path),
        nosmooth=True,
        device=args.device,
        cache_dir=str(cache_dir),
        img_size=96,
        save_cache=True,
        wav2lip_batch_size=args.batch_size,
        ffmpeg_loglevel="error",
    )
    lip.sync(str(face_path), str(audio_path), str(output_path))
    elapsed = time.time() - started

    audio_duration = _audio_duration(audio_path)
    video_duration = _probe_duration(output_path)
    report = {
        "slide": args.slide,
        "title": slides[args.slide - 1].get("title", ""),
        "face_path": str(face_path),
        "audio_path": str(audio_path),
        "audio_dir": str(audio_dir),
        "checkpoint_path": str(checkpoint_path),
        "output_path": str(output_path),
        "device": args.device,
        "batch_size": args.batch_size,
        "elapsed_seconds": round(elapsed, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "video_duration_seconds": round(video_duration, 3),
        "duration_delta_seconds": round(abs(video_duration - audio_duration), 3),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"video:  {output_path}")
    print(f"report: {report_path}")
    print(
        f"duration: audio={audio_duration:.3f}s video={video_duration:.3f}s "
        f"delta={abs(video_duration - audio_duration):.3f}s elapsed={elapsed:.1f}s"
    )


def _resolve_content_path(value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        path = path / "content.json"
    if not path.exists():
        raise SystemExit(f"content file not found: {path}")
    return path


def _audio_duration(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _normalise_checkpoint(path: Path) -> Path:
    """Convert original Wav2Lip checkpoints into the bare state_dict expected by lipsync."""
    normalised = path.with_name(f"{path.stem}_state_dict{path.suffix}")
    if normalised.exists():
        return normalised

    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict) or "state_dict" not in checkpoint:
        return path

    state_dict = checkpoint["state_dict"]
    converted = {}
    for key, value in state_dict.items():
        converted[key.removeprefix("module.")] = value
    torch.save(converted, normalised)
    return normalised


if __name__ == "__main__":
    main()
