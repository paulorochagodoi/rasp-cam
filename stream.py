"""Simple Raspberry Pi camera streaming server.

Streaming strategy (in priority order):
  1. WebRTC  — P2P via /ws/webrtc WebSocket (~100-400 ms latency)
  2. MJPEG   — HTTP multipart stream at /video_feed (universal fallback)

The browser tries WebRTC first; if ICE negotiation or video playback fails
within the timeout it transparently switches to the MJPEG endpoint.
"""
from __future__ import annotations
import asyncio
import json
import threading
import time

from flask import Flask, Response, send_from_directory
from flask_sock import Sock

from camera import CameraStream
from webrtc_peer import WebRTCPeer, _AIORTC_AVAILABLE

app = Flask(__name__, static_folder="web")
sock = Sock(app)

camera = CameraStream(width=640, height=480, framerate=24)
camera.start()

# Dedicated asyncio event loop — runs in a background thread so all
# WebRTC peers share one loop without blocking Flask worker threads.
_loop = asyncio.new_event_loop()
threading.Thread(
    target=_loop.run_forever, daemon=True, name="webrtc-loop"
).start()


# ---------------------------------------------------------------------------
# Static / UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory("web", "index.html")


# ---------------------------------------------------------------------------
# MJPEG fallback
# ---------------------------------------------------------------------------

@app.route("/video_feed")
def video_feed():
    """MJPEG multipart stream — works in every browser, no JS required."""
    return Response(
        _mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


def _mjpeg_generator():
    interval = 1.0 / camera.framerate
    while True:
        t0 = time.monotonic()
        jpeg = camera.get_jpeg()
        if jpeg:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
        sleep = interval - (time.monotonic() - t0)
        if sleep > 0:
            time.sleep(sleep)


# ---------------------------------------------------------------------------
# WebRTC signaling (P2P)
# ---------------------------------------------------------------------------

@sock.route("/ws/webrtc")
def ws_webrtc(ws):
    """WebSocket endpoint for WebRTC offer/answer signaling."""
    if not _AIORTC_AVAILABLE:
        ws.close(message="WebRTC unavailable: install aiortc and av")
        return

    incoming: asyncio.Queue = asyncio.Queue()
    outgoing: asyncio.Queue = asyncio.Queue()

    def _sender():
        """Forward outgoing WebRTC messages to the browser over the WebSocket."""
        while True:
            try:
                fut = asyncio.run_coroutine_threadsafe(outgoing.get(), _loop)
                msg = fut.result(timeout=10)
                if msg is None:  # sentinel — peer closed
                    break
                ws.send(json.dumps(msg))
            except Exception:
                break

    sender_t = threading.Thread(target=_sender, daemon=True)
    sender_t.start()

    session_fut = asyncio.run_coroutine_threadsafe(
        _webrtc_session(incoming, outgoing), _loop
    )

    try:
        while True:
            data = ws.receive(timeout=30)
            if data is None:
                break
            _loop.call_soon_threadsafe(incoming.put_nowait, json.loads(data))
    finally:
        _loop.call_soon_threadsafe(outgoing.put_nowait, None)
        session_fut.cancel()
        sender_t.join(timeout=2)


async def _webrtc_session(
    incoming: asyncio.Queue, outgoing: asyncio.Queue
) -> None:
    """Async coroutine that owns one WebRTCPeer for the duration of a session."""
    peer = WebRTCPeer(camera, outgoing)
    try:
        while True:
            msg = await incoming.get()
            if msg is None:
                break
            await peer.handle(msg)
    finally:
        await peer.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
