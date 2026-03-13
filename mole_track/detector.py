import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class DetectorState(Enum):
    IDLE = "idle"
    CALIBRATED = "calibrated"
    RUNNING = "running"
    TRACKING_LOST = "tracking_lost"


@dataclass
class DetectionEvent:
    triggered: bool = False
    displacement_value: float = 0.0   # the active-mode value (used for threshold compare)
    displacement_mode: str = "max"
    displacement_mean: float = 0.0
    displacement_max: float = 0.0
    displacement_total: float = 0.0
    point_displacements: list[float] = field(default_factory=list)  # per-point, px
    current_points: list[tuple[float, float]] = field(default_factory=list)  # normalized 0..1
    active_points: int = 0
    total_points: int = 0
    state: str = DetectorState.IDLE.value
    # Windowed accumulation (sum of last N per-frame displacements per point)
    windowed_mean: float = 0.0
    windowed_max: float = 0.0
    windowed_total: float = 0.0
    windowed_per_point: list[float] = field(default_factory=list)
    # Cumulative displacement from the moment Start was pressed
    cumulative_mean: float = 0.0
    cumulative_max: float = 0.0
    cumulative_total: float = 0.0
    cumulative_per_point: list[float] = field(default_factory=list)
    # Odometer — total path length (sum of all frame-to-frame steps); monotonically increases
    odometer_mean: float = 0.0
    odometer_max: float = 0.0
    odometer_total: float = 0.0
    odometer_per_point: list[float] = field(default_factory=list)


