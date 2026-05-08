from __future__ import annotations
import asyncio
import fractions
import time
from typing import Optional, Set

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

# Keep references so GC doesn't collect active peer connections
_pcs: Set["RTCPeerConnection"] = set()

# ---------------------------------------------------------------------------
# Video track — feeds picamera2 frames into aiortc
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

            await asyncio.sleep(1 / self._camera.framerate)
            return video_frame


# ---------------------------------------------------------------------------
# Offer handler — called once per browser tab via POST /offer
# ---------------------------------------------------------------------------

async def handle_offer(camera, sdp: str) -> str:
    """
    Process a WebRTC SDP offer and return an SDP answer.

    Signaling flow (no WebSocket needed):
      browser  POST /offer  {sdp}  →  this coroutine
      browser  ←  {type:"answer", sdp}  ←  HTTP response

    ICE candidates are embedded in both SDPs (full-trickle disabled),
    so no additional signaling channel is required.
    """
    if not _AIORTC_AVAILABLE:
        raise RuntimeError("aiortc not installed — run: pip install aiortc av")

    pc = RTCPeerConnection(
        configuration=RTCConfiguration(
            iceServers=[RTCIceServer(urls=[STUN_SERVER])]
        )
    )
    _pcs.add(pc)
    pc.addTrack(_CameraVideoTrack(camera))  # type: ignore[name-defined]

    @pc.on("connectionstatechange")
    async def _cleanup() -> None:
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            _pcs.discard(pc)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Wait for ICE gathering (aiortc gathers asynchronously)
    deadline = time.monotonic() + 5.0
    while (
        pc.iceGatheringState != "complete"
        and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.1)

    return pc.localDescription.sdp
