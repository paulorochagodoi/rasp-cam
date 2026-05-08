from __future__ import annotations
import io
import threading
from typing import Optional

import numpy as np


class CameraStream:
    """
    picamera2 wrapper that captures frames in a background thread and
    exposes the latest frame to multiple consumers (MJPEG, WebRTC).
    """

    def __init__(self, width: int = 640, height: int = 480, framerate: int = 24):
        from picamera2 import Picamera2

        self._cam = Picamera2()
        cfg = self._cam.create_video_configuration(
            main={"size": (width, height), "format": "RGB888"},
            controls={"FrameRate": float(framerate)},
        )
        self._cam.configure(cfg)
        self.width = width
        self.height = height
        self.framerate = framerate
        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> "CameraStream":
        self._cam.start()
        self._running = True
        threading.Thread(
            target=self._capture_loop, daemon=True, name="camera-capture"
        ).start()
        return self

    def _capture_loop(self) -> None:
        while self._running:
            arr = self._cam.capture_array()
            with self._lock:
                self._frame = arr

    def get_frame(self) -> Optional[np.ndarray]:
        """Return the latest captured frame as an RGB numpy array."""
        with self._lock:
            return self._frame

    def get_jpeg(self, quality: int = 85) -> Optional[bytes]:
        """Return the latest frame encoded as JPEG bytes."""
        from PIL import Image

        frame = self.get_frame()
        if frame is None:
            return None
        buf = io.BytesIO()
        Image.fromarray(frame).save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def stop(self) -> None:
        self._running = False
        self._cam.stop()