class MoleDetector:
    """
    Lucas-Kanade sparse optical flow on user-calibrated key points.

    Lifecycle:
      1. set_keypoints(pts)  — store normalized points from calibration UI
      2. start()             — begin tracking loop in daemon thread
      3. stop()              — halt loop cleanly
      4. on_detection callback fires each frame with a DetectionEvent
    """

    LK_PARAMS = dict(
        winSize=(15, 15),
        maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03),
    )

    def __init__(self, settings, camera):
        self.settings = settings
        self.camera = camera

        self._keypoints: np.ndarray | None = None   # shape (N, 1, 2) float32
        self._current_pts: np.ndarray | None = None
        self._prev_gray: np.ndarray | None = None

        self._state = DetectorState.IDLE
        self._state_lock = threading.Lock()

        self._debounce_count = 0
        self._window_per_point: deque = deque()  # deque of list[float], one per processed frame
        self._start_pts: np.ndarray | None = None    # snapshot taken when start() is called
        self._odometer: np.ndarray | None = None     # accumulated path length per point
        self._last_event: DetectionEvent | None = None
        self._on_detection: Callable[[DetectionEvent], None] | None = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def set_keypoints(self, normalized_points: list[tuple[float, float]]) -> None:
        """
        Accept calibration points as normalized (0..1) fractions of the display image.
        Backend scales them to lores resolution for optical flow processing.
        """
        w = self.settings.lores_width
        h = self.settings.lores_height
        pts = np.array(
            [[[x * w, y * h]] for x, y in normalized_points],
            dtype=np.float32,
        )
        with self._state_lock:
            self._keypoints = pts
            self._current_pts = pts.copy()
            self._prev_gray = None
            self._debounce_count = 0
            self._window_per_point.clear()
            self._start_pts = None   # cleared; snapshotted fresh when start() is called
            self._odometer  = np.zeros(len(pts), dtype=np.float32)
            self._state = DetectorState.CALIBRATED
        logger.info("Calibrated with %d key points", len(pts))

    def set_detection_callback(self, cb: Callable[[DetectionEvent], None]) -> None:
        self._on_detection = cb

    def start(self) -> bool:
        with self._state_lock:
            if self._state not in (DetectorState.CALIBRATED, DetectorState.TRACKING_LOST):
                logger.warning("Cannot start from state: %s", self._state.value)
                return False
            self._state = DetectorState.RUNNING
            self._stop_event.clear()
            self._debounce_count = 0
            self._window_per_point.clear()
            # Snapshot reference positions — cumulative displacement is measured from here.
            self._start_pts = self._current_pts.copy() if self._current_pts is not None else None
            # Reset odometer so path length counts from this Start press.
            n = len(self._current_pts) if self._current_pts is not None else 0
            self._odometer = np.zeros(n, dtype=np.float32)

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mole-detector")
        self._thread.start()
        logger.info("Detector started")
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        with self._state_lock:
            if self._state == DetectorState.RUNNING:
                self._state = DetectorState.CALIBRATED
        logger.info("Detector stopped")

    def get_state(self) -> DetectorState:
        with self._state_lock:
            return self._state

    def _run_loop(self) -> None:
        frame_interval = 1.0 / self.settings.frame_rate
        try:
            while not self._stop_event.is_set():
                gray = self.camera.get_gray_frame()
                if gray is None:
                    time.sleep(0.05)
                    continue

                with self._state_lock:
                    if self._prev_gray is None:
                        self._prev_gray = gray
                        continue
                    event = self._compute_flow(self._prev_gray, gray)
                    self._prev_gray = gray

                self._last_event = event
                if self._on_detection:
                    self._on_detection(event)

                time.sleep(frame_interval)
        except Exception:
            logger.exception("Detector thread crashed — stopping")
            with self._state_lock:
                self._state = DetectorState.TRACKING_LOST

    def _compute_flow(self, prev: np.ndarray, curr: np.ndarray) -> DetectionEvent:
        """Core LK computation. Called with _state_lock held."""
        if self._current_pts is None or len(self._current_pts) == 0:
            self._state = DetectorState.TRACKING_LOST
            return DetectionEvent(state=DetectorState.TRACKING_LOST.value)

        next_pts, status, _ = cv2.calcOpticalFlowPyrLK(
            prev, curr, self._current_pts, None, **self.LK_PARAMS
        )

        # Restore original (N,1) boolean indexing — gives (M,2) shaped arrays.
        # A separate 1-D mask is used only for filtering _start_pts below.
        good_new = next_pts[status == 1]          # (M, 2)
        good_old = self._current_pts[status == 1] # (M, 2)
        mask     = status.flatten() == 1          # (N,)  — for _start_pts only
        active   = len(good_new)
        total    = len(self._current_pts)

        if active < total * self.settings.min_points_ratio:
            self._state = DetectorState.TRACKING_LOST
            self._window_per_point.clear()
            # Do NOT modify _start_pts or _current_pts — they stay in sync for recovery.
            logger.warning("Tracking lost: %d/%d points active", active, total)
            return DetectionEvent(
                state=DetectorState.TRACKING_LOST.value,
                active_points=active,
                total_points=total,
            )

        displacements = np.linalg.norm(good_new - good_old, axis=1)
        disp_mean  = float(np.mean(displacements))
        disp_max   = float(np.max(displacements))
        disp_total = float(np.sum(displacements))

        # ── Windowed accumulation ──────────────────────────────────────────
        max_window = self.settings.accumulation_window
        self._window_per_point.append([float(d) for d in displacements])
        while len(self._window_per_point) > max_window:
            self._window_per_point.popleft()

        n_pts = len(displacements)
        win_per_point = [0.0] * n_pts
        for frame_disps in self._window_per_point:
            for i in range(min(n_pts, len(frame_disps))):
                win_per_point[i] += frame_disps[i]

        win_mean  = float(np.mean(win_per_point)) if n_pts > 0 else 0.0
        win_max   = float(np.max(win_per_point))  if n_pts > 0 else 0.0
        win_total = float(np.sum(win_per_point))

        # ── Cumulative displacement from Start ────────────────────────────────
        # _start_pts and _current_pts are always the same length; filter both
        # by the same mask each frame so point correspondence is maintained.
        if self._start_pts is not None and len(self._start_pts) == len(self._current_pts):
            good_start = self._start_pts.reshape(-1, 2)[mask]
        else:
            # Fallback: use pre-frame positions (first frame or edge case)
            good_start = good_old

        cum_per_point = np.linalg.norm(good_new - good_start, axis=1)
        cum_mean  = float(np.mean(cum_per_point)) if n_pts > 0 else 0.0
        cum_max   = float(np.max(cum_per_point))  if n_pts > 0 else 0.0
        cum_total = float(np.sum(cum_per_point))

        # Keep _start_pts filtered to surviving points (same mask as _current_pts)
        if self._start_pts is not None and len(self._start_pts) == len(self._current_pts):
            self._start_pts = self._start_pts.reshape(-1, 2)[mask].reshape(-1, 1, 2)
        else:
            self._start_pts = good_start.reshape(-1, 1, 2)

        # ── Odometer — total path length, monotonically increasing ────────────
        # Each frame adds this frame's per-point step; never decreases on reversal.
        if self._odometer is not None and len(self._odometer) == total:
            odo = self._odometer[mask] + displacements
        else:
            odo = displacements.copy()   # fallback: start accumulating from now
        self._odometer = odo             # update (now length == active == n_pts)

        odo_mean  = float(np.mean(odo)) if n_pts > 0 else 0.0
        odo_max   = float(np.max(odo))  if n_pts > 0 else 0.0
        odo_total = float(np.sum(odo))

        mode = self.settings.detection_mode
        if mode == "max":
            disp_value = disp_max
        elif mode == "total":
            disp_value = disp_total
        elif mode == "windowed_max":
            disp_value = win_max
        elif mode == "windowed_mean":
            disp_value = win_mean
        elif mode == "windowed_total":
            disp_value = win_total
        elif mode == "cumulative_max":
            disp_value = cum_max
        elif mode == "cumulative_mean":
            disp_value = cum_mean
        elif mode == "cumulative_total":
            disp_value = cum_total
        elif mode == "odometer_max":
            disp_value = odo_max
        elif mode == "odometer_mean":
            disp_value = odo_mean
        elif mode == "odometer_total":
            disp_value = odo_total
        else:  # "mean"
            disp_value = disp_mean

        if disp_value >= self.settings.displacement_threshold:
            self._debounce_count += 1
        else:
            self._debounce_count = 0

        triggered = self._debounce_count >= self.settings.detection_debounce

        # Normalize current point positions to 0..1 for the frontend overlay
        w = self.settings.lores_width
        h = self.settings.lores_height
        norm_pts = [(float(pt[0] / w), float(pt[1] / h)) for pt in good_new.reshape(-1, 2)]

        # Re-anchor tracked points to their new positions each frame.
        # (cumulative reference _start_pts is NOT re-anchored — it stays fixed from Start)
        self._current_pts = good_new.reshape(-1, 1, 2)

        return DetectionEvent(
            triggered=triggered,
            displacement_value=disp_value,
            displacement_mode=mode,
            displacement_mean=disp_mean,
            displacement_max=disp_max,
            displacement_total=disp_total,
            point_displacements=[float(d) for d in displacements],
            current_points=norm_pts,
            active_points=active,
            total_points=total,
            state=DetectorState.RUNNING.value,
            windowed_mean=win_mean,
            windowed_max=win_max,
            windowed_total=win_total,
            windowed_per_point=win_per_point,
            cumulative_mean=cum_mean,
            cumulative_max=cum_max,
            cumulative_total=cum_total,
            cumulative_per_point=[float(d) for d in cum_per_point],
            odometer_mean=odo_mean,
            odometer_max=odo_max,
            odometer_total=odo_total,
            odometer_per_point=[float(d) for d in odo],
        )
