"""
uruvagam.html_gen
~~~~~~~~~~~~~~~~~~
Generates a self-contained themed HTML slide presentation from content JSON.
- Full keyboard navigation (arrow keys / space)
- Speaker notes panel (toggle with 'N')
- Full-screen support (F)
- Presenter PiP video (auto-advances on video end when videos are present)
- Dark theme matching the PPTX
- Zero external dependencies — single file, works offline

CLI regen:  python html_gen.py <output_dir>
  Re-generates preview.html from content.json, picking up any new video files.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from theme_config import DEFAULT_THEME_NAME, css_vars, load_theme

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
{theme_css}
  }}

  body {{
    background: var(--deck-bg);
    font-family: var(--font);
    height: 100vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    color: var(--white);
  }}

  /* ── Deck container ──────────────────────────────── */
  #deck {{
    position: relative;
    width: 100%;
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
  }}

  .slide {{
    display: none;
    position: relative;
    width: 100%;
    max-width: min(100%, calc((100vh - 120px) * 16 / 9));
    aspect-ratio: 16 / 9;
    background: var(--navy);
    border-radius: 6px;
    overflow: hidden;
    box-shadow: var(--slide-shadow);
  }}
  .slide.active {{ display: flex; flex-direction: column; }}

  /* ── Left accent bar ─────────────────────────────── */
  .slide::before {{
    content: '';
    position: absolute;
    left: 0; top: 0;
    width: 1.1%;
    height: 100%;
    background: var(--cyan);
    z-index: 2;
  }}

  /* ── Header band ─────────────────────────────────── */
  .slide-header {{
    background: var(--surface);
    padding: 1.4% 2.5% 1.2% 3%;
    border-bottom: 2px solid var(--cyan2);
    min-height: 14%;
    display: flex;
    align-items: center;
    z-index: 1;
  }}
  .slide-header h2 {{
    font-size: clamp(14px, 2.8vw, 30px);
    font-weight: 600;
    color: var(--cyan);
    line-height: 1.2;
  }}

  /* ── Title slide ─────────────────────────────────── */
  .slide-title-content {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 4% 5% 4% 5.5%;
  }}
  .slide-title-content h1 {{
    font-size: clamp(18px, 4vw, 46px);
    font-weight: 700;
    color: var(--white);
    line-height: 1.2;
    margin-bottom: 3%;
  }}
  .slide-title-content .meta {{
    font-size: clamp(11px, 1.8vw, 20px);
    color: var(--cyan);
    margin-bottom: 1.5%;
  }}
  .slide-title-content .tagline {{
    font-size: clamp(9px, 1.3vw, 14px);
    color: var(--muted);
    margin-top: 2%;
  }}
  .title-rule {{
    height: 2px;
    width: 60%;
    background: var(--cyan2);
    margin: 2% 0;
  }}

  /* ── Bullet content ──────────────────────────────── */
  .slide-body {{
    flex: 1;
    padding: 1.5% 3% 1.5% 3%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 0.6%;
    overflow: hidden;
  }}
  /* two-column layout when diagram present */
  .slide-body.has-diagram {{
    flex-direction: row;
    align-items: stretch;
    gap: 1.5%;
    padding: 1.2% 2% 1.2% 2.5%;
  }}
  .bullets-col {{
    flex: 0 0 54%;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 0.5%;
  }}
  .diagram-col {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-width: 0;
  }}
  .slide-subtitle {{
    font-size: clamp(9px, 1.4vw, 16px);
    color: var(--muted);
    margin-bottom: 0.8%;
    padding-left: 1%;
  }}
  .bullet-row {{
    display: flex;
    align-items: center;
    background: var(--num-bg);
    border-radius: 4px;
    padding: 1% 1.5%;
    gap: 3%;
    min-height: 12%;
  }}
  .bullet-row:nth-child(even) {{
    background: var(--row-even);
  }}
  .bullet-num {{
    font-size: clamp(10px, 1.5vw, 17px);
    font-weight: 700;
    color: var(--cyan);
    min-width: 3%;
    text-align: center;
    flex-shrink: 0;
  }}
  .bullet-text {{
    font-size: clamp(11px, 1.9vw, 21px);
    color: var(--offwhite);
    line-height: 1.35;
  }}

  /* ── ASCII diagram panel (right column) ─────────── */
  .diagram-block {{
    background: var(--num-bg);
    border: 1px solid var(--cyan2);
    border-radius: 6px;
    padding: 3% 3%;
    font-family: 'Courier New', Courier, monospace;
    font-size: clamp(7.5px, 1.0vw, 11.5px);
    color: var(--cyan);
    white-space: pre;
    overflow: auto;
    line-height: 1.55;
    height: 100%;
    box-sizing: border-box;
  }}

  /* dense slides: more bullets, smaller text */
  .slide-body.dense .bullet-row {{ min-height: 9%; padding: 0.6% 1.2%; }}
  .slide-body.dense .bullet-text {{ font-size: clamp(9px, 1.55vw, 17px); }}
  .slide-body.dense .bullet-num  {{ font-size: clamp(9px, 1.3vw, 15px); }}

  /* ── Summary slide ───────────────────────────────── */
  .slide-summary .slide-header {{
    background: var(--elevated);
    border-top: 3px solid var(--cyan);
  }}
  .slide-summary .bullet-num {{ color: var(--cyan); }}
  .cta-bar {{
    background: var(--surface);
    padding: 1% 3%;
    font-size: clamp(9px, 1.2vw, 14px);
    color: var(--muted);
    text-align: center;
    margin-top: auto;
  }}

  /* ── Controls bar ────────────────────────────────── */
  #controls {{
    height: 52px;
    background: var(--deck-bg);
    border-top: 1px solid var(--control-border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    gap: 12px;
    flex-shrink: 0;
  }}
  #controls button {{
    background: none;
    border: 1px solid var(--control-border);
    color: var(--muted);
    padding: 5px 14px;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    transition: all .15s;
    font-family: var(--font);
  }}
  #controls button:hover {{ border-color: var(--cyan); color: var(--cyan); }}
  #controls button:disabled {{ opacity: .3; cursor: default; }}
  #slide-counter {{ font-size: 13px; color: var(--muted); min-width: 80px; text-align: center; }}

  #progress-bar {{
    position: absolute;
    bottom: 52px;
    left: 0;
    height: 2px;
    background: var(--cyan);
    transition: width .3s ease;
  }}

  /* ── Speaker notes panel ────────────────────────── */
  #notes-panel {{
    display: none;
    position: fixed;
    bottom: 52px;
    left: 0;
    right: 0;
    height: 200px;
    background: var(--notes-bg);
    border-top: 1px solid var(--cyan2);
    padding: 14px 24px;
    overflow-y: auto;
    z-index: 100;
  }}
  #notes-panel h4 {{
    font-size: 12px;
    color: var(--cyan2);
    text-transform: uppercase;
    letter-spacing: .08em;
    margin-bottom: 8px;
  }}
  #notes-panel p {{
    font-size: 14px;
    color: var(--offwhite);
    line-height: 1.6;
  }}
  #notes-panel.visible {{ display: block; }}

  /* ── Keyboard shortcut hint ─────────────────────── */
  #hint {{
    position: fixed;
    top: 12px;
    right: 16px;
    font-size: 11px;
    color: var(--muted);
  }}

  /* ── Presenter PiP ───────────────────────────────── */
  #pip-wrap {{
    position: absolute;
    bottom: 3%;
    right: 2%;
    width: clamp(72px, 13%, 160px);
    aspect-ratio: 1;
    border-radius: 50%;
    overflow: hidden;
    border: 2.5px solid var(--cyan);
    box-shadow: 0 4px 20px rgba(0,0,0,.7);
    background: var(--pip-bg);
    z-index: 20;
    display: none;
    cursor: pointer;
    transition: opacity .2s;
  }}
  #pip-wrap.active {{ display: block; }}
  #pip-wrap:hover {{ opacity: .85; }}
  #pip-video {{
    width: 100%;
    height: 100%;
    object-fit: cover;
  }}

  .slide-footer {{
    position: absolute;
    left: 2.2%;
    right: 2.2%;
    bottom: 2.2%;
    height: 5.2%;
    display: flex;
    align-items: center;
    gap: 1%;
    color: var(--muted);
    font-size: clamp(7px, .9vw, 11px);
    z-index: 8;
    pointer-events: none;
  }}
  .slide-logo {{
    max-width: clamp(54px, 9vw, 110px);
    max-height: 100%;
    object-fit: contain;
  }}
  .slide-footer-text {{
    line-height: 1;
  }}
</style>
</head>
<body>

<div id="deck">
{slides_html}
  <div id="pip-wrap" title="Click to pause / resume" onclick="togglePip()">
    <video id="pip-video" playsinline></video>
  </div>
  <div id="progress-bar"></div>
</div>

<div id="notes-panel">
  <h4>Speaker Notes</h4>
  <p id="notes-text"></p>
</div>

<div id="controls">
  <button id="btn-prev" onclick="navigate(-1)">← Prev</button>
  <div style="display:flex;align-items:center;gap:12px;">
    <span id="slide-counter">1 / {total}</span>
    <button onclick="toggleNotes()" title="N">Notes</button>
    <button onclick="toggleFullscreen()" title="F">⛶ Fullscreen</button>
  </div>
  <button id="btn-next" onclick="navigate(1)">Next →</button>
</div>

<div id="hint">← → Space · N=notes · F=fullscreen · P=pause video</div>

<script>
const slides = {slides_json};
const slideVideos = {videos_json};
let current = 0;
const total = slides.length;

const pipWrap  = document.getElementById('pip-wrap');
const pipVideo = document.getElementById('pip-video');
let   pipPaused = false;
let   advanceTimer = null;

function showSlide(i) {{
  document.querySelectorAll('.slide').forEach((s,j) => {{
    s.classList.toggle('active', j === i);
  }});
  current = i;
  document.getElementById('slide-counter').textContent = (i+1) + ' / ' + total;
  document.getElementById('btn-prev').disabled = i === 0;
  document.getElementById('btn-next').disabled = i === total - 1;
  document.getElementById('notes-text').textContent = slides[i].speaker_notes || '';
  const pct = total > 1 ? ((i / (total-1)) * 100) : 100;
  document.getElementById('progress-bar').style.width = pct + '%';

  // stop any pending auto-advance
  if (advanceTimer) {{ clearTimeout(advanceTimer); advanceTimer = null; }}

  // PiP: swap video for this slide
  pipPaused = false;
  const vpath = slideVideos[i];
  if (vpath) {{
    pipWrap.classList.add('active');
    pipVideo.src = vpath;
    pipVideo.play().catch(() => {{}});
  }} else {{
    pipWrap.classList.remove('active');
    pipVideo.pause();
    pipVideo.src = '';
  }}
}}

// auto-advance when slide video ends
pipVideo.addEventListener('ended', () => {{
  if (current < total - 1) {{
    advanceTimer = setTimeout(() => navigate(1), 600);
  }}
}});

function navigate(dir) {{
  const next = current + dir;
  if (next >= 0 && next < total) showSlide(next);
}}

function togglePip() {{
  if (pipPaused) {{
    pipVideo.play().catch(() => {{}});
    pipPaused = false;
  }} else {{
    pipVideo.pause();
    pipPaused = true;
  }}
}}

function toggleNotes() {{
  document.getElementById('notes-panel').classList.toggle('visible');
}}

function toggleFullscreen() {{
  if (!document.fullscreenElement) document.documentElement.requestFullscreen();
  else document.exitFullscreen();
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'ArrowRight' || e.key === ' ') navigate(1);
  else if (e.key === 'ArrowLeft') navigate(-1);
  else if (e.key === 'n' || e.key === 'N') toggleNotes();
  else if (e.key === 'f' || e.key === 'F') toggleFullscreen();
  else if (e.key === 'Home') showSlide(0);
  else if (e.key === 'End') showSlide(total - 1);
  else if (e.key === 'p' || e.key === 'P') togglePip();
}});

showSlide(0);
</script>
</body>
</html>
"""

