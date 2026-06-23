"""VoxCPM engine — a thin proxy to the VoxCPM worker process on :5602 (it lives in
its own venv). Handles voice design (novel voice from a text description)."""
import httpx

from .base import TTSEngine

WORKER_URL = "http://127.0.0.1:5602"


class VoxCPMEngine(TTSEngine):
    name = "voxcpm"

    def load(self) -> None:
        # The worker process owns the model; nothing to load in-process.
        pass

    def worker_alive(self) -> bool:
        try:
            return httpx.get(f"{WORKER_URL}/health", timeout=2.0).status_code == 200
        except Exception:
            return False

    def synthesize(self, text: str, voice=None, **params) -> tuple[bytes, int]:
        payload: dict = {"text": text}
        if params.get("description"):
            payload["description"] = params["description"]
        if params.get("reference_wav_path"):
            payload["reference_wav_path"] = params["reference_wav_path"]
        if params.get("lora_dir"):
            payload["lora_dir"] = params["lora_dir"]
        if params.get("cfg_value") is not None:
            payload["cfg_value"] = params["cfg_value"]
        if params.get("inference_timesteps") is not None:
            payload["inference_timesteps"] = params["inference_timesteps"]
        r = httpx.post(f"{WORKER_URL}/generate", json=payload, timeout=180.0)
        r.raise_for_status()
        return r.content, 0  # sample rate is embedded in the returned WAV
