#!/usr/bin/env bash
# uruvagam preflight — verify prerequisites before driving the pipeline.
# Run from the repo root. Exits 0 on pass, 1 on fail. Always prints
# PREFLIGHT_RESULT: <pass|fail> as the last line.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$REPO_ROOT" || { echo "[ERR] cannot cd to repo root"; echo "PREFLIGHT_RESULT: fail"; exit 1; }

OK=0
WARN=0
FAIL=0

pass()  { echo "[OK]   $1"; OK=$((OK+1)); }
warn()  { echo "[WARN] $1"; WARN=$((WARN+1)); }
fail()  { echo "[FAIL] $1"; if [ -n "${2-}" ]; then echo "       fix: $2"; fi; FAIL=$((FAIL+1)); }
info()  { echo "[INFO] $1"; }

echo "uruvagam preflight — repo: $REPO_ROOT"
echo ""

# -- 1. venv --
if [ -x ".venv/bin/python" ]; then
  py_ver=$(.venv/bin/python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "?")
  pass ".venv exists (python $py_ver)"
else
  fail ".venv not found or not executable" "bash setup.sh"
fi

# -- 2. OMLX_API_KEY --
if [ -n "${OMLX_API_KEY:-}" ]; then
  pass "OMLX_API_KEY is set"
else
  fail "OMLX_API_KEY not set" "export OMLX_API_KEY=<your-key>"
fi

# -- 3. oMLX server --
if curl -sf -m 3 -H "Authorization: Bearer ${OMLX_API_KEY:-none}" http://127.0.0.1:8000/v1/models -o /dev/null 2>&1; then
  pass "oMLX server reachable at http://127.0.0.1:8000"
else
  fail "oMLX server not reachable at http://127.0.0.1:8000" "start the oMLX daemon (user-managed) before continuing"
fi

# -- 4. Python packages (Phase A — required for content + voice) --
if [ -x ".venv/bin/python" ]; then
  if .venv/bin/python -c "import f5_tts_mlx" 2>/dev/null; then
    pass "f5_tts_mlx installed"
  else
    fail "f5_tts_mlx not installed" ".venv/bin/pip install -r requirements.txt"
  fi

  # -- 5. Python packages (Phase B — required for lip-sync) --
  if .venv/bin/python -c "import lipsync" 2>/dev/null; then
    pass "lipsync installed"
  else
    fail "lipsync not installed" ".venv/bin/pip install -r requirements-lipsync.txt"
  fi
fi

# -- 6. Wav2Lip checkpoint --
if [ -f "weights/checkpoints/wav2lip.pth" ]; then
  size=$(stat -f%z "weights/checkpoints/wav2lip.pth" 2>/dev/null || stat -c%s "weights/checkpoints/wav2lip.pth" 2>/dev/null || echo 0)
  size_mb=$((size / 1024 / 1024))
  if [ "$size_mb" -gt 300 ]; then
    pass "Wav2Lip checkpoint present (${size_mb} MB)"
  else
    fail "Wav2Lip checkpoint exists but is smaller than expected (${size_mb} MB, expected ~416 MB)" "re-download wav2lip.pth into weights/checkpoints/ (see setup.sh / Agents.md)"
  fi
else
  fail "Wav2Lip checkpoint missing: weights/checkpoints/wav2lip.pth" "download wav2lip.pth (~416 MB) into weights/checkpoints/ (see setup.sh / Agents.md)"
fi

# Normalized state dict (used by lipsync_poc.py) — warn only since some setups derive it on demand
if [ -f "weights/checkpoints/wav2lip_state_dict.pth" ]; then
  pass "Wav2Lip normalized state_dict present"
else
  warn "weights/checkpoints/wav2lip_state_dict.pth not found — first lipsync run may need to derive it"
fi

# -- 7. Required assets --
if [ -f "assets/voice_reference.wav" ] && [ -f "assets/voice_reference.txt" ]; then
  pass "default voice reference present (assets/voice_reference.wav + .txt)"
else
  fail "default voice reference incomplete" "ensure both assets/voice_reference.wav and assets/voice_reference.txt exist (the .txt must contain the exact spoken transcript of the .wav)"
fi

if [ -f "assets/face_reference.mp4" ]; then
  pass "default face reference present (assets/face_reference.mp4)"
else
  fail "default face reference missing: assets/face_reference.mp4" "record/copy a face reference video (1080p, 30fps, 8-12s of stable talking face) into assets/face_reference.mp4"
fi

if [ -f "assets/speaker_style.txt" ]; then
  # check it's not the placeholder example
  if grep -q "example" "assets/speaker_style.txt" 2>/dev/null && [ "$(wc -c < assets/speaker_style.txt)" -lt 300 ]; then
    warn "assets/speaker_style.txt looks like the placeholder — narration may sound generic until you replace it with your own sentences"
  else
    pass "speaker style file present (assets/speaker_style.txt)"
  fi
else
  fail "assets/speaker_style.txt missing" "copy assets/speaker_style.example.txt to assets/speaker_style.txt and edit"
fi

# -- 8. Themes --
if [ -d "themes" ] && [ -n "$(ls themes/*.yaml 2>/dev/null)" ]; then
  themes=$(ls themes/*.yaml 2>/dev/null | xargs -n1 basename | sed 's/\.yaml$//' | tr '\n' ' ')
  pass "themes available: ${themes}"
else
  warn "no theme files found in themes/ — default may fail"
fi

# -- 9. Inventory: voice ref alternates and face ref alternates --
echo ""
info "asset inventory (for selection):"
echo "       voice refs (wav | transcript status):"
for wav in assets/voice_reference*.wav assets/voice_refs/*.wav; do
  [ -f "$wav" ] || continue
  txt="${wav%.wav}.txt"
  if [ -f "$txt" ]; then
    echo "         $wav  [paired]"
  else
    echo "         $wav  [MISSING $txt — unusable until transcript is added]"
  fi
done
echo "       face refs:"
ls -1 assets/face_reference*.mp4 2>/dev/null | sed 's/^/         /'

# -- 10. Current run detection --
NEWEST_RUN=$(ls -td outputs/*/ 2>/dev/null | head -1 | sed 's|/$||' || true)
if [ -n "$NEWEST_RUN" ]; then
  info "newest run: $NEWEST_RUN"
else
  info "no existing outputs/<run>/ — this would be a fresh run"
fi

echo ""
echo "summary: $OK ok, $WARN warn, $FAIL fail"
if [ "$FAIL" -gt 0 ]; then
  echo "PREFLIGHT_RESULT: fail"
  exit 1
else
  echo "PREFLIGHT_RESULT: pass"
  exit 0
fi
