"""Import an arbitrary audio file (e.g. a captured .weba/.webm/.mp3) as a Library
voice. Transcodes to a mono 16-bit WAV reference clip via ffmpeg, then registers it
through the same voice registry the API uses.

Usage (from repo root, backend venv python):
  python scripts/import_clip.py <source_audio> "<voice name>" "<description>"
"""
import os
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.app import config  # noqa: E402
from server.app import voices as voice_registry  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: import_clip.py <source_audio> <name> [description]")
        sys.exit(1)
    src = sys.argv[1]
    name = sys.argv[2]
    desc = sys.argv[3] if len(sys.argv) > 3 else ""
    if not os.path.exists(src):
        print(f"no such file: {src}")
        sys.exit(1)

    vid = uuid.uuid4().hex[:8]
    dst = config.VOICES_DIR / f"{vid}.wav"

    # Downmix to mono, keep the source sample rate (engines resample internally).
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-ac", "1", "-sample_fmt", "s16", str(dst)],
        check=True,
        capture_output=True,
    )

    # Report what we wrote (duration / rate) for a sanity line.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=sample_rate", "-of", "default=nw=1", str(dst)],
        capture_output=True, text=True,
    )

    v = voice_registry.add_voice(
        name=name, description=desc, engine="chatterbox", ref_clip=str(dst), voice_id=vid
    )
    print("REGISTERED", v["id"], "->", v["name"])
    print("CLIP", dst)
    print(probe.stdout.strip())


if __name__ == "__main__":
    main()
