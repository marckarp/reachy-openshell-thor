"""Trusted speaker service: plays one of a few built-in Reachy sounds through the robot's speaker.

Runs on the Thor (outside the sandbox) with robot/venv. The sandbox reaches it as
host.openshell.internal:8042 and may only call:

  GET  /sounds          list of allowed names
  POST /play {"name": "confused"}

Only names in SOUNDS are accepted; there is no path, device, or volume parameter.
Audio goes straight to the Reachy USB audio card over ALSA (the daemon's own audio
path does not work on this host).
"""

import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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
}

app = FastAPI(title="Reachy speaker (trusted host service)")
_lock = threading.Lock()


class PlayRequest(BaseModel):
    name: str


@app.get("/sounds")
def sounds() -> dict:
    return {"sounds": sorted(SOUNDS)}


@app.post("/play")
def play(req: PlayRequest) -> dict:
    file = SOUNDS.get(req.name)
    if file is None:
        raise HTTPException(400, f"unknown sound; allowed: {sorted(SOUNDS)}")
    if not _lock.acquire(blocking=False):
        raise HTTPException(429, "a sound is already playing")
    try:
        subprocess.run(["aplay", "-q", "-D", ALSA_DEVICE, str(file)], check=True, timeout=15)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, f"speaker error: {type(exc).__name__}") from exc
    finally:
        _lock.release()
    return {"status": "played", "name": req.name}
