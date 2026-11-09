"""Virtual Nota device — a Python stand-in for the ESP32 in the demo.

Why it exists: Wokwi has no INMP441 microphone model and no way to feed real
speech into the firmware, so the firmware build proves the code compiles and the
button/LED state machine works, while this script proves the *device contract*
end to end: machine code -> binding -> quota -> job -> agent -> result, and the
WebSocket push channel the real device keeps open.

It plays the same five states the firmware shows on its LED:

    idle -> recording -> uploading -> thinking -> done | error | quota

Usage:
    python -m device.sim.virtual_device --base http://127.0.0.1:8787
    python -m device.sim.virtual_device --text "明天下午三点提醒我给张总发报价单"
    python -m device.sim.virtual_device --wav sample.wav
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path
from typing import Any

import httpx

LED = {
    "idle": "○ 待机",
    "recording": "● 录音",
    "uploading": "◍ 上传",
    "thinking": "◎ 思考",
    "done": "◉ 完成",
    "error": "✕ 失败",
    "quota": "◭ 额度用完",
}

STATE_MAP = {
    "queued": "thinking",
    "transcribing": "thinking",
    "agent_running": "thinking",
    "done": "done",
    "failed": "error",
}


class VirtualDevice:
    def __init__(self, base: str, machine_code: str, *, tz: str = "Asia/Shanghai"):
        self.base = base.rstrip("/")
        self.machine_code = machine_code
        self.tz = tz
        # trust_env=False: a machine-wide HTTP proxy must not intercept loopback
        # traffic, which otherwise shows up as a confusing 502.
        self.client = httpx.Client(base_url=self.base, timeout=30.0, trust_env=False)
        self.boot_id = int(time.time()) % 100000
        self._press = 0

    # ------------------------------------------------------------------ led
    def led(self, state: str, detail: str = "") -> None:
        print(f"  [{LED.get(state, state)}] {detail}".rstrip())

    def idempotency_key(self) -> str:
        self._press += 1
        return f"{self.machine_code}-{self.boot_id}-{self._press}"

    # --------------------------------------------------------------- actions
    def status(self) -> dict[str, Any]:
        res = self.client.get(f"/api/device/{self.machine_code}/status")
        if res.status_code == 404:
            # A factory-fresh device is unknown until it says something once.
            return {"machine_code": self.machine_code, "user": None, "quota": None,
                    "recent": [], "paired": False}
        res.raise_for_status()
        return res.json()

    def press_and_speak(self, text: str) -> dict[str, Any]:
        """One button press carrying typed text (no mic in the simulator)."""
        self.led("recording", f"按下说话：{text}")
        time.sleep(0.3)
        self.led("uploading", "打包 WAV / 提交")
        res = self.client.post("/api/device/command", json={
            "machine_code": self.machine_code,
            "text": text,
            "idempotency_key": self.idempotency_key(),
        })
        payload = res.json()
        if not payload.get("ok"):
            self.led("quota", json.dumps(payload, ensure_ascii=False))
            return payload
        self.led("thinking", f"job={payload['job_id']}")
        return self.wait_for(payload["job_id"])

    def press_and_send_wav(self, wav_path: Path) -> dict[str, Any]:
        """One press carrying a real recording, exactly like the firmware."""
        self.led("recording", f"读取 {wav_path.name}")
        self.led("uploading", "POST /api/notes/audio")
        with wav_path.open("rb") as fh:
            res = self.client.post(
                "/api/notes/audio",
                files={"file": (wav_path.name, fh, "audio/wav")},
                data={"idempotency_key": self.idempotency_key()},
                headers={"X-User-Id": "demo"},
            )
        payload = res.json()
        if res.status_code == 429:
            self.led("quota", json.dumps(payload, ensure_ascii=False))
            return payload
        res.raise_for_status()
        self.led("thinking", f"job={payload['job_id']}")
        return self.wait_for(payload["job_id"])

    def wait_for(self, job_id: str) -> dict[str, Any]:
        last = None
        for _ in range(240):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            state = job.get("state")
            if state != last:
                self.led(STATE_MAP.get(state, "thinking"), f"state={state}")
                last = state
            if state in ("done", "failed"):
                return job
            time.sleep(0.25)
        return {"state": "timeout", "job_id": job_id}

    # ---------------------------------------------------------------- stream
    async def stream(self, seconds: float = 20.0) -> None:
        """Keep the device socket open, print heartbeat + due reminders."""
        import websockets

        url = self.base.replace("http://", "ws://").replace("https://", "wss://")
        url += f"/ws/device?machine_code={self.machine_code}"
        print(f"  connecting {url}")
        async with websockets.connect(url) as ws:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=seconds)
                except asyncio.TimeoutError:
                    break
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "heartbeat":
                    due = message.get("due") or []
                    print(f"  [♡] heartbeat ts={message.get('ts')} due={len(due)}")
                    for r in due:
                        self.led("done", f"该提醒了：{r['title']} @ {r['fire_at']}")
                else:
                    print(f"  [↔] {message}")


def make_demo_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> Path:
    """A 440 Hz tone — enough to exercise the upload path without a microphone."""
    import math
    import struct

    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(frames))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Nota virtual hardware")
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--machine-code", default="A1B2C3D4E5F6")
    parser.add_argument("--text", default="", help="what the user says")
    parser.add_argument("--wav", default="", help="send this wav file instead")
    parser.add_argument("--stream", type=float, default=0.0, help="listen on the socket N seconds")
    args = parser.parse_args()

    device = VirtualDevice(args.base, args.machine_code)
    print(f"Nota virtual device  machine_code={args.machine_code}  base={args.base}")

    try:
        before = device.status()
    except httpx.HTTPError as exc:
        print(f"  backend unreachable: {exc}")
        return 2

    quota = before.get("quota") or {}
    if before.get("paired") is False:
        print("  未配对：本次开机首次说话时会自动绑定到 demo 用户")
    else:
        print(f"  bound user={before.get('user')}  quota={quota.get('used')}/{quota.get('limit')}")

    if args.wav:
        job = device.press_and_send_wav(Path(args.wav))
    else:
        text = args.text or "明天下午三点提醒我给张总发报价单"
        job = device.press_and_speak(text)

    if job.get("state") == "done":
        device.led("done", job.get("summary", "")[:70].replace("\n", " "))
        for todo in job.get("todos") or []:
            print(f"      待办：{todo['title']} {todo.get('due_at') or ''}")
        for rem in job.get("reminders") or []:
            print(f"      提醒：{rem['title']} @ {rem['fire_at']}")
    else:
        device.led("error", str(job.get("error") or job))

    after = device.status()
    print(f"  quota now {(after.get('quota') or {}).get('used')}/"
          f"{(after.get('quota') or {}).get('limit')}")

    if args.stream:
        asyncio.run(device.stream(args.stream))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
