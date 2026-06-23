"""OpenVoice API — two consumers: the web UI and programmatic API callers.

Multi-engine: Chatterbox (default voice + cloning + knobs) and VoxCPM (voice design
+ baked-LoRA clones). All behind /api/tts."""
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import config
from . import voicelab
from . import voices as voice_registry
from .engines.base import Voice
from .engines.chatterbox import ChatterboxEngine
from .engines.voxcpm import VoxCPMEngine

chatterbox = ChatterboxEngine()
voxcpm = VoxCPMEngine()

# Baked into every voice design — stops VoxCPM inventing random rooms. The
# clean-studio steer lands at "sound booth" quality.
STUDIO_SUFFIX = (
    ", clean professional studio recording, close dry microphone, "
    "no background noise, no reverb, no room ambience"
)
SAMPLE_LINE = "A gentle rain fell over the city as the evening lights came on."
# Phonetically rich line for Library voice previews (the Rainbow Passage opening — a
# standard in voice/elocution work). Broad sound coverage shows a voice off well.
PREVIEW_LINE = (
    "When the sunlight strikes raindrops in the air, they act as a prism and form a rainbow."
)

# Strips any stray inline tags (e.g. a leftover <angry>..</angry> from the old emotion
# feature) so they're never read aloud. Emotion (IndexTTS2) was removed — see git history.
_TAG_RE = re.compile(r"<\s*/?\s*[^>]+?\s*>")


@asynccontextmanager
async def lifespan(app: FastAPI):
    chatterbox.load()  # warm Chatterbox; VoxCPM is warmed by its worker
    yield


VERSION = "1.1.0"

app = FastAPI(title="OpenVoice", version=VERSION, lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None
    exaggeration: float | None = None
    cfg_weight: float | None = None
    save: bool = False  # UI synthesis sets this → the clip lands in the Clip Library
    rate: float = 1.0  # pitch-preserving speed (ffmpeg atempo); 1.0 = normal, 1.1 = +10%


class DesignRequest(BaseModel):
    name: str
    description: str
    text: str = SAMPLE_LINE


class PreviewRequest(BaseModel):
    description: str
    text: str = SAMPLE_LINE
    cfg_base: float = 2.0


class SaveRequest(BaseModel):
    name: str
    description: str
    variant_id: str
    gender: str | None = None
    internal_only: bool = False


class EnabledRequest(BaseModel):
    enabled: bool


class FavoriteRequest(BaseModel):
    favorite: bool


class MetaRequest(BaseModel):
    # Curation metadata edited from the Voice Library. Either field may be omitted.
    gender: str | None = None
    internal_only: bool | None = None


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "engines": {
            "chatterbox": True,
            "voxcpm": voxcpm.worker_alive(),
        },
    }


@app.get("/api/admin/engines")
def engine_status():
    """Live engine status. Both engines are always-on; this is a health readout,
    not a control surface (VoxCPM is no longer started/stopped on demand)."""
    return {
        "chatterbox": True,
        "voxcpm": voxcpm.worker_alive(),
    }


@app.get("/api/voices")
def get_voices():
    return voice_registry.list_voices()


@app.post("/api/voices/{voice_id}/enabled")
def set_voice_enabled(voice_id: str, req: EnabledRequest):
    v = voice_registry.set_enabled(voice_id, req.enabled)
    if v is None:
        raise HTTPException(status_code=404, detail="unknown voice")
    return v


@app.post("/api/voices/{voice_id}/meta")
def set_voice_meta(voice_id: str, req: MetaRequest):
    """Voice Library: edit a voice's gender / internal-only flag."""
    v = voice_registry.set_meta(voice_id, gender=req.gender, internal_only=req.internal_only)
    if v is None:
        raise HTTPException(status_code=404, detail="unknown voice")
    return v


@app.delete("/api/voices/{voice_id}")
def remove_voice(voice_id: str):
    removed = voice_registry.delete_voice(voice_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="unknown voice")
    ref = voice_registry.clip_path(removed)
    if ref and os.path.exists(ref):
        try:
            os.remove(ref)
        except OSError:
            pass
    return {"ok": True, "id": voice_id}


