#!/usr/bin/env python3
"""
uruvagam — generate.py
-----------------------
CLI entry point. Generates training content + slides from a topic in one command.

Usage examples:
  python generate.py "Introduction to Apache Kafka"
  python generate.py "Kubernetes Observability with Prometheus" --duration 15
  python generate.py "AWS Cost Optimization Strategies" --provider claude
  python generate.py "SRE Incident Response Playbook" --audience "all engineers" --html
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
    from rich import print as rprint
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

console = Console() if HAS_RICH else None


def log(msg: str, style: str = ""):
    if HAS_RICH:
        console.print(msg, style=style)
    else:
        print(msg)


def main():
    parser = argparse.ArgumentParser(
        description="uruvagam — AI-powered training content generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # changed: topic is optional when --source is provided
    parser.add_argument("topic", nargs="?", default=None,
                        help="Training topic (quoted string) — or use --source")
    parser.add_argument("--source", default=None,
                        help="Path to raw notes file to restructure (mutually exclusive with topic)")
    parser.add_argument("--title", default=None,
                        help="Presentation title (required when using --source)")
    parser.add_argument("--duration", "-d", type=int, default=10,
                        help="Target duration in minutes (default: 10)")
    parser.add_argument("--audience", "-a", default="engineering team",
                        help="Target audience description")
    # changed: default provider is local oMLX HTTP server
    parser.add_argument("--provider", "-p", choices=["omlx", "ollama", "claude"],
                        default="omlx", help="LLM provider (default: omlx)")
    parser.add_argument("--model", "-m", default=None,
                        help="Model override (default: config.yaml value)")
    parser.add_argument("--api-key", default=None,
                        help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    parser.add_argument("--output-dir", "-o", default="outputs",
                        help="Output base directory (default: outputs/)")
    parser.add_argument("--no-pptx", action="store_true",
                        help="Skip PPTX generation")
    parser.add_argument("--no-html", action="store_true",
                        help="Skip HTML preview generation")
    parser.add_argument("--config", default="config.yaml",
                        help="Config file path (default: config.yaml)")
    parser.add_argument("--theme", default=None,
                        help="Presentation theme name or YAML path")
    parser.add_argument("--template", default=None,
                        help="Optional PPTX template path")
    parser.add_argument("--logo", default=None,
                        help="Optional logo image path for PPTX/HTML")
    # added: agent controls
    parser.add_argument("--no-agents", action="store_true",
                        help="Skip all quality agents (ContentCritic, SpeakerStyle, DurationBudget)")
    parser.add_argument("--speaker-style", default="assets/speaker_style.txt",
                        help="Presenter speaking style examples for SpeakerStyleAgent (default: assets/speaker_style.txt)")
    # added: optional per-run guidance appended to the content prompt (falls back to config content_instructions)
    parser.add_argument("--instructions", default=None,
                        help="Extra content guidance appended to the prompt (e.g. TTS spoken-form rules)")
    args = parser.parse_args()

    # ── Resolve topic vs source mode ───────────────────────────────────────────
    # added: --source restructures raw notes; --title becomes the topic in that mode
    source_content = None
    if args.source:
        src_path = Path(args.source)
        if not src_path.exists():
            parser.error(f"--source file not found: {src_path}")
        source_content = src_path.read_text(encoding="utf-8")
        if not args.title:
            parser.error("--title is required when using --source")
        topic = args.title
    elif args.topic:
        topic = args.topic
    else:
        parser.error("provide a topic or use --source FILE --title TITLE")

    # ── Load config ────────────────────────────────────────────────────────────
    cfg = _load_config(args.config)
    # changed: oMLX HTTP server defaults; key from env, never config
    provider = args.provider or cfg.get("provider", "omlx")
    model = args.model or cfg.get("model", "Qwen3.6-35B-A3B-4bit")
    presentation_cfg = cfg.get("presentation", {}) or {}
    theme = args.theme or presentation_cfg.get("theme", "dark_corporate")
    template_path = args.template or presentation_cfg.get("template")
    logo_path = args.logo or presentation_cfg.get("logo")
    # added: extra content guidance — CLI flag wins, else config content_instructions, else none
    extra_instructions = args.instructions or cfg.get("content_instructions")
    omlx_url = cfg.get("omlx_base_url", "http://127.0.0.1:8000")
    omlx_api_key = os.environ.get("OMLX_API_KEY")
    ollama_url = cfg.get("ollama_base_url", "http://localhost:11434")
    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY") or cfg.get("claude_api_key", "")

    # ── Setup output dir ───────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = topic.replace(" ", "_").lower()
    safe = "".join(c for c in safe if c.isalnum() or c == "_")[:30]
    run_name = f"{safe}_{ts}"
    out_dir = Path(args.output_dir) / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Header ─────────────────────────────────────────────────────────────────
    mode_label = "Source" if source_content else "Topic"
    if HAS_RICH:
        console.print(Panel.fit(
            f"[bold cyan]uruvagam[/bold cyan] — AI Training Content Generator\n"
            f"[dim]{mode_label}:[/dim]    {topic}\n"
            f"[dim]Provider:[/dim] {provider} / {model}\n"
            f"[dim]Output:[/dim]   {out_dir}",
            border_style="cyan"
        ))
    else:
        print(f"\n=== uruvagam ===")
        print(f"{mode_label}:    {topic}")
        print(f"Provider: {provider} / {model}")
        print(f"Output:   {out_dir}\n")

    t0 = time.time()

    # ── Step 1: Generate content ───────────────────────────────────────────────
    _step("Generating content with LLM...")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        # changed: modules live at repo root, not under src/
        from content_gen import generate_content

        content = generate_content(
            topic=topic,
            duration_minutes=args.duration,
            audience=args.audience,
            provider=provider,
            model=model,
            omlx_base_url=omlx_url,
            omlx_api_key=omlx_api_key,
            ollama_base_url=ollama_url,
            api_key=api_key,
            source_content=source_content,
            extra_instructions=extra_instructions,
        )
        content["_audience"] = args.audience

    except Exception as e:
        log(f"[red]✗ Content generation failed: {e}[/red]" if HAS_RICH else f"ERROR: {e}")
        sys.exit(1)

    # Save initial JSON
    json_path = out_dir / "content.json"
    with open(json_path, "w") as f:
        json.dump(content, f, indent=2, ensure_ascii=False)
    _ok(f"Content JSON saved → {json_path}")

    # ── Step 1b: Quality agents ────────────────────────────────────────────────
    # agents run for local providers (omlx, ollama); skipped for claude or with --no-agents
    if not args.no_agents and provider in ("omlx", "ollama"):
        _step("Running quality agents...")
        try:
            from agents import run_content_critic, run_speaker_style, run_duration_budget

            llm_config = {
                "provider": provider,
                "base_url": ollama_url if provider == "ollama" else omlx_url,
                "model": model,
                "api_key": omlx_api_key,  # only used when provider == "omlx"
            }

            # 1. evaluate (returns report; does not alter content)
            content, quality_report = run_content_critic(content, args.audience, llm_config)
            report_path = out_dir / "quality_report.json"
            with open(report_path, "w") as f:
                json.dump(quality_report, f, indent=2)
            _ok(f"Quality report → {report_path}")

            # 2. style rewrite (rewrites speaker_notes to match presenter voice)
            content = run_speaker_style(content, args.speaker_style, llm_config)

            # 3. duration budget (condense over-budget slides for cleaner TTS chunking)
            content = run_duration_budget(content, llm_config)

            # resave content.json with agent-improved notes
            with open(json_path, "w") as f:
                json.dump(content, f, indent=2, ensure_ascii=False)
            _ok("Content JSON updated with agent improvements")

        except Exception as e:
            log(f"[yellow]⚠ Agents failed: {e} — continuing with original content[/yellow]"
                if HAS_RICH else f"WARNING Agents: {e} — continuing with original content")
    elif args.no_agents:
        _step("Agents skipped (--no-agents)")

    # Save narration script
    script_path = out_dir / "narration_script.txt"
    _write_script(content, script_path)
    _ok(f"Narration script → {script_path}")

    # ── Step 2: PPTX ──────────────────────────────────────────────────────────
    if not args.no_pptx:
        _step("Generating PPTX slides...")
        try:
            # changed: modules live at repo root, not under src/
            from slide_gen import generate_pptx
            pptx_path = str(out_dir / f"{safe}.pptx")
            generate_pptx(
                content,
                pptx_path,
                theme=theme,
                template_path=template_path,
                logo_path=logo_path,
            )
            _ok(f"Slides (PPTX) → {pptx_path}")
        except Exception as e:
            log(f"[yellow]⚠ PPTX failed: {e}[/yellow]" if HAS_RICH else f"WARNING PPTX: {e}")

    # ── Step 3: HTML preview ───────────────────────────────────────────────────
    if not args.no_html:
        _step("Generating HTML preview...")
        try:
            # changed: modules live at repo root, not under src/
            from html_gen import generate_html
            html_path = str(out_dir / "preview.html")
            generate_html(
                content,
                html_path,
                output_dir=str(out_dir),
                theme=theme,
                logo_path=logo_path,
            )
            _ok(f"HTML viewer → {html_path}")
        except Exception as e:
            log(f"[yellow]⚠ HTML failed: {e}[/yellow]" if HAS_RICH else f"WARNING HTML: {e}")

    # ── Summary ────────────────────────────────────────────────────────────────
    elapsed = time.time() - t0
    slides_count = len(content.get("slides", []))

    if HAS_RICH:
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("[dim]Title[/dim]", content.get("title", ""))
        t.add_row("[dim]Slides[/dim]", str(slides_count))
        t.add_row("[dim]Duration[/dim]", f"{content.get('duration_minutes', args.duration)} min")
        t.add_row("[dim]Time taken[/dim]", f"{elapsed:.1f}s")
        console.print(Panel(t, title="[bold green]✓ Done[/bold green]", border_style="green"))
        console.print(f"\n[bold]Open preview:[/bold]  open {out_dir}/preview.html")
        console.print(f"[bold]Open slides:[/bold]   open {out_dir}/{safe}.pptx")
        console.print(f"\n[dim]Next: run  python tts.py {out_dir}/  to generate voice audio[/dim]\n")
    else:
        print(f"\n✓ Done in {elapsed:.1f}s")
        print(f"  Slides: {slides_count}")
        print(f"  open {out_dir}/preview.html")
        print(f"  open {out_dir}/{safe}.pptx")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_config(path: str) -> dict:
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _write_script(content: dict, path: Path):
    lines = [
        f"TITLE: {content.get('title', '')}",
        "=" * 60,
        "",
        "LEARNING OBJECTIVES:",
    ]
    for obj in content.get("objectives", []):
        lines.append(f"  • {obj}")
    lines += ["", "=" * 60, "", "FULL NARRATION SCRIPT:", ""]
    lines.append(content.get("full_script", ""))
    lines += ["", "=" * 60, "", "SLIDE-BY-SLIDE NOTES:", ""]
    for i, slide in enumerate(content.get("slides", []), 1):
        lines.append(f"[Slide {i}] {slide.get('title', '')}")
        notes = slide.get("speaker_notes", "").strip()
        if notes:
            lines.append(notes)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _step(msg: str):
    if HAS_RICH:
        console.print(f"[cyan]⟳[/cyan] {msg}")
    else:
        print(f"  ... {msg}")


def _ok(msg: str):
    if HAS_RICH:
        console.print(f"[green]✓[/green] {msg}")
    else:
        print(f"  ✓ {msg}")


if __name__ == "__main__":
    main()
