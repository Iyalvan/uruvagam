#!/usr/bin/env python3
"""
uruvagam - video.py
-------------------
Config-driven video backend runner for lip-sync and talking-head experiments.

The default backend wraps the existing Wav2Lip POC. Heavier backends can be
called through an external command, usually from a separate venv, conda env,
Docker image, or CUDA machine.
"""

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import soundfile as sf
import yaml


DEFAULT_VIDEO_CONFIG: dict[str, Any] = {
    "default_backend": "wav2lip",
    "audio_dir": "audio_qwen3tts",
    "output_root": "video_eval",
    "face_ref": "assets/face_reference.mp4",
    "backends": {
        "wav2lip": {
            "type": "wav2lip",
            "checkpoint": "weights/checkpoints/wav2lip.pth",
            "device": "cpu",
            "batch_size": 4,
        }
    },
}


def main():
    parser = argparse.ArgumentParser(description="Run a configured video backend")
    parser.add_argument("input", nargs="?", help="Path to content.json or its parent output directory")
    parser.add_argument("--config", default="config.yaml", help="Project config file")
    parser.add_argument("--backend", default=None, help="Backend name from config, or 'external'")
    parser.add_argument("--backend-name", default=None, help="Report/output label for ad-hoc external backends")
    parser.add_argument("--slide", type=int, default=None, help="1-based slide number")
    parser.add_argument("--all", action="store_true", help="Run every slide in content.json")
    parser.add_argument("--audio-dir", default=None, help="Audio directory. Relative paths resolve inside RUN_DIR")
    parser.add_argument("--face-ref", default=None, help="Face reference image/video")
    parser.add_argument("--output-root", default=None, help="Evaluation output root. Default: RUN_DIR/video_eval")
    parser.add_argument("--output-dir", default=None, help="Exact output directory. Overrides --output-root")
    parser.add_argument("--external-command", default=None, help="Command template for external backends")
    parser.add_argument("--external-cwd", default=None, help="Working directory for external backend command")
    parser.add_argument("--dry-run", action="store_true", help="Print planned command(s) without running")
    parser.add_argument("--list-backends", action="store_true", help="List configured backends and exit")
    args = parser.parse_args()

    config = _load_config(Path(args.config))
    video_config = {**DEFAULT_VIDEO_CONFIG, **config.get("video", {})}
    backends = {
        **DEFAULT_VIDEO_CONFIG["backends"],
        **video_config.get("backends", {}),
    }

    if args.list_backends:
        _print_backends(backends)
        return

    if not args.input:
        raise SystemExit("input is required unless --list-backends is used")

    content_path = _resolve_content_path(args.input)
    run_dir = content_path.parent
    content = json.loads(content_path.read_text(encoding="utf-8"))
    slides = content.get("slides", [])

    backend_key = args.backend or video_config.get("default_backend", "wav2lip")
    backend_config = backends.get(backend_key)
    if backend_config is None:
        if backend_key != "external":
            known = ", ".join(sorted(backends))
            raise SystemExit(f"unknown video backend '{backend_key}'. Known backends: {known}")
        backend_config = {"type": "external"}

    backend_type = backend_config.get("type", backend_key)
    backend_label = args.backend_name or backend_key
    slide_numbers = _select_slides(args.slide, args.all, len(slides))

    audio_dir = _resolve_run_path(
        args.audio_dir or backend_config.get("audio_dir") or video_config.get("audio_dir", "audio"),
        run_dir,
    )
    face_path = _resolve_face_path(args.face_ref or backend_config.get("face_ref") or video_config["face_ref"], run_dir)
    output_dir = _resolve_output_dir(args.output_dir, args.output_root, video_config, backend_label, run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not face_path.exists():
        raise SystemExit(f"face reference not found: {face_path}")

    reports = []
    for slide_number in slide_numbers:
        title = slides[slide_number - 1].get("title", "")
        audio_path = audio_dir / f"slide_{slide_number:02d}.wav"
        output_path = output_dir / f"slide_{slide_number:02d}_lipsync.mp4"
        report_path = output_dir / f"slide_{slide_number:02d}_video_report.json"

        if not audio_path.exists():
            raise SystemExit(f"audio file not found: {audio_path}")

        print(f"[{slide_number:02d}] {backend_label} - {title}", flush=True)
        started = time.time()
        command = _run_backend(
            backend_type=backend_type,
            backend_config=backend_config,
            backend_label=backend_label,
            content_path=content_path,
            run_dir=run_dir,
            slide_number=slide_number,
            face_path=face_path,
            audio_path=audio_path,
            output_path=output_path,
            output_dir=output_dir,
            external_command=args.external_command,
            external_cwd=args.external_cwd,
            dry_run=args.dry_run,
        )
        elapsed = time.time() - started

        if args.dry_run:
            continue

        if not output_path.exists():
            raise SystemExit(f"backend did not create expected output: {output_path}")

        report = _build_report(
            backend_label=backend_label,
            backend_type=backend_type,
            slide_number=slide_number,
            title=title,
            face_path=face_path,
            audio_path=audio_path,
            output_path=output_path,
            command=command,
            elapsed=elapsed,
        )
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        reports.append(report)
        print(
            f"     video: {output_path}\n"
            f"     delta: {report['duration_delta_seconds']:.3f}s elapsed: {elapsed:.1f}s"
        )

    if reports:
        summary_path = output_dir / "video_eval_report.json"
        summary_path.write_text(
            json.dumps(
                {
                    "backend": backend_label,
                    "backend_type": backend_type,
                    "run_dir": str(run_dir),
                    "audio_dir": str(audio_dir),
                    "face_path": str(face_path),
                    "output_dir": str(output_dir),
                    "slides": reports,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"summary: {summary_path}")


def _load_config(path: Path) -> dict[str, Any]:
    config = _read_yaml(path)
    if path.name == "config.yaml":
        local_path = path.with_name("config.local.yaml")
        if local_path.exists():
            config = _deep_merge(config, _read_yaml(local_path))
    return config


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a mapping: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _print_backends(backends: dict[str, dict[str, Any]]):
    print("Configured video backends:")
    for name in sorted(backends):
        backend = backends[name]
        backend_type = backend.get("type", name)
        runtime = backend.get("runtime", "local")
        command_status = ""
        if backend_type == "external":
            command_status = " command=configured" if backend.get("command") else " command=missing"
        print(f"  {name}: type={backend_type} runtime={runtime}{command_status}")
    print("")
    print("Ad-hoc external backend:")
    print("  --backend external --backend-name NAME --external-command 'COMMAND ... {face} {audio} {output}'")


def _resolve_content_path(value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        path = path / "content.json"
    if not path.exists():
        raise SystemExit(f"content file not found: {path}")
    return path


def _select_slides(slide: int | None, run_all: bool, slide_count: int) -> list[int]:
    if run_all:
        return list(range(1, slide_count + 1))
    selected = slide or 1
    if selected < 1 or selected > slide_count:
        raise SystemExit(f"slide must be between 1 and {slide_count}")
    return [selected]


def _resolve_run_path(value: str | Path, run_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "outputs":
        return path
    return run_dir / path


def _resolve_face_path(value: str | Path, run_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    run_path = run_dir / path
    if run_path.exists():
        return run_path
    return path


def _resolve_output_dir(
    output_dir: str | None,
    output_root: str | None,
    video_config: dict[str, Any],
    backend_label: str,
    run_dir: Path,
) -> Path:
    if output_dir:
        return _resolve_run_path(output_dir, run_dir)
    root = _resolve_run_path(output_root or video_config.get("output_root", "video_eval"), run_dir)
    return root / backend_label


def _run_backend(
    *,
    backend_type: str,
    backend_config: dict[str, Any],
    backend_label: str,
    content_path: Path,
    run_dir: Path,
    slide_number: int,
    face_path: Path,
    audio_path: Path,
    output_path: Path,
    output_dir: Path,
    external_command: str | None,
    external_cwd: str | None,
    dry_run: bool,
) -> list[str]:
    if backend_type == "wav2lip":
        return _run_wav2lip(
            backend_config=backend_config,
            content_path=content_path,
            run_dir=run_dir,
            slide_number=slide_number,
            face_path=face_path,
            audio_path=audio_path,
            output_dir=output_dir,
            dry_run=dry_run,
        )
    if backend_type == "external":
        return _run_external(
            backend_config=backend_config,
            backend_label=backend_label,
            content_path=content_path,
            run_dir=run_dir,
            slide_number=slide_number,
            face_path=face_path,
            audio_path=audio_path,
            output_path=output_path,
            output_dir=output_dir,
            external_command=external_command,
            external_cwd=external_cwd,
            dry_run=dry_run,
        )
    raise SystemExit(f"unsupported video backend type: {backend_type}")


def _run_wav2lip(
    *,
    backend_config: dict[str, Any],
    content_path: Path,
    run_dir: Path,
    slide_number: int,
    face_path: Path,
    audio_path: Path,
    output_dir: Path,
    dry_run: bool,
) -> list[str]:
    command = [
        sys.executable,
        "lipsync_poc.py",
        str(content_path),
        "--slide",
        str(slide_number),
        "--face-ref",
        str(face_path),
        "--audio-dir",
        str(audio_path.parent),
        "--output-dir",
        str(output_dir),
        "--checkpoint",
        str(backend_config.get("checkpoint", "weights/checkpoints/wav2lip.pth")),
        "--device",
        str(backend_config.get("device", "cpu")),
        "--batch-size",
        str(backend_config.get("batch_size", 4)),
    ]
    return _run_command(command, cwd=run_dir.parent.parent if run_dir.parts[:1] == ("outputs",) else Path.cwd(), dry_run=dry_run)


def _run_external(
    *,
    backend_config: dict[str, Any],
    backend_label: str,
    content_path: Path,
    run_dir: Path,
    slide_number: int,
    face_path: Path,
    audio_path: Path,
    output_path: Path,
    output_dir: Path,
    external_command: str | None,
    external_cwd: str | None,
    dry_run: bool,
) -> list[str]:
    template = external_command or backend_config.get("command")
    if not template:
        raise SystemExit(
            f"external backend '{backend_label}' needs --external-command or video.backends.{backend_label}.command"
        )

    values = {
        "python": sys.executable,
        "slide": slide_number,
        "slide_padded": f"{slide_number:02d}",
        "content": str(content_path),
        "run_dir": str(run_dir),
        "face": str(face_path),
        "audio": str(audio_path),
        "output": str(output_path),
        "output_dir": str(output_dir),
        "project_dir": str(Path.cwd()),
    }
    if isinstance(template, list):
        command = [str(part).format(**values) for part in template]
    else:
        command = shlex.split(str(template).format(**values))

    cwd_template = external_cwd or backend_config.get("cwd") or ""
    cwd = Path(str(cwd_template).format(**values)) if cwd_template else Path.cwd()
    return _run_command(command, cwd=cwd, dry_run=dry_run)


def _run_command(command: list[str], cwd: Path, dry_run: bool) -> list[str]:
    print("     command:", shlex.join(command), flush=True)
    if dry_run:
        return command
    subprocess.run(command, cwd=str(cwd), check=True)
    return command


def _build_report(
    *,
    backend_label: str,
    backend_type: str,
    slide_number: int,
    title: str,
    face_path: Path,
    audio_path: Path,
    output_path: Path,
    command: list[str],
    elapsed: float,
) -> dict[str, Any]:
    audio_duration = _audio_duration(audio_path)
    video_duration = _probe_duration(output_path)
    return {
        "schema_version": 1,
        "backend": backend_label,
        "backend_type": backend_type,
        "slide": slide_number,
        "title": title,
        "face_path": str(face_path),
        "audio_path": str(audio_path),
        "output_path": str(output_path),
        "command": command,
        "elapsed_seconds": round(elapsed, 3),
        "audio_duration_seconds": round(audio_duration, 3),
        "video_duration_seconds": round(video_duration, 3),
        "duration_delta_seconds": round(abs(video_duration - audio_duration), 3),
    }


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


if __name__ == "__main__":
    main()
