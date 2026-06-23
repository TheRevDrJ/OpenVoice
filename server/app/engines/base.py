"""Engine abstraction — OpenVoice is a registry of capability-specialized engines
(best TTS voice + best voice-design + best API, composed). This is the TTS slice."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Voice:
    id: str
    name: str
    # Reference clip for cloning-based engines; None = engine default voice.
    ref_audio: str | None = None


class TTSEngine(ABC):
    """Speaks text. Voice may be a preset, a clone reference, or None (default)."""

    name: str = "base"

    @abstractmethod
    def load(self) -> None:
        """Load weights into VRAM. Called once at startup; kept warm."""

    @abstractmethod
    def synthesize(self, text: str, voice: Voice | None = None, **params) -> tuple[bytes, int]:
        """Return (wav_bytes, sample_rate)."""
