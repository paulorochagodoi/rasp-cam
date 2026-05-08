from __future__ import annotations
import asyncio
import fractions
import time
from typing import Optional

try:
    import av
    from aiortc import (
        RTCPeerConnection,
        RTCSessionDescription,
        RTCConfiguration,
        RTCIceServer,
        MediaStreamTrack,
    )
    _AIORTC_AVAILABLE = True
except ImportError:
    _AIORTC_AVAILABLE = False
    MediaStreamTrack = object  # type: ignore[assignment,misc]

STUN_SERVER = "stun:stun.l.google.com:19302"

# ---------------------------------------------------------------------------
# Video track — wraps picamera2 frames for aiortc
# ---------------------------------------------------------------------------

if _AIORTC_AVAILABLE:
    class _CameraVideoTrack(MediaStreamTrack):  # type: ignore[valid-type]
        kind = "video"

        def __init__(self, camera) -> None:
            super().__init__()
            self._camera = camera
            self._pts: int = 0
            self._time_base = fractions.Fraction(1, 90000)
            self._last_ts: Optional[float] = None

        async def recv(self):  # type: ignore[override]
            loop = asyncio.get_event_loop()
            # run blocking capture_array in a thread pool
            frame_arr = await loop.run_in_executor(None, self._camera.get_frame)
            if frame_arr is None:
                await asyncio.sleep(0.05)
                return await self.recv()

            video_frame = av.VideoFrame.from_ndarray(frame_arr, format="rgb24")

            now = time.monotonic()
            if self._last_ts is None:
                self._last_ts = now
            elapsed = now - self._last_ts
            self._pts += int(elapsed * 90000)
            self._last_ts = now

            video_frame.pts = self._pts
            video_frame.time_base = self._time_base

            # throttle to the configured framerate
            await asyncio.sleep(1 / self._camera.framerate)
            return video_frame


# ---------------------------------------------------------------------------
# Peer — manages one RTCPeerConnection per browser tab
# ---------------------------------------------------------------------------

class WebRTCPeer:
    """
    Manages one WebRTC peer connection.

    Signaling flow (browser is always the offerer):
      browser  →  offer (SDP, full ICE)  →  handle()
      webrtcbin creates answer            →  outgoing queue
      browser  ←  answer (SDP, full ICE) ←  sender thread
    """

    def __init__(self, camera, outgoing: asyncio.Queue) -> None:
        if not _AIORTC_AVAILABLE:
            raise RuntimeError(
                "aiortc not installed. Run: pip install aiortc av"
            )
        self._pc = RTCPeerConnection(
            configuration=RTCConfiguration(
                iceServers=[RTCIceServer(urls=[STUN_SERVER])]
            )
        )
        self._pc.addTrack(_CameraVideoTrack(camera))  # type: ignore[name-defined]
        self._outgoing = outgoing

        @self._pc.on("connectionstatechange")
        async def _on_state() -> None:
            if self._pc.connectionState in ("failed", "closed"):
                # signal the sender thread to stop
                await self._outgoing.put(None)

    async def handle(self, msg: dict) -> None:
        msg_type = msg.get("type")

        if msg_type == "offer":
            await self._pc.setRemoteDescription(
                RTCSessionDescription(sdp=msg["sdp"], type="offer")
            )
            answer = await self._pc.createAnswer()
            await self._pc.setLocalDescription(answer)

            # wait for ICE gathering to complete (aiortc gathers asynchronously)
            deadline = time.monotonic() + 5.0
            while (
                self._pc.iceGatheringState != "complete"
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.1)

            await self._outgoing.put(
                {"type": "answer", "sdp": self._pc.localDescription.sdp}
            )

    async def close(self) -> None:
        await self._pc.close()
