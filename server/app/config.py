"""Central config — where everything lives, by the project's data-safety rule.

Two homes:
- USER DATA — voice clips + the ``voices.json`` registry — lives under ``data/voices``,
  backed up by Dropbox on the dev box but **gitignored** (never committed, public or private).
  The built-in voice ships in code, so a fresh clone still works with no data present.
  Generated audio / previews / review clips (``data/out|preview|listen``) and LoRA adapter
  weights (``*.safetensors``) are likewise gitignored and Dropbox-backed. (Hard lesson
  2026-06-19: data parked in a non-backed-up AppData dir got wiped and took the voice library
  with it — keep irreplaceable data in the Dropbox tree.)
- REGENERABLE BULK — model weights — is huge and freely re-downloadable, so it stays
  OUTSIDE Dropbox to avoid pointless sync/backup churn. ``OPENVOICE_ROOT`` overrides the
  location (e.g. a deploy box points it at its own data drive); otherwise it defaults under
  ``%LOCALAPPDATA%``.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- Produced / kept data -> repo data/ tree (Dropbox-backed, gitignored) ---
DATA_ROOT = REPO_ROOT / "data"
VOICES_DIR = DATA_ROOT / "voices"
AUDIO_OUT = DATA_ROOT / "out"
LISTEN_DIR = DATA_ROOT / "listen"    # curated clips to review (Listen tab)
PREVIEW_DIR = DATA_ROOT / "preview"  # transient A/B/C design variants awaiting a save
LORAS_DIR = VOICES_DIR / "loras"     # per-voice LoRA adapters for baked (cloned) voices

# --- Regenerable bulk (model weights) -> OUTSIDE Dropbox ---
# OPENVOICE_ROOT overrides the location (e.g. a deploy box points it at its own data
# drive); otherwise default under LOCALAPPDATA (home as a last resort).
_root_override = os.environ.get("OPENVOICE_ROOT")
if _root_override:
    LOCAL_ROOT = Path(_root_override)
else:
    _base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    LOCAL_ROOT = _base / "openvoice"
HF_HOME = LOCAL_ROOT / "hf-cache"

# HF_HOME must be set before any huggingface / chatterbox import so weights land here.
os.environ.setdefault("HF_HOME", str(HF_HOME))
for d in (HF_HOME, AUDIO_OUT, VOICES_DIR, LISTEN_DIR, PREVIEW_DIR, LORAS_DIR):
    d.mkdir(parents=True, exist_ok=True)

BACKEND_PORT = 5601
FRONTEND_PORT = 5600

# --- Built frontend (single-port deploy) ---
# In DEV the UI runs on Vite (:5600) and proxies /api to this backend. In a DEPLOYED
# build the frontend is compiled and served straight from this backend, so a box runs
# on ONE port with no Node process. OPENVOICE_DIST overrides the location (the dev box
# builds outside Dropbox to dodge the EPERM race); default is the repo's web/dist.
DIST_DIR = Path(os.environ.get("OPENVOICE_DIST") or (REPO_ROOT / "web" / "dist"))
