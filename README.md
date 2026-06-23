# OpenVoice

**Self-hosted voice synthesis and cloning** — a local, GPU-backed alternative to
cloud TTS services. Runs entirely on your own NVIDIA hardware, behind a clean web
UI and an HTTP API. No accounts, no per-character billing, no sending your voices
to someone else's server.

Part of the **HonedEdge Foundation** — free, open tools for churches and nonprofits.

## What it does

- **Voice synthesis** — type text, pick a voice, get speech, with expressive knobs.
- **Voice design** — describe a voice in plain language and audition three takes.
- **Voice cloning** — a 10-second clone, or a 30-minute "Voice Lab" bake that
  fine-tunes a high-fidelity voice from a recording.
- **Clip Library** — every generation saved, searchable by voice, mark favorites.

Two engines, each in its own environment, behind one backend:
Chatterbox (default + 10-second cloning) and VoxCPM2 (voice design + baked-LoRA clones).

## Quick start

1. Install the **full** NVIDIA driver from [nvidia.com/drivers](https://www.nvidia.com/drivers)
   (the basic Windows display driver is not enough), and restart.
2. Download this repo (Code → Download ZIP, or `git clone`) and extract it.
3. Right-click **`setup.bat`** → *Run as administrator*.
4. Run **`openvoice.bat start`**, then open **http://localhost:5601**.

Full details, flags, and how to move voices between machines: see
[`SETUP.txt`](SETUP.txt).

## For developers

Dev runs the UI on Vite (`:5600`) proxying the FastAPI backend (`:5601`); a
deployed build compiles the UI and the backend serves it single-port. Engine venvs
are uv-managed (`uv sync` per component).
