"""Voice registry — named voices, each optionally backed by a reference clip
(for cloning) or born from a saved voice-design. Metadata in voices.json; clips in
the local-only voices dir. Voices can be enabled/disabled (Synthesis draws only from
enabled) and deleted."""
import json
import os
import time
import uuid

from . import config

_VOICES_JSON = config.VOICES_DIR / "voices.json"

# Always-present engine default (Chatterbox built-in voice). Can't be disabled/deleted.
DEFAULT_VOICE = {
    "id": "default",
    "name": "Computer",
    "description": "Chatterbox default voice",
    "engine": "chatterbox",
    "ref_clip": None,
    "builtin": True,
    "enabled": True,
    "gender": "male",
    "internal_only": False,
}


def _load() -> list[dict]:
    if _VOICES_JSON.exists():
        try:
            data = json.loads(_VOICES_JSON.read_text(encoding="utf-8"))
        except Exception:
            return []
        for v in data:
            v.setdefault("enabled", True)  # normalize older records
            v.setdefault("gender", None)        # "male" | "female" | None (unset)
            v.setdefault("internal_only", False)  # testing-only / don't-distribute flag
            # ref_clip is stored as a bare filename (portable across machines/clones);
            # migrate any legacy absolute path down to its basename.
            if v.get("ref_clip"):
                v["ref_clip"] = os.path.basename(v["ref_clip"])
        return data
    return []


def _save(voices: list[dict]) -> None:
    _VOICES_JSON.write_text(json.dumps(voices, indent=2), encoding="utf-8")


def _merged_default(voices: list[dict]) -> dict:
    """The built-in voice, with any persisted overrides applied (only its Synthesis
    visibility is ever stored). Always stays builtin so it can't be deleted/renamed."""
    persisted = next((v for v in voices if v["id"] == "default"), None)
    return {**DEFAULT_VOICE, **(persisted or {}), "builtin": True}


def list_voices() -> list[dict]:
    voices = _load()
    rest = [v for v in voices if v["id"] != "default"]
    return [_merged_default(voices), *rest]


def get_voice(voice_id: str | None) -> dict | None:
    if voice_id in (None, "", "default"):
        return _merged_default(_load())
    return next((v for v in _load() if v["id"] == voice_id), None)


def clip_path(voice: dict | None) -> str | None:
    """Resolve a voice's stored ref_clip (a bare filename) to an absolute path under
    this machine's VOICES_DIR. The registry stays portable: voices.json holds only the
    filename, so the same data works on any machine or fresh clone."""
    if not voice or not voice.get("ref_clip"):
        return None
    return str(config.VOICES_DIR / voice["ref_clip"])


def lora_path(voice: dict | None) -> str | None:
    """Resolve a baked voice's LoRA adapter dir (a bare name) to an absolute path
    under this machine's LORAS_DIR. Portable, same as clip_path."""
    if not voice or not voice.get("lora_dir"):
        return None
    return str(config.LORAS_DIR / voice["lora_dir"])


def add_voice(
    name: str,
    description: str = "",
    engine: str = "chatterbox",
    ref_clip: str | None = None,
    voice_id: str | None = None,
    lora_dir: str | None = None,
    gender: str | None = None,
    internal_only: bool = False,
) -> dict:
    voices = _load()
    v = {
        "id": voice_id or uuid.uuid4().hex[:8],
        "name": name,
        "description": description,
        "engine": engine,
        "ref_clip": os.path.basename(ref_clip) if ref_clip else None,
        # baked (voxcpm-lora) voices also carry a per-voice adapter dir (bare name,
        # resolved under LORAS_DIR by lora_path() — portable like ref_clip).
        "lora_dir": lora_dir,
        "builtin": False,
        "enabled": True,
        # Curation metadata (set at creation in Voice Lab, editable in Voice Library):
        "gender": gender if gender in ("male", "female", "unspecified") else None,
        "internal_only": bool(internal_only),
        "created": int(time.time()),
    }
    voices.insert(0, v)
    _save(voices)
    return v


def set_meta(voice_id: str, gender: str | None = None, internal_only: bool | None = None) -> dict | None:
    """Edit a voice's curation metadata from the Voice Library. Only the provided
    fields change; gender must be 'male'/'female' (anything else clears it to unset)."""
    voices = _load()
    for v in voices:
        if v["id"] == voice_id:
            if gender is not None:
                v["gender"] = gender if gender in ("male", "female", "unspecified") else None
            if internal_only is not None:
                v["internal_only"] = bool(internal_only)
            _save(voices)
            return v
    return None


def set_enabled(voice_id: str, enabled: bool) -> dict | None:
    voices = _load()
    for v in voices:
        if v["id"] == voice_id:
            v["enabled"] = bool(enabled)
            _save(voices)
            return v
    # The built-in default isn't normally in voices.json — materialize it (overrides
    # only) the first time it's hidden, so its visibility persists.
    if voice_id == "default":
        rec = {**DEFAULT_VOICE, "enabled": bool(enabled)}
        voices.append(rec)
        _save(voices)
        return _merged_default(voices)
    return None


def delete_voice(voice_id: str) -> dict | None:
    if voice_id == "default":
        return None  # built-in — never deletable
    voices = _load()
    removed = next((v for v in voices if v["id"] == voice_id), None)
    if removed is None:
        return None
    _save([v for v in voices if v["id"] != voice_id])
    return removed
