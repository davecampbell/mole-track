import io
import logging
import threading

import cv2
import numpy as np

logger = logging.getLogger(__name__)

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
    PICAMERA_AVAILABLE = True
except ImportError:
    PICAMERA_AVAILABLE = False
    logger.warning("picamera2 not available — camera disabled (dev machine mode)")


class FrameBuffer(io.BufferedIOBase):
    """Thread-safe JPEG frame buffer. Written by JpegEncoder, read by MJPEG streamer."""

    def __init__(self):
        self.frame: bytes | None = None
        self.condition = threading.Condition()

    def write(self, buf: bytes) -> int:
        with self.condition:
            self.frame = buf
            self.condition.notify_all()
        return len(buf)

    def wait_for_frame(self) -> bytes:
        """Block until a new frame is available. Safe to call from a thread pool."""
        with self.condition:
            self.condition.wait()
            return self.frame


class CameraManager:
    """
    Owns the Picamera2 instance. Exposes:
      - jpeg_buffer:      FrameBuffer  (color MJPEG stream)
      - gray_jpeg_buffer: FrameBuffer  (CLAHE-normalized gray MJPEG stream)
      - get_gray_frame()               (normalized 320x240 grayscale for optical flow)
    """

    def __init__(self, settings):
        self.settings = settings
        self.jpeg_buffer      = FrameBuffer()   # color stream (JpegEncoder output)
        self.gray_jpeg_buffer = FrameBuffer()   # normalized gray debug stream
        self._latest_gray: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._clahe  = None
        self._picam2 = None
        self._running = False

    def start(self) -> None:
        if not PICAMERA_AVAILABLE:
            logger.warning("Skipping camera start — picamera2 not available")
            return

        # CLAHE: normalize local contrast to make LK robust to illumination changes.
        # clipLimit=2.0 prevents over-amplifying noise; 8x8 tiles balance local/global.
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        self._picam2 = Picamera2()
        config = self._picam2.create_video_configuration(
            main={
                "size": (self.settings.capture_width, self.settings.capture_height),
                "format": "RGB888",
            },
            lores={
                "size": (self.settings.lores_width, self.settings.lores_height),
                "format": "YUV420",
            },
            controls={"FrameRate": self.settings.frame_rate},
        )
        self._picam2.configure(config)
        self._picam2.post_callback = self._on_frame
        self._picam2.start_recording(JpegEncoder(), FileOutput(self.jpeg_buffer))
        self._running = True
        logger.info(
            "Camera started — main=%dx%d lores=%dx%d @%.1ffps",
            self.settings.capture_width,
            self.settings.capture_height,
            self.settings.lores_width,
            self.settings.lores_height,
            self.settings.frame_rate,
        )

    def _on_frame(self, request) -> None:
        """
        Called from picamera2's internal thread after each frame.
        1. Extracts the Y-plane from the lores YUV420 buffer (grayscale, no conversion needed).
        2. Applies CLAHE normalisation — makes LK tracking robust to lighting changes.
        3. Stores the normalised frame for the detector (get_gray_frame).
        4. JPEG-encodes the normalised frame into gray_jpeg_buffer for the debug stream.
        """
        try:
            yuv  = request.make_array("lores")
            gray = yuv[: self.settings.lores_height, :]   # Y plane, no-copy slice

            # Apply CLAHE — cheap (~1ms on Pi Zero for 320x240)
            gray_norm = self._clahe.apply(gray)

            with self._frame_lock:
                self._latest_gray = gray_norm.copy()

            # JPEG-encode normalised gray for the optional debug stream.
            # Quality 75 is fine for a diagnostic view; imencode is fast on Pi Zero.
            ok, jpeg_buf = cv2.imencode(".jpg", gray_norm, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                self.gray_jpeg_buffer.write(jpeg_buf.tobytes())

        except Exception as exc:
            logger.debug("Frame callback error: %s", exc)

    def get_gray_frame(self) -> np.ndarray | None:
        """Return the latest CLAHE-normalised grayscale frame (for optical flow)."""
        with self._frame_lock:
            return self._latest_gray.copy() if self._latest_gray is not None else None

    def stop(self) -> None:
        if self._picam2 and self._running:
            self._picam2.stop_recording()
            self._running = False
            logger.info("Camera stopped")

    @property
    def is_running(self) -> bool:
        return self._running