@app.get("/api/voices/{voice_id}/sample")
def voice_sample(voice_id: str):
    v = voice_registry.get_voice(voice_id)
    if v is None:
        raise HTTPException(status_code=404, detail="unknown voice")
    # Preview = what the voice actually generates (a real synthesis of the sample line),
    # NOT its source/reference clip — synthesized once on demand and cached.
    cache = config.VOICES_DIR / f"_sample_{v['id']}.wav"
    if not cache.exists():
        cache.write_bytes(_synth_voice(v, PREVIEW_LINE))
    # Don't let the browser cache previews — they get regenerated, and a stale copy
    # would silently replay an old (or source-clip) version.
    return FileResponse(
        str(cache), media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )


def _synth_voice(v: dict, text: str, exaggeration=None, cfg_weight=None) -> bytes:
    """Synthesize `text` in voice `v` via its engine (no emotion). Shared by /api/tts
    and the Library sample preview, so a voice's preview is what it actually GENERATES,
    not the source clip it was cloned from."""
    engine = v.get("engine")
    if engine == "voxcpm-lora":
        if not voxcpm.worker_alive():
            raise HTTPException(status_code=503, detail="VoxCPM worker not running")
        ref = voice_registry.clip_path(v)
        lora = voice_registry.lora_path(v)
        if not ref or not lora:
            raise HTTPException(
                status_code=400, detail="baked voice is missing its adapter or reference clip"
            )
        clean = _TAG_RE.sub("", text)  # strip any stray inline tags
        audio, _sr = voxcpm.synthesize(
            clean, reference_wav_path=ref, lora_dir=lora, inference_timesteps=28
        )
    elif engine == "voxcpm":
        if not voxcpm.worker_alive():
            raise HTTPException(status_code=503, detail="VoxCPM worker not running")
        ref = voice_registry.clip_path(v)
        if ref:
            audio, _sr = voxcpm.synthesize(text, reference_wav_path=ref)
        else:
            audio, _sr = voxcpm.synthesize(text, description=v.get("description"))
    else:
        voice = Voice(id=v["id"], name=v["name"], ref_audio=voice_registry.clip_path(v))
        audio, _sr = chatterbox.synthesize(
            text, voice=voice, exaggeration=exaggeration, cfg_weight=cfg_weight
        )
    return audio


def _load_manifest() -> list:
    """The Clip Library manifest: per-clip metadata (caption, voice, favorite). It's a
    lookup keyed by filename — listen_list globs the dir for the actual files."""
    manifest = config.LISTEN_DIR / "manifest.json"
    if not manifest.exists():
        return []
    try:
        items = json.loads(manifest.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _save_manifest(items: list) -> None:
    (config.LISTEN_DIR / "manifest.json").write_text(
        json.dumps(items, indent=2), encoding="utf-8"
    )


def _manifest_upsert(fname: str, **fields) -> None:
    """Merge fields into the manifest entry for fname (creating it if absent)."""
    items = _load_manifest()
    for it in items:
        if it.get("file") == fname:
            it.update(fields)
            break
    else:
        items.append({"file": fname, **fields})
    _save_manifest(items)


def _apply_rate(audio: bytes, rate: float) -> bytes:
    """Pitch-preserving speed change via ffmpeg atempo. Input wav comes in on a pipe;
    output goes to a seekable temp file (the wav muxer can't write its header to a
    pipe). Falls back to the original audio on any failure."""
    if not rate or abs(rate - 1.0) < 0.01:
        return audio
    rate = max(0.5, min(2.0, rate))  # single atempo pass covers this range
    out = config.AUDIO_OUT / f"_rate_{uuid.uuid4().hex[:8]}.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-filter:a", f"atempo={rate:.3f}", "-y", str(out)],
            input=audio, check=True, capture_output=True,
        )
        return out.read_bytes()
    except Exception:
        return audio
    finally:
        out.unlink(missing_ok=True)


def _save_clip(audio: bytes, voice_name: str, text: str) -> str | None:
    """Drop a generated clip into the Clip Library (data/listen) so the UI can find it."""
    try:
        slug = re.sub(r"[^a-z0-9]+", "-", voice_name.lower()).strip("-")[:24] or "clip"
        fname = f"{slug}-{uuid.uuid4().hex[:6]}.wav"
        (config.LISTEN_DIR / fname).write_bytes(audio)
        _manifest_upsert(fname, caption=text.strip()[:200], voice=voice_name)
        return fname
    except Exception:
        return None


