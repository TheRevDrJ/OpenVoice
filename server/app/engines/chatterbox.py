"""Chatterbox (Resemble AI) — our first TTS engine. Default voice, preset clones,
expressive knobs (exaggeration, cfg_weight). Proven on the 4090, 2026-06-12."""
import io

import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

from .base import TTSEngine, Voice


class ChatterboxEngine(TTSEngine):
    name = "chatterbox"

    def __init__(self, device: str = "cuda"):
        self.device = device
        self.model: ChatterboxTTS | None = None

    def load(self) -> None:
        self.model = ChatterboxTTS.from_pretrained(device=self.device)

    def synthesize(self, text: str, voice: Voice | None = None, **params) -> tuple[bytes, int]:
        if self.model is None:
            raise RuntimeError("ChatterboxEngine.load() not called")

        kwargs = {}
        if voice and voice.ref_audio:
            kwargs["audio_prompt_path"] = voice.ref_audio
        for knob in ("exaggeration", "cfg_weight"):
            if params.get(knob) is not None:
                kwargs[knob] = params[knob]

        wav = self.model.generate(text, **kwargs)
        buf = io.BytesIO()
        ta.save(buf, wav, self.model.sr, format="wav")
        return buf.getvalue(), self.model.sr
