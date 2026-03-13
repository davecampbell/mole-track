import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from mole_track.api.schemas import (
    ActionResponse,
    CalibrateRequest,
    HealthResponse,
    SettingsRequest,
    SettingsResponse,
    StatusResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _camera(request: Request):
    return request.app.state.camera


def _detector(request: Request):
    return request.app.state.detector


def _settings(request: Request):
    return request.app.state.settings


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    cam = _camera(request)
    det = _detector(request)
    return HealthResponse(
        status="ok" if cam.is_running else "degraded",
        camera=cam.is_running,
        detector_state=det.get_state().value,
    )


def _mjpeg_response(loop, frame_buffer) -> StreamingResponse:
    """Return a StreamingResponse that serves frames from *frame_buffer* as MJPEG."""
    async def frame_generator():
        while True:
            # Offload blocking Condition.wait() to thread pool so the
            # asyncio event loop remains free for concurrent requests.
            frame = await loop.run_in_executor(None, frame_buffer.wait_for_frame)
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + frame
                + b"\r\n"
            )

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/stream.mjpeg")
async def mjpeg_stream(request: Request):
    """Live color camera stream (main 640×480 output from JpegEncoder)."""
    cam = _camera(request)
    if not cam.is_running:
        raise HTTPException(503, "Camera not running")
    return _mjpeg_response(asyncio.get_running_loop(), cam.jpeg_buffer)


@router.get("/stream-gray.mjpeg")
async def mjpeg_gray_stream(request: Request):
    """CLAHE-normalised grayscale stream — the exact frames used by the detector."""
    cam = _camera(request)
    if not cam.is_running:
        raise HTTPException(503, "Camera not running")
    return _mjpeg_response(asyncio.get_running_loop(), cam.gray_jpeg_buffer)


@router.post("/calibrate", response_model=ActionResponse)
async def calibrate(request: Request, body: CalibrateRequest):
    det = _detector(request)
    pts = [(p.x, p.y) for p in body.points]
    det.set_keypoints(pts)
    return ActionResponse(success=True, message=f"Calibrated with {len(pts)} points")


@router.post("/detection/start", response_model=ActionResponse)
async def start_detection(request: Request):
    det = _detector(request)
    ok = det.start()
    if not ok:
        raise HTTPException(400, f"Cannot start from state: {det.get_state().value}")
    return ActionResponse(success=True, message="Detection started")


@router.post("/detection/stop", response_model=ActionResponse)
async def stop_detection(request: Request):
    det = _detector(request)
    det.stop()
    return ActionResponse(success=True, message="Detection stopped")


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request):
    cam = _camera(request)
    det = _detector(request)
    cfg = _settings(request)
    event = det._last_event
    return StatusResponse(
        detector_state=det.get_state().value,
        triggered=event.triggered if event else False,
        displacement_value=event.displacement_value if event else 0.0,
        displacement_mode=event.displacement_mode if event else cfg.detection_mode,
        displacement_mean=event.displacement_mean if event else 0.0,
        displacement_max=event.displacement_max if event else 0.0,
        displacement_total=event.displacement_total if event else 0.0,
        point_displacements=event.point_displacements if event else [],
        current_points=[{"x": x, "y": y} for x, y in event.current_points] if event else [],
        active_points=event.active_points if event else 0,
        total_points=event.total_points if event else 0,
        camera_running=cam.is_running,
        windowed_mean=event.windowed_mean if event else 0.0,
        windowed_max=event.windowed_max if event else 0.0,
        windowed_total=event.windowed_total if event else 0.0,
        windowed_per_point=event.windowed_per_point if event else [],
        cumulative_mean=event.cumulative_mean if event else 0.0,
        cumulative_max=event.cumulative_max if event else 0.0,
        cumulative_total=event.cumulative_total if event else 0.0,
        cumulative_per_point=event.cumulative_per_point if event else [],
        odometer_mean=event.odometer_mean if event else 0.0,
        odometer_max=event.odometer_max if event else 0.0,
        odometer_total=event.odometer_total if event else 0.0,
        odometer_per_point=event.odometer_per_point if event else [],
    )


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request):
    cfg = _settings(request)
    return SettingsResponse(
        detection_mode=cfg.detection_mode,
        displacement_threshold=cfg.displacement_threshold,
        detection_debounce=cfg.detection_debounce,
        accumulation_window=cfg.accumulation_window,
    )


@router.post("/settings", response_model=SettingsResponse)
async def update_settings(request: Request, body: SettingsRequest):
    cfg = _settings(request)
    if body.detection_mode is not None:
        cfg.detection_mode = body.detection_mode
        logger.info("Detection mode → %s", cfg.detection_mode)
    if body.displacement_threshold is not None:
        cfg.displacement_threshold = body.displacement_threshold
        logger.info("Displacement threshold → %.1f", cfg.displacement_threshold)
    if body.detection_debounce is not None:
        cfg.detection_debounce = body.detection_debounce
        logger.info("Detection debounce → %d", cfg.detection_debounce)
    if body.accumulation_window is not None:
        cfg.accumulation_window = body.accumulation_window
        logger.info("Accumulation window → %d", cfg.accumulation_window)
    return SettingsResponse(
        detection_mode=cfg.detection_mode,
        displacement_threshold=cfg.displacement_threshold,
        detection_debounce=cfg.detection_debounce,
        accumulation_window=cfg.accumulation_window,
    )