@app.post("/api/tts")
def tts(req: TTSRequest):
    v = voice_registry.get_voice(req.voice_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"unknown voice: {req.voice_id}")

    # Synthesize via the voice's engine (voxcpm-lora / voxcpm / chatterbox), shared with
    # the Library sample preview. Any stray inline tags are stripped so a leftover
    # <angry>..</angry> is never read aloud (emotion was an IndexTTS2 feature, removed).
    text = _TAG_RE.sub("", req.text)
    audio = _synth_voice(v, text, req.exaggeration, req.cfg_weight)

    audio = _apply_rate(audio, req.rate)  # pitch-preserving speed (no-op at 1.0)

    headers = {}
    if req.save:
        fname = _save_clip(audio, v["name"], req.text)
        if fname:
            headers["X-Clip-File"] = fname
    return Response(content=audio, media_type="audio/wav", headers=headers)


@app.post("/api/design")
def design(req: DesignRequest):
    """One-shot design (programmatic). The UI uses the A/B/C preview + save flow."""
    if not voxcpm.worker_alive():
        raise HTTPException(status_code=503, detail="VoxCPM worker not running")
    audio, _sr = voxcpm.synthesize(req.text, description=req.description + STUDIO_SUFFIX)
    vid = uuid.uuid4().hex[:8]
    clip = config.VOICES_DIR / f"{vid}.wav"
    clip.write_bytes(audio)
    voice = voice_registry.add_voice(
        name=req.name, description=req.description, engine="voxcpm", ref_clip=str(clip), voice_id=vid
    )
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"X-Voice-Id": voice["id"], "X-Voice-Name": voice["name"]},
    )


@app.post("/api/design/preview")
def design_preview(req: PreviewRequest):
    """Generate three variants (A/B/C) of a described voice, perturbing guidance
    around cfg_base. Returns ids; audio is served via /api/design/preview/{id}."""
    if not voxcpm.worker_alive():
        raise HTTPException(status_code=503, detail="VoxCPM worker not running")
    desc = req.description.strip() + STUDIO_SUFFIX
    variants = []
    for label, delta in (("A", -0.25), ("B", 0.0), ("C", 0.25)):
        cfg = max(0.5, req.cfg_base + delta)
        audio, _sr = voxcpm.synthesize(req.text, description=desc, cfg_value=cfg)
        vid = uuid.uuid4().hex[:10]
        (config.PREVIEW_DIR / f"{vid}.wav").write_bytes(audio)
        variants.append({"id": vid, "label": label})
    return {"variants": variants}


@app.get("/api/design/preview/{vid}")
def design_preview_audio(vid: str):
    if "/" in vid or "\\" in vid or ".." in vid:
        raise HTTPException(status_code=404, detail="not found")
    p = config.PREVIEW_DIR / f"{vid}.wav"
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), media_type="audio/wav")


@app.post("/api/design/save")
def design_save(req: SaveRequest):
    """Promote a chosen A/B/C variant into a saved, frozen voice."""
    if "/" in req.variant_id or "\\" in req.variant_id or ".." in req.variant_id:
        raise HTTPException(status_code=400, detail="bad variant id")
    src = config.PREVIEW_DIR / f"{req.variant_id}.wav"
    if not src.exists():
        raise HTTPException(status_code=404, detail="variant expired — regenerate")
    vid = uuid.uuid4().hex[:8]
    dst = config.VOICES_DIR / f"{vid}.wav"
    dst.write_bytes(src.read_bytes())
    return voice_registry.add_voice(
        name=req.name, description=req.description, engine="voxcpm", ref_clip=str(dst), voice_id=vid,
        gender=req.gender, internal_only=req.internal_only,
    )


@app.post("/api/voices/clone")
async def voices_clone(
    name: str = Form(...),
    file: UploadFile = File(...),
    gender: str | None = Form(None),
    internal_only: bool = Form(False),
):
    """Quick clone (Voice Lab · 10-Second Clone): upload a short sample, store a clean
    mono wav, and register a Chatterbox zero-shot clone. Instant — lower fidelity than a
    30-Minute deep bake, but no GPU training. Chatterbox resamples the prompt itself, so
    we only need a faithful mono wav as the reference clip."""
    nm = name.strip()
    if not nm:
        raise HTTPException(status_code=400, detail="name required")
    vid = uuid.uuid4().hex[:8]
    tmp = config.VOICES_DIR / f"_upload_{vid}"
    dst = config.VOICES_DIR / f"{vid}.wav"
    with open(tmp, "wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(tmp),
             "-ac", "1", "-ar", "24000", str(dst)],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", "ignore")[-300:]
        raise HTTPException(status_code=400, detail=f"could not decode audio — {tail}")
    finally:
        tmp.unlink(missing_ok=True)
    return voice_registry.add_voice(
        name=nm, description="Quick clone from a short sample.",
        engine="chatterbox", ref_clip=str(dst), voice_id=vid,
        gender=gender, internal_only=internal_only,
    )


