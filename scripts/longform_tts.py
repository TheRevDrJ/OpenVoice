"""Long-form narration: render a whole text file in a Library voice by chunking it
and calling the *running* backend (so it reuses the already-warm model — constant
VRAM, no reload), then stitching the chunks with natural pauses into one WAV that
lands in the Listen tab.

Usage (backend must be running on :5601):
  python scripts/longform_tts.py <text_file> <voice_name> <out_filename.wav> "<caption>"
"""
import io
import json
import os
import re
import sys
import urllib.request

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.app import config  # noqa: E402
from server.app import voices as voice_registry  # noqa: E402

API = "http://127.0.0.1:5601/api/tts"
MAXCHARS = 240   # group sentences up to this; keeps each generation short + crisp
HARDSPLIT = 320  # a single sentence longer than this gets split at clause breaks

# Spoken-pause lengths (seconds) between stitched pieces.
GAP_PARA = 0.65
GAP_SENT = 0.32
GAP_CLAUSE = 0.14


def resolve_voice(name: str) -> str:
    for v in voice_registry.list_voices():
        if v["name"].lower() == name.lower():
            return v["id"]
    print(f"no voice named {name!r}")
    sys.exit(1)


def split_sentences(para: str) -> list[str]:
    # Break after . ! ? plus any closing quote/bracket, on whitespace. Done with a
    # substitution (not a look-behind) so the optional quote stays legal in regex.
    marked = re.sub(r'([.!?]["”\'\)\]]*)\s+', r"\1\n", para.strip())
    return [s.strip() for s in marked.split("\n") if s.strip()]


def clause_split(sentence: str) -> list[str]:
    # Last resort for very long sentences: split at commas / semicolons / dashes.
    parts = re.split(r'(?<=[,;:—–-])\s+', sentence)
    out, buf = [], ""
    for p in parts:
        if len(buf) + len(p) + 1 <= HARDSPLIT:
            buf = (buf + " " + p).strip()
        else:
            if buf:
                out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def build_chunks(text: str) -> list[tuple[str, bool]]:
    """Return (chunk_text, ends_paragraph) in order."""
    # Strip written scripture citations like "(Jonah 1:12)" / "(Jonah 4:2-3)".
    text = re.sub(r"\s*\([A-Z][A-Za-z]+\.?\s*\d+:\d+(?:[-–]\d+)?\)", "", text)
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    chunks: list[tuple[str, bool]] = []
    for para in paragraphs:
        sentences = split_sentences(para)
        buf = ""
        para_pieces: list[str] = []
        for s in sentences:
            for piece in (clause_split(s) if len(s) > HARDSPLIT else [s]):
                if len(buf) + len(piece) + 1 <= MAXCHARS:
                    buf = (buf + " " + piece).strip()
                else:
                    if buf:
                        para_pieces.append(buf)
                    buf = piece
        if buf:
            para_pieces.append(buf)
        for i, piece in enumerate(para_pieces):
            chunks.append((piece, i == len(para_pieces) - 1))
    return chunks


def synth(text: str, voice_id: str) -> tuple[np.ndarray, int]:
    payload = json.dumps(
        {"text": text, "voice_id": voice_id, "exaggeration": 0.5, "cfg_weight": 0.5}
    ).encode()
    req = urllib.request.Request(
        API, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        wav = resp.read()
    data, sr = sf.read(io.BytesIO(wav))
    if getattr(data, "ndim", 1) > 1:
        data = data[:, 0]
    return data.astype(np.float32), sr


def main() -> None:
    text_file, voice_name, out_name = sys.argv[1], sys.argv[2], sys.argv[3]
    caption = sys.argv[4] if len(sys.argv) > 4 else ""
    voice_id = resolve_voice(voice_name)
    text = open(text_file, encoding="utf-8").read()
    chunks = build_chunks(text)
    n = len(chunks)
    print(f"VOICE {voice_name} ({voice_id}) · {n} chunks", flush=True)

    sr = 24000
    pieces: list[np.ndarray] = []
    for i, (chunk, ends_para) in enumerate(chunks, 1):
        try:
            data, sr = synth(chunk, voice_id)
        except Exception as e:  # one bad chunk shouldn't sink the whole render
            print(f"  [{i}/{n}] FAILED ({e}) — skipping: {chunk[:50]!r}", flush=True)
            continue
        pieces.append(data)
        gap = GAP_PARA if ends_para else GAP_SENT
        if i < n:
            pieces.append(np.zeros(int(sr * gap), dtype=np.float32))
        print(f"  [{i}/{n}] {len(data)/sr:5.1f}s  {chunk[:48]!r}", flush=True)

    full = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    out_path = config.LISTEN_DIR / out_name
    sf.write(str(out_path), full, sr, subtype="PCM_16")
    total = len(full) / sr
    print(f"WROTE {out_path}  ({total/60:.1f} min)", flush=True)

    # Register a caption in the Listen manifest so it shows up labeled.
    manifest_path = config.LISTEN_DIR / "manifest.json"
    items = []
    if manifest_path.exists():
        try:
            items = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            items = []
    items = [it for it in items if it.get("file") != out_name]
    items.insert(0, {"file": out_name, "caption": caption or out_name})
    manifest_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
    print("MANIFEST updated", flush=True)


if __name__ == "__main__":
    main()
