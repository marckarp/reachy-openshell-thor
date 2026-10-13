"""Trusted speaker service: plays one of a few built-in Reachy sounds through the robot's speaker.

Runs on the Thor (outside the sandbox) with robot/venv. The sandbox reaches it as
host.openshell.internal:8042 and may only call:

  GET  /sounds          list of allowed names
  POST /play {"name": "confused"}

The host (voice.sh) may also call, outside any policy:

  POST /say  {"text": "Reachy has looked left."}    Kokoro TTS -> speaker (returns while playing)
  GET  /busy                                         {"busy": true} while anything is playing

Only names in SOUNDS are accepted by /play; there is no path, device, or volume parameter.
Audio goes straight to the Reachy USB audio card over ALSA (the daemon's own audio
path does not work on this host).
"""

import subprocess
import tempfile
import threading
from pathlib import Path

import soundfile
from fastapi import FastAPI, HTTPException
from kokoro_onnx import Kokoro
from pydantic import BaseModel, Field

import reachy_mini

ASSETS = Path(reachy_mini.__file__).parent / "assets"      # Pollen's built-in sounds
OURS = Path(__file__).parent / "sounds"                      # synthesized here (robot/sounds/)
ALSA_DEVICE = "plughw:CARD=Audio,DEV=0"
SOUNDS = {
    "confused": ASSETS / "confused1.wav",
    "impatient": ASSETS / "impatient1.wav",
    "wake_up": ASSETS / "wake_up.wav",
    "sleep": ASSETS / "go_sleep.wav",
    "dance": ASSETS / "dance1.wav",
    "count": ASSETS / "count.wav",
    "no": OURS / "no.wav",      # two low tones, "uh-uh"
    "yes": OURS / "yes.wav",    # short rising chirp, "mm-hm"
    "love": OURS / "love.wav",  # three-note "I love you" melody
}

MODELS = Path(__file__).parent / "models"
VOICE = "af_sarah"

app = FastAPI(title="Reachy speaker (trusted host service)")
_lock = threading.Lock()
_tts: Kokoro | None = None


def tts() -> Kokoro:
    global _tts
    if _tts is None:
        _tts = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
    return _tts


class PlayRequest(BaseModel):
    name: str


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)


@app.get("/sounds")
def sounds() -> dict:
    return {"sounds": sorted(SOUNDS)}


def _aplay_then_release(file: Path) -> None:
    try:
        subprocess.run(["aplay", "-q", "-D", ALSA_DEVICE, str(file)], check=False, timeout=30)
    finally:
        _lock.release()


@app.post("/play")
def play(req: PlayRequest) -> dict:
    """Starts the sound and returns at once, so the robot can move while the dance tune plays."""
    file = SOUNDS.get(req.name)
    if file is None:
        raise HTTPException(400, f"unknown sound; allowed: {sorted(SOUNDS)}")
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "a sound is already playing")
    threading.Thread(target=_aplay_then_release, args=(file,), daemon=True).start()
    return {"status": "playing", "name": req.name}


@app.get("/busy")
def busy() -> dict:
    """True while a sound or speech is playing. listen.py waits on this before reopening the mic."""
    return {"busy": _lock.locked()}


def _aplay_tmp_then_release(path: str) -> None:
    try:
        subprocess.run(["aplay", "-q", "-D", ALSA_DEVICE, path], check=False, timeout=120)
    finally:
        Path(path).unlink(missing_ok=True)
        _lock.release()


@app.post("/say")
def say(req: SayRequest) -> dict:
    """Synthesizes now, plays in the background, returns at once so the robot can move while talking."""
    if not _lock.acquire(timeout=20):          # queue behind a sound that is still playing
        raise HTTPException(429, "speaker busy")
    try:
        samples, rate = tts().create(req.text, voice=VOICE, speed=1.0, lang="en-us")
        wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        soundfile.write(wav.name, samples, rate)
    except Exception as exc:  # noqa: BLE001
        _lock.release()
        raise HTTPException(502, f"tts error: {type(exc).__name__}") from exc
    threading.Thread(target=_aplay_tmp_then_release, args=(wav.name,), daemon=True).start()
    return {"status": "speaking", "chars": len(req.text), "seconds": round(len(samples) / rate, 1)}
