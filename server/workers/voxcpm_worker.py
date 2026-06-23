"""VoxCPM2 worker — runs in the voxcpm-venv, serves voice design (and later cloning)
on :5602. It's a SEPARATE process from the main backend because VoxCPM's dependency
stack differs from Chatterbox's; the main FastAPI app proxies here (engines/voxcpm.py).

Run from the repo root (its own uv-managed venv):
  & ".\voxcpm\.venv\Scripts\python.exe" -m uvicorn \
    server.workers.voxcpm_worker:app --host 127.0.0.1 --port 5602
"""
import io
import os
from pathlib import Path

# HF cache: honor OPENVOICE_ROOT, else default under LOCALAPPDATA (home as a last resort).
# Never hardcoded — mirrors server/app/config.py so the cache lands in one place.
if "HF_HOME" not in os.environ:
    _root = os.environ.get("OPENVOICE_ROOT")
    if _root:
        os.environ["HF_HOME"] = str(Path(_root) / "hf-cache")
    else:
        _base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        os.environ["HF_HOME"] = str((_base / "openvoice") / "hf-cache")
# Windows without Developer Mode can't create HF cache symlinks → materialize real files.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

from contextlib import asynccontextmanager

import numpy as np
import soundfile as sf
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from voxcpm import VoxCPM
from voxcpm.model.voxcpm2 import LoRAConfig

_state: dict = {}

# Canonical LoRA structure for baked voices (matches the trainer's r32/a32 lm+dit
# recipe). Loading the base WITH this config gives the model LoRA layers we can
# hot-swap per voice; with LoRA disabled the model behaves exactly as the plain base,
# so voice design + reference-cloning are unchanged.
_LORA_CFG = LoRAConfig(enable_lm=True, enable_dit=True, enable_proj=False, r=32, alpha=32, dropout=0.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # optimize=False (no torch.compile) so per-voice LoRA hot-swap stays clean.
    model = VoxCPM.from_pretrained(
        "openbmb/VoxCPM2", load_denoiser=False, optimize=False, lora_config=_LORA_CFG
    )
    model.set_lora_enabled(False)  # default = plain base (design / reference-clone)
    _state["model"] = model
    _state["cur_lora"] = None
    # The TRUE output rate is model.tts_model.sample_rate (VoxCPM2's AudioVAE
    # out_sample_rate) — what their own CLI saves with. Reading .sample_rate off
    # the wrapper returns nothing and a wrong default → slowed-down, low audio.
    _state["sr"] = int(model.tts_model.sample_rate)
    yield


app = FastAPI(title="OpenVoice VoxCPM worker", lifespan=lifespan)


class GenReq(BaseModel):
    text: str
    # Voice DESIGN (first generation only): a natural-language voice spec.
    description: str | None = None
    # Voice REUSE: clone the frozen voice from its canonical clip (timbre locked).
    reference_wav_path: str | None = None
    # BAKED voice: hot-swap this per-voice LoRA adapter dir before generating.
    lora_dir: str | None = None
    cfg_value: float = 2.0
    inference_timesteps: int = 10


@app.get("/health")
def health():
    return {"status": "ok", "engine": "voxcpm", "sr": _state.get("sr")}


@app.post("/generate")
def generate(req: GenReq):
    model = _state["model"]
    sr = _state["sr"]

    if req.lora_dir:
        # Baked, fine-tuned clone: hot-swap this voice's LoRA adapter (cache the
        # current one to skip redundant reloads), enable it, and clone from the
        # voice's frozen reference clip.
        if _state.get("cur_lora") != req.lora_dir:
            model.load_lora(req.lora_dir)
            _state["cur_lora"] = req.lora_dir
        model.set_lora_enabled(True)
        wav = model.generate(
            text=req.text,
            reference_wav_path=req.reference_wav_path,
            cfg_value=req.cfg_value,
            inference_timesteps=req.inference_timesteps,
        )
    elif req.reference_wav_path:
        # Clone a frozen voice: timbre comes entirely from the reference clip,
        # so repeated calls stay the SAME voice (no description re-roll).
        model.set_lora_enabled(False)
        wav = model.generate(
            text=req.text,
            reference_wav_path=req.reference_wav_path,
            cfg_value=req.cfg_value,
            inference_timesteps=req.inference_timesteps,
        )
    else:
        # Voice design: description in parentheses at the START of the text.
        model.set_lora_enabled(False)
        text = f"({req.description}) {req.text}" if req.description else req.text
        wav = model.generate(
            text=text, cfg_value=req.cfg_value, inference_timesteps=req.inference_timesteps
        )

    wav = np.asarray(wav).squeeze()
    buf = io.BytesIO()
    sf.write(buf, wav, sr, format="wav")
    return Response(content=buf.getvalue(), media_type="audio/wav")