TITLE_SLIDE_HTML = """
  <div class="slide">
    <div class="slide-title-content">
      <h1>{title}</h1>
      <div class="title-rule"></div>
      <div class="meta">{meta}</div>
      <div class="tagline">{tagline}</div>
    </div>
    {footer_html}
  </div>
"""

CONTENT_SLIDE_HTML = """
  <div class="slide {extra_class}">
    <div class="slide-header"><h2>{title}</h2></div>
    <div class="slide-body {body_class}">
      {body_inner}
    </div>
    {cta_html}
    {footer_html}
  </div>
"""


def generate_html(
    content: dict,
    output_path: str,
    output_dir: str = None,
    theme: str | None = DEFAULT_THEME_NAME,
    logo_path: str | None = None,
    video_subdir: str | None = None,
) -> str:
    """Generate a self-contained HTML presentation. Returns path.

    output_dir: if provided, scans for video files and wires them into the PiP player.
    video_subdir: override which subdir to look in (e.g. 'video_qwen3tts').
                and wires them into the PiP player.
    """
    theme_cfg = load_theme(theme)
    logo_src = _asset_src(logo_path, output_path) if logo_path else None
    slides_data = _build_slides(content)
    slides_html = _render_slides_html(slides_data, content, theme_cfg, logo_src)

    n = len(slides_data)
    video_paths = _find_video_paths(output_dir, n, video_subdir) if output_dir else [None] * n

    slides_json = json.dumps(
        [{"speaker_notes": s.get("speaker_notes", "")} for s in slides_data]
    )
    videos_json = json.dumps(video_paths)

    html = HTML_TEMPLATE.format(
        title=content.get("title", "Training"),
        theme_css=css_vars(theme_cfg),
        slides_html=slides_html,
        slides_json=slides_json,
        videos_json=videos_json,
        total=n,
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def _build_slides(content: dict) -> list:
    """Normalise content into a flat list of slide dicts."""
    slides = []

    # Always prepend a title slide from the top-level metadata
    slides.append({
        "slide_type": "title",
        "title": content.get("title", "Training Presentation"),
        "meta": f"{content.get('duration_minutes', 10)} minutes  ·  {content.get('_audience', 'Engineering Team')}",
        "speaker_notes": "",
    })

    for s in content.get("slides", []):
        if s.get("slide_type") == "title":
            # Update the notes on the title slide we already added
            slides[0]["speaker_notes"] = s.get("speaker_notes", "")
            continue
        slides.append(s)

    return slides


def _render_slides_html(slides: list, content: dict, theme: dict, logo_src: str | None) -> str:
    parts = []
    footer_html = _slide_footer_html(theme, logo_src)

    for slide in slides:
        stype = slide.get("slide_type", "content")

        if stype == "title":
            parts.append(TITLE_SLIDE_HTML.format(
                title=_esc(slide.get("title", "")),
                meta=_esc(slide.get("meta", "")),
                tagline=_esc(theme["html"].get("tagline", "Generated with uruvagam")),
                footer_html=footer_html,
            ))

        else:
            extra = "slide-summary" if stype == "summary" else ""

            bullets = slide.get("bullets", [])
            dense_class = "dense" if len(bullets) > 6 else ""
            bullets_html = "\n".join(
                f'<div class="bullet-row"><span class="bullet-num">{i+1}</span>'
                f'<span class="bullet-text">{_esc(b)}</span></div>'
                for i, b in enumerate(bullets[:8])
            )

            subtitle = slide.get("subtitle", "")
            subtitle_html = (
                f'<div class="slide-subtitle">{_esc(subtitle)}</div>' if subtitle else ""
            )

            diagram = slide.get("diagram", "")
            if diagram:
                # two-column: bullets left, diagram right
                body_class = "has-diagram"
                body_inner = (
                    f'<div class="bullets-col">{subtitle_html}{bullets_html}</div>'
                    f'<div class="diagram-col"><pre class="diagram-block">{_esc(diagram.strip())}</pre></div>'
                )
            else:
                body_class = dense_class
                body_inner = f"{subtitle_html}{bullets_html}"

            cta_html = (
                '<div class="cta-bar">Questions? Connect with the team - slides available in your knowledge base</div>'
                if stype == "summary" else ""
            )

            parts.append(CONTENT_SLIDE_HTML.format(
                title=_esc(slide.get("title", "")),
                extra_class=extra,
                body_class=body_class,
                body_inner=body_inner,
                cta_html=cta_html,
                footer_html=footer_html,
            ))

    return "\n".join(parts)


def _slide_footer_html(theme: dict, logo_src: str | None) -> str:
    footer_text = theme["html"].get("footer_text", "")
    if not logo_src and theme.get("name") == DEFAULT_THEME_NAME:
        return ""
    logo_html = f'<img class="slide-logo" src="{_esc(logo_src)}" alt="Logo">' if logo_src else ""
    text_html = f'<span class="slide-footer-text">{_esc(footer_text)}</span>' if footer_text else ""
    return f'<div class="slide-footer">{logo_html}{text_html}</div>'


def _esc(text: str) -> str:
    """Basic HTML entity escaping."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _find_video_paths(output_dir: str, n_slides: int, video_subdir: str | None = None) -> list:
    """Return list of relative video paths (or None) for each slide index.
    Checks video_subdir (if given), then video_qwen3tts/, then video/ as fallbacks.
    Paths are relative to the HTML file (both live in output_dir)."""
    base = Path(output_dir)
    # priority order: explicit subdir first, then known defaults
    subdirs = []
    if video_subdir:
        subdirs.append(video_subdir)
    for default in ("video_qwen3tts", "video"):
        if not video_subdir or video_subdir != default:
            subdirs.append(default)
    paths = []
    for i in range(1, n_slides + 1):
        found = None
        for subdir in subdirs:
            for name in (f"slide_{i:02d}_lipsync.mp4", f"slide_{i:02d}.mp4"):
                if (base / subdir / name).exists():
                    found = f"{subdir}/{name}"
                    break
            if found:
                break
        paths.append(found)
    return paths


def _asset_src(asset_path: str, output_path: str) -> str | None:
    if asset_path.startswith(("http://", "https://", "data:")):
        return asset_path
    path = Path(asset_path)
    if not path.exists():
        raise FileNotFoundError(f"logo not found: {path}")
    output_dir = Path(output_path).parent
    return os.path.relpath(path.resolve(), output_dir.resolve())


def main():
    parser = argparse.ArgumentParser(description="Regenerate preview.html from a run directory")
    parser.add_argument("output_dir", help="Run directory containing content.json")
    parser.add_argument("--theme", default=DEFAULT_THEME_NAME, help="Theme name or YAML path")
    parser.add_argument("--logo", default=None, help="Optional logo image path")
    parser.add_argument("--video-dir", default=None, help="Video subdir name within run dir (e.g. video_qwen3tts)")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    content_path = out_dir / "content.json"
    if not content_path.exists():
        print(f"ERROR: {content_path} not found")
        sys.exit(1)
    with open(content_path, encoding="utf-8") as f:
        content = json.load(f)
    html_path = str(out_dir / "preview.html")
    try:
        generate_html(
            content,
            html_path,
            output_dir=str(out_dir),
            theme=args.theme,
            logo_path=args.logo,
            video_subdir=args.video_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(f"Regenerated: {html_path}")


if __name__ == "__main__":
    main()
