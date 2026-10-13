"""Move Reachy's head through the daemon's REST API.

This is the same path the OpenShell sandbox uses: plain HTTP to the daemon on
port 8000. No Reachy SDK needed, only the standard library.

Run:  robot/venv/bin/python robot/move_demo.py
"""

import json
import math
import time
import urllib.request

DAEMON = "http://127.0.0.1:8000"

# The five fixed poses from the OpenShell demo (pitch, yaw) in radians.
POSES = {
    "up": (math.radians(-30), 0.0),
    "down": (math.radians(30), 0.0),
    "left": (0.0, math.radians(40)),
    "right": (0.0, math.radians(-40)),
    "front": (0.0, 0.0),
}


def post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        DAEMON + path, data=data, method="POST", headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read() or b"{}")


def get(path: str) -> dict:
    with urllib.request.urlopen(DAEMON + path, timeout=5) as resp:
        return json.loads(resp.read())


def look(direction: str, duration: float = 1.0) -> None:
    pitch, yaw = POSES[direction]
    move = post(
        "/api/move/goto",
        {
            "head_pose": {"x": 0, "y": 0, "z": 0, "roll": 0, "pitch": pitch, "yaw": yaw},
            "duration": duration,
            "interpolation": "minjerk",
        },
    )
    print(f"look {direction:5s} -> move {move['uuid']}")
    # Wait until the daemon reports that move as finished.
    while move["uuid"] in get("/api/move/running"):
        time.sleep(0.1)


if __name__ == "__main__":
    status = get("/api/daemon/status")
    print(f"daemon state={status['state']} version={status['version']}")
    for direction in ["up", "left", "right", "front"]:
        look(direction)
    print("done")
