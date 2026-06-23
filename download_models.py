# OpenVoice — Copyright (c) 2026 TheRevDrJ. Licensed under AGPL-3.0-or-later (see LICENSE).
"""Pre-download OpenVoice's model weights into the HuggingFace cache so the first
server start is instant instead of a multi-minute surprise. Safe to re-run — files
already cached are skipped. Covers both engines: Chatterbox + VoxCPM2.
"""
import os
from pathlib import Path

# Mirror server/app/config.py's cache rule: regenerable model bulk lives OUTSIDE the
# Dropbox tree, under OPENVOICE_ROOT (falling back to %LOCALAPPDATA%\openvoice).
_root = os.environ.get("OPENVOICE_ROOT")
if _root:
    HF_HOME = Path(_root) / "hf-cache"
else:
    _base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    HF_HOME = (_base / "openvoice") / "hf-cache"

os.environ.setdefault("HF_HOME", str(HF_HOME))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")  # Windows without Developer Mode
HF_HOME.mkdir(parents=True, exist_ok=True)

from huggingface_hub import snapshot_download

# Repo ids verified against the installed packages (chatterbox/tts.py REPO_ID and the
# voxcpm worker's from_pretrained call) — not guessed.
MODELS = [
    ("ResembleAI/chatterbox", "Chatterbox TTS — default voice, knobs, 10-second clones"),
    ("openbmb/VoxCPM2", "VoxCPM2 — voice design + baked-LoRA clones"),
]

print(f"  HF cache: {HF_HOME}")
for repo, label in MODELS:
    print(f"  Downloading {label}\n    [{repo}] ...")
    snapshot_download(repo)
    print(f"  {label} ready.\n")

print("  All OpenVoice models cached.")