@app.post("/api/voicelab/bake")
async def voicelab_bake(
    name: str = Form(...),
    file: UploadFile = File(...),
    gender: str | None = Form(None),
    internal_only: bool = Form(False),
):
    """Voice Lab: upload an audio file + a name → bake a usable voxcpm-lora voice as a
    background job (ffmpeg → slice → ASR → train → register). Returns a job_id to poll."""
    ok, msg = voicelab.bake_available()
    if not ok:
        raise HTTPException(status_code=503, detail=msg)
    uploads = voicelab.WORK_ROOT / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "upload")
    dst = uploads / f"{uuid.uuid4().hex[:8]}_{safe}"
    with open(dst, "wb") as out:
        shutil.copyfileobj(file.file, out)
    job_id = voicelab.start_bake(
        dst, name.strip() or "Untitled voice", gender=gender, internal_only=internal_only
    )
    return {"job_id": job_id}


@app.get("/api/voicelab/jobs/{job_id}")
def voicelab_job(job_id: str):
    """Poll a bake job: stage, progress, status (running/done/error), and voice_id when ready."""
    j = voicelab.get_job(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return j


@app.get("/api/listen")
def listen_list():
    """The Clip Library: every .wav in data/listen, newest first, with captions looked
    up from the manifest. Generated clips (saved on synthesis) land here automatically."""
    meta: dict[str, dict] = {}
    for it in _load_manifest():
        if it.get("file"):
            meta[it["file"]] = it
    wavs = sorted(
        config.LISTEN_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    items = [
        {
            "file": p.name,
            "caption": meta.get(p.name, {}).get("caption", ""),
            "voice": meta.get(p.name, {}).get("voice", ""),
            "favorite": bool(meta.get(p.name, {}).get("favorite", False)),
        }
        for p in wavs
    ]
    return {"dir": str(config.LISTEN_DIR), "items": items}


@app.get("/api/listen/{filename}")
def listen_file(filename: str):
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=404, detail="not found")
    path = config.LISTEN_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(path), media_type="audio/wav")


@app.delete("/api/listen/{filename}")
def delete_listen_file(filename: str):
    """Clip Library: delete a clip from data/listen. Any manifest entry self-heals on
    the next read (listen_list globs the dir, captions are just a lookup)."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=404, detail="not found")
    path = config.LISTEN_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    try:
        os.remove(path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not delete — {e}")
    # Prune the manifest entry so favorites/captions don't orphan.
    _save_manifest([it for it in _load_manifest() if it.get("file") != filename])
    return {"ok": True, "file": filename}


@app.post("/api/listen/{filename}/favorite")
def favorite_listen_file(filename: str, req: FavoriteRequest):
    """Mark/unmark a Clip Library clip as a favorite (stored in the manifest)."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=404, detail="not found")
    path = config.LISTEN_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    _manifest_upsert(filename, favorite=bool(req.favorite))
    return {"ok": True, "file": filename, "favorite": bool(req.favorite)}


@app.post("/api/listen/{filename}/reveal")
def reveal_listen_file(filename: str):
    """Open the Clip Library folder in the OS file manager with the clip selected.
    Local/home-use only (acts on the machine running the backend)."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=404, detail="not found")
    path = config.LISTEN_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    try:
        if sys.platform == "win32":
            # explorer /select,<path> via subprocess is flaky (explorer doesn't parse
            # argv normally). os.startfile on the folder is reliable.
            os.startfile(str(path.parent))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"could not open folder — {e}")
    return {"ok": True}


# --- Single-port deploy: serve the built UI from this backend ---------------------
# Mounted LAST, after every /api/* route, so the catch-all static mount never shadows
# the API (Starlette matches routes in registration order). In dev this directory does
# not exist — the UI is served by Vite on :5600 — so the mount is simply skipped.
if (config.DIST_DIR / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(config.DIST_DIR), html=True), name="ui")
