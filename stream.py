"""Simple Raspberry Pi camera streaming server.

Streaming strategy (in priority order):
  1. WebRTC  — P2P via POST /offer + RTCPeerConnection (~100-400 ms latency)
  2. MJPEG   — HTTP multipart stream at /video_feed (universal fallback)

The browser tries WebRTC first; if ICE negotiation or video playback fails
within the timeout it transparently switches to MJPEG.

No WebSocket library is required — WebRTC signaling uses a plain HTTP POST.
"""
from __future__ import annotations
import asyncio
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

from camera import CameraStream
from webrtc_peer import handle_offer, _AIORTC_AVAILABLE

app = Flask(__name__, static_folder="web")

camera = CameraStream(width=640, height=480, framerate=24)
camera.start()

# Dedicated asyncio loop for WebRTC peers — runs in a background thread so
# it never blocks Flask worker threads.
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True, name="webrtc-loop").start()


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
# WebRTC signaling — plain HTTP POST, no WebSocket library needed
# ---------------------------------------------------------------------------

@app.route("/offer", methods=["POST"])
def webrtc_offer():
    """
    Receive a WebRTC SDP offer and return an SDP answer.

    The browser waits for ICE gathering to complete before POSTing, so
    the offer already contains all ICE candidates.  The server embeds
    its own candidates in the answer before responding.
    """
    if not _AIORTC_AVAILABLE:
        return jsonify({"error": "WebRTC unavailable: install aiortc and av"}), 503

    data = request.get_json(force=True)
    if not data or "sdp" not in data:
        return jsonify({"error": "Missing sdp field"}), 400

    try:
        future = asyncio.run_coroutine_threadsafe(
            handle_offer(camera, data["sdp"]), _loop
        )
        answer_sdp = future.result(timeout=15)
        return jsonify({"type": "answer", "sdp": answer_sdp})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
