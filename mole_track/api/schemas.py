from typing import Literal

from pydantic import BaseModel, Field


class Point(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0, description="Normalized X coordinate (0..1)")
    y: float = Field(..., ge=0.0, le=1.0, description="Normalized Y coordinate (0..1)")


class CalibrateRequest(BaseModel):
    points: list[Point] = Field(..., min_length=2, max_length=12)


class ActionResponse(BaseModel):
    success: bool
    message: str


class StatusResponse(BaseModel):
    detector_state: str
    triggered: bool
    displacement_value: float
    displacement_mode: str
    displacement_mean: float
    displacement_max: float
    displacement_total: float
    point_displacements: list[float]
    current_points: list[dict]   # [{x: float, y: float}, ...]
    active_points: int
    total_points: int
    camera_running: bool
    windowed_mean: float
    windowed_max: float
    windowed_total: float
    windowed_per_point: list[float]
    cumulative_mean: float
    cumulative_max: float
    cumulative_total: float
    cumulative_per_point: list[float]
    odometer_mean: float
    odometer_max: float
    odometer_total: float
    odometer_per_point: list[float]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    camera: bool
    detector_state: str


class SettingsRequest(BaseModel):
    detection_mode: Literal[
        "mean", "max", "total",
        "windowed_mean", "windowed_max", "windowed_total",
        "cumulative_mean", "cumulative_max", "cumulative_total",
        "odometer_mean", "odometer_max", "odometer_total",
    ] | None = None
    displacement_threshold: float | None = Field(default=None, gt=0.0)
    detection_debounce: int | None = Field(default=None, ge=1, le=20)
    accumulation_window: int | None = Field(default=None, ge=2, le=60)


class SettingsResponse(BaseModel):
    detection_mode: str
    displacement_threshold: float
    detection_debounce: int
    accumulation_window: int
