"""Always-on listening. Runs on the Thor (host), outside the sandbox.

  mic -> Silero VAD (ONNX, CPU) -> one WAV per utterance -> sandbox agent --audio
      -> Nemotron Omni decides: IGNORE (not addressed) or a tool call -> /say the reply

The model is the wake word: the agent's AUDIO_PROMPT tells it to reply IGNORE unless the
speaker addressed Reachy. Nothing is sent while the room is quiet. The mic is closed while
Reachy speaks so it does not hear itself.

  robot/venv/bin/python robot/listen.py
  robot/venv/bin/python robot/listen.py --vad-test some.wav     # print detected speech spans
"""

import base64
import collections
import io
import subprocess
import sys
import wave
from pathlib import Path

import httpx
import numpy as np
import onnxruntime as ort

MIC = "plughw:CARD=Audio,DEV=0"
RATE = 16000
CHUNK = 512                      # 32 ms, the window Silero expects at 16 kHz
MODEL = Path(__file__).parent / "models" / "silero_vad.onnx"
SPEAKER = "http://127.0.0.1:8042"
AGENT = ["openshell", "sandbox", "exec", "--name", "reachy-agent", "--no-tty", "--",
         "/opt/venv/bin/python", "/sandbox/minimal_agent.py", "--audio"]

START_PROB, END_PROB = 0.5, 0.35     # speech starts above START, counts as silence below END
PRE_ROLL_S, END_SILENCE_S = 0.3, 0.7
MIN_UTT_S, MAX_UTT_S = 0.6, 12.0


class VAD:
    def __init__(self) -> None:
        self.session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(64, dtype=np.float32)   # Silero v5 wants the last 64 samples prepended

    def __call__(self, pcm: np.ndarray) -> float:
        x = pcm.astype(np.float32) / 32768.0
        inp = np.concatenate([self.context, x])[None, :]
        self.context = x[-64:]
        out, self.state = self.session.run(
            None, {"input": inp, "state": self.state, "sr": np.array(RATE, dtype=np.int64)})
        return float(out[0, 0])


def chunks_from(stream) -> "iter[np.ndarray]":
    while True:
        raw = stream.read(CHUNK * 2)
        if len(raw) < CHUNK * 2:
            return
        yield np.frombuffer(raw, dtype=np.int16)


def capture_utterance(stream, vad: VAD) -> np.ndarray | None:
    """Block until one complete utterance has been heard; return its PCM."""
    pre = collections.deque(maxlen=int(PRE_ROLL_S * RATE / CHUNK))
    buf: list[np.ndarray] = []
    silent = 0
    for pcm in chunks_from(stream):
        prob = vad(pcm)
        if not buf:
            pre.append(pcm)
            if prob > START_PROB:
                buf = list(pre)
                silent = 0
                print("hearing...", end="\r", flush=True)
            continue
        buf.append(pcm)
        silent = silent + 1 if prob < END_PROB else 0
        if silent * CHUNK / RATE >= END_SILENCE_S or len(buf) * CHUNK / RATE >= MAX_UTT_S:
            audio = np.concatenate(buf)
            buf, pre = [], collections.deque(maxlen=pre.maxlen)
            vad.reset()
            if len(audio) / RATE - END_SILENCE_S >= MIN_UTT_S:
                return audio
            print("            ", end="\r")
    return None


def to_wav(pcm: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def turn(pcm: np.ndarray) -> None:
    print(f"heard {len(pcm) / RATE:.1f} s -> asking Reachy")
    b64 = base64.b64encode(to_wav(pcm)).decode()
    res = subprocess.run(AGENT, input=b64, capture_output=True, text=True, timeout=120)
    reply, already_spoke = "", False
    for line in (res.stdout + res.stderr).splitlines():
        if line.startswith(("[model chose]", "[robot said]", "[reply]", "[busy]")) or "Error" in line:
            print("  " + line)
        if line.startswith("[model chose] speak("):
            already_spoke = True
        if line.startswith("[reply]"):
            reply = line[len("[reply]"):].strip()
    if not reply or reply.upper().lstrip("\"'").startswith("IGNORE"):
        print("  (not for me)")
        return
    if already_spoke or (reply.startswith("(") and reply.endswith(")")):   # spoke via tool, or "(done)"
        return
    try:
        httpx.post(f"{SPEAKER}/say", json={"text": reply[:300]}, timeout=60)
    except httpx.HTTPError as exc:
        print(f"  speaker error: {type(exc).__name__}")


def listen() -> None:
    vad = VAD()
    print("Listening. Say 'Reachy, look left'. Ctrl-C to stop.")
    while True:
        rec = subprocess.Popen(["arecord", "-q", "-D", MIC, "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                               stdout=subprocess.PIPE)
        try:
            pcm = capture_utterance(rec.stdout, vad)
        finally:
            rec.kill()
            rec.wait()
        if pcm is None:
            print("mic stream ended; retrying")
            continue
        turn(pcm)          # mic is closed here, so Reachy does not hear itself


def vad_test(path: str) -> None:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, "need 16 kHz mono"
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    vad = VAD()
    probs = [vad(pcm[i:i + CHUNK]) for i in range(0, len(pcm) - CHUNK + 1, CHUNK)]
    t = lambda i: i * CHUNK / RATE  # noqa: E731
    spans, start = [], None
    for i, p in enumerate(probs):
        if start is None and p > START_PROB:
            start = i
        elif start is not None and p < END_PROB:
            spans.append((t(start), t(i)))
            start = None
    if start is not None:
        spans.append((t(start), t(len(probs))))
    print(f"{path}: {len(pcm) / RATE:.1f} s, max prob {max(probs):.2f}")
    for a, b in spans:
        print(f"  speech {a:5.2f}s -> {b:5.2f}s")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--vad-test"]:
        vad_test(sys.argv[2])
    else:
        try:
            listen()
        except KeyboardInterrupt:
            print("\nbye")
