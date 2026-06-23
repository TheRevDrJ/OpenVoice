"""Voice Lab — bake a usable `voxcpm-lora` voice from an audio file, as a background job.

Orchestrates the exact pipeline run by hand the night the recipe was locked:
  ffmpeg (→mono wav) → GPT-SoVITS slice → faster-whisper ASR → manifest →
  VoxCPM2 LoRA train (~step 100) → register into the voice library → sample.

Each job runs on a daemon thread (the stages shell out to GPU tooling and block).
State is in-memory + mirrored to a status.json per job, polled by GET /api/voicelab/jobs/{id}.

NOTE: the external bake tooling (GPT-SoVITS, the VoxCPM trainer, a local VoxCPM2 snapshot)
is machine-specific and configured via env vars (see below); unset => the bake is reported
unavailable on that box. Serving already-baked voices works anywhere via the worker.
"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import soundfile as sf

from . import config, voices

# --- external bake tooling — CONFIG, never hardcoded ---
# The bake shells out to machine-specific tooling that lives OUTSIDE the repo (GPT-SoVITS,
# the VoxCPM trainer, a local VoxCPM2 snapshot). Read those paths from env on the box that
# runs bakes; unset => the bake reports "not configured" instead of crashing, and a fresh
# clone still runs everything else. Set them in your local (gitignored) run config.
def _envpath(var: str) -> Path | None:
    v = os.environ.get(var)
    return Path(v) if v else None


GPTSOVITS_ROOT = _envpath("OPENVOICE_GPTSOVITS_ROOT")
GPTSOVITS_PY = (GPTSOVITS_ROOT / "runtime" / "python.exe") if GPTSOVITS_ROOT else None
TRAIN_VENV_PY = _envpath("OPENVOICE_TRAIN_VENV_PY")
TRAINER = _envpath("OPENVOICE_TRAINER")
VOXCPM2_MODEL = _envpath("OPENVOICE_VOXCPM2_MODEL")
# Per-job scratch (regenerable) — defaults under the model-cache root (OPENVOICE_ROOT), never Dropbox.
WORK_ROOT = _envpath("OPENVOICE_VOICELAB_WORK") or (config.LOCAL_ROOT / "voicelab-jobs")

_BAKE_TOOLING = {
    "OPENVOICE_GPTSOVITS_ROOT": GPTSOVITS_ROOT,
    "OPENVOICE_TRAIN_VENV_PY": TRAIN_VENV_PY,
    "OPENVOICE_TRAINER": TRAINER,
    "OPENVOICE_VOXCPM2_MODEL": VOXCPM2_MODEL,
}


def bake_available() -> tuple[bool, str]:
    """Whether this box has the external bake tooling configured (env vars set)."""
    missing = [k for k, v in _BAKE_TOOLING.items() if v is None]
    if missing:
        return False, "Voice Lab bake tooling not configured on this server — set " + ", ".join(missing)
    return True, ""

# Ordered stages, each with a coarse progress floor for the UI bar.
STAGES = [
    ("converting", 0.05),
    ("slicing", 0.15),
    ("transcribing", 0.30),
    ("training", 0.50),
    ("registering", 0.90),
    ("ready", 1.0),
]

_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# Keep the machine awake for the duration of a bake. A 30-min capture + LoRA train is a
# long unattended GPU job; if the PC idle-sleeps mid-train it kills the engines AND the
# job (learned the hard way 2026-06-22). Acquired at the start of _run, released in its
# finally (done or error). Windows only; a harmless no-op elsewhere.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _keep_awake(on: bool) -> None:
    try:
        if os.name != "nt":
            return
        flags = _ES_CONTINUOUS | (_ES_SYSTEM_REQUIRED if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def _set(job_id: str, **kw) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)
            job = dict(_jobs[job_id])
    try:
        (WORK_ROOT / job_id / "status.json").write_text(json.dumps(job), encoding="utf-8")
    except Exception:
        pass


def get_job(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", name.lower())[:16]
    return s or uuid.uuid4().hex[:8]


def _unique_voice_id(name: str) -> str:
    base = _slug(name)
    existing = {v["id"] for v in voices.list_voices()}
    vid = base
    while vid in existing:
        vid = f"{base}{uuid.uuid4().hex[:3]}"
    return vid


def _pick_ref(slices_dir: Path) -> Path:
    """A clean 4-8s slice from the middle of the narration (skip the quiet intro)."""
    wavs = sorted(slices_dir.glob("*.wav"))
    good = [w for w in wavs if 4.0 <= sf.info(str(w)).duration <= 8.0]
    pool = good or wavs
    return pool[len(pool) // 2]


def _write_manifest(list_file: Path, out: Path) -> int:
    n = 0
    with open(list_file, "r", encoding="utf-8") as f, open(out, "w", encoding="utf-8") as o:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
            rel, _spk, _lang, text = parts
            text = text.strip()
            if not text:
                continue
            wav = rel if os.path.isabs(rel) else str(GPTSOVITS_ROOT / rel)
            if not os.path.isfile(wav):
                continue
            o.write(json.dumps({"audio": wav.replace("\\", "/"), "text": text}, ensure_ascii=False) + "\n")
            n += 1
    return n


def _write_config(cfg: Path, manifest: Path, ckpt_dir: Path, num_workers: int = 4) -> None:
    cfg.write_text(
        "\n".join(
            [
                f"pretrained_path: {str(VOXCPM2_MODEL).replace(chr(92), '/')}/",
                f"train_manifest: {str(manifest).replace(chr(92), '/')}",
                "val_manifest: null",
                "sample_rate: 16000",
                "out_sample_rate: 48000",
                "batch_size: 2",
                "grad_accum_steps: 8",
                f"num_workers: {num_workers}",
                "num_iters: 100",          # step 100 = the locked sweet spot
                "log_interval: 10",
                "valid_interval: 100",
                "save_interval: 50",       # -> 50 / 100
                "learning_rate: 0.0001",
                "weight_decay: 0.01",
                "warmup_steps: 100",
                "max_steps: 800",          # same LR-schedule horizon as the locked recipe
                "max_batch_tokens: 8192",
                "max_grad_norm: 1.0",
                f"save_path: {str(ckpt_dir).replace(chr(92), '/')}",
                f"tensorboard: {str(ckpt_dir / 'logs').replace(chr(92), '/')}",
                "lambdas:",
                "  loss/diff: 1.0",
                "  loss/stop: 1.0",
                "lora:",
                "  enable_lm: true",
                "  enable_dit: true",
                "  enable_proj: false",
                "  r: 32",
                "  alpha: 32",
                "  dropout: 0.0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def start_bake(
    src_file: Path, name: str, gender: str | None = None, internal_only: bool = False
) -> str:
    ok, msg = bake_available()
    if not ok:
        raise RuntimeError(msg)  # caller (the API) turns this into a clean 503
    job_id = uuid.uuid4().hex[:10]
    (WORK_ROOT / job_id).mkdir(parents=True, exist_ok=True)
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "name": name, "stage": "queued", "progress": 0.0,
            "status": "running", "error": None, "voice_id": None,
        }
    threading.Thread(
        target=_run, args=(job_id, Path(src_file), name, gender, internal_only), daemon=True
    ).start()
    return job_id


def _run(
    job_id: str, src_file: Path, name: str, gender: str | None = None, internal_only: bool = False
) -> None:
    work = WORK_ROOT / job_id
    gs_env = {**os.environ, "PYTHONPATH": str(GPTSOVITS_ROOT), "PYTHONIOENCODING": "utf-8"}
    # GPT-SoVITS tools cache their models (e.g. faster-whisper large-v3) in the DEFAULT
    # HF cache, NOT our app's HF_HOME. Drop the override so the whisper model is found
    # locally instead of (failing to) re-download from a mirror.
    gs_env.pop("HF_HOME", None)
    _keep_awake(True)  # hold off idle-sleep until the bake finishes (or errors)
    try:
        # 1) convert -> mono wav
        _set(job_id, stage="converting", progress=0.05)
        wav = work / "source.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src_file), "-ac", "1", str(wav)],
            check=True, capture_output=True,
        )

        # 2) slice (GPT-SoVITS slicer exits nonzero on a unicode print — verify by output)
        _set(job_id, stage="slicing", progress=0.15)
        slices = work / "slices"
        r = subprocess.run(
            [str(GPTSOVITS_PY), "tools/slice_audio.py", str(wav), str(slices),
             "-34", "4000", "300", "10", "500", "0.9", "0.25", "0", "1"],
            cwd=str(GPTSOVITS_ROOT), env=gs_env, capture_output=True,
        )
        n_slices = len(list(slices.glob("*.wav")))
        if n_slices == 0:
            raise RuntimeError(
                "slicing produced no clips: " + (r.stderr or b"").decode("utf-8", "ignore")[-400:]
            )
        _set(job_id, n_slices=n_slices)

        # 3) ASR
        _set(job_id, stage="transcribing", progress=0.30)
        asr_out = work / "asr"
        r = subprocess.run(
            [str(GPTSOVITS_PY), "tools/asr/fasterwhisper_asr.py", "-i", str(slices),
             "-o", str(asr_out), "-s", "large-v3", "-l", "en", "-p", "float16"],
            cwd=str(GPTSOVITS_ROOT), env=gs_env, capture_output=True,
        )
        list_file = asr_out / f"{slices.name}.list"
        if not list_file.exists():
            raise RuntimeError(
                "ASR produced no transcript list: " + (r.stderr or b"").decode("utf-8", "ignore")[-400:]
            )

        # 4) manifest
        manifest = work / "train.jsonl"
        n_samples = _write_manifest(list_file, manifest)
        if n_samples == 0:
            raise RuntimeError("no usable (clip, transcript) pairs")
        _set(job_id, n_samples=n_samples)

        # 5) train (fresh ckpt dir -> no resume-from-latest surprise)
        _set(job_id, stage="training", progress=0.50)
        ckpt_dir = work / "checkpoints"
        cfg = work / "lora.yaml"
        # Tiny datasets finish an epoch in a couple of steps, so a multi-worker
        # dataloader respawns its workers constantly — on Windows each respawn
        # re-imports the whole torch stack (~2min), which dominated a 5-slice test
        # at ~115s/step. In-thread loading (0 workers) is instant there. Real
        # 30-min captures yield enough slices that 4 workers pay off.
        num_workers = 0 if n_samples < 64 else 4
        _write_config(cfg, manifest, ckpt_dir, num_workers=num_workers)
        subprocess.run([str(TRAIN_VENV_PY), str(TRAINER), "--config_path", str(cfg)],
                       check=True, capture_output=True)
        step = ckpt_dir / "step_0000100"
        if not (step / "lora_weights.safetensors").exists():
            raise RuntimeError("training did not produce a step-100 checkpoint")

        # 6) register into the library
        _set(job_id, stage="registering", progress=0.90)
        vid = _unique_voice_id(name)
        dst_lora = config.LORAS_DIR / vid
        dst_lora.mkdir(parents=True, exist_ok=True)
        for f in ("lora_weights.safetensors", "lora_config.json"):
            shutil.copy2(step / f, dst_lora / f)
        dst_ref = config.VOICES_DIR / f"{vid}.wav"
        shutil.copy2(_pick_ref(slices), dst_ref)
        voices.add_voice(
            name=name,
            description=f"Baked in the Voice Lab from {src_file.name} (VoxCPM2 LoRA, step 100).",
            engine="voxcpm-lora", ref_clip=str(dst_ref), voice_id=vid, lora_dir=vid,
            gender=gender, internal_only=internal_only,
        )

        _set(job_id, stage="ready", progress=1.0, status="done", voice_id=vid)
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", "ignore")[-500:]
        _set(job_id, stage="error", status="error", error=f"{e.cmd[0]} failed: {tail}")
    except Exception as e:
        _set(job_id, stage="error", status="error", error=str(e))
    finally:
        _keep_awake(False)
