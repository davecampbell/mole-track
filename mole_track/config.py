import os
from dataclasses import dataclass


@dataclass
class Settings:
    # Camera
    capture_width: int = int(os.getenv("CAPTURE_WIDTH", 640))
    capture_height: int = int(os.getenv("CAPTURE_HEIGHT", 480))
    lores_width: int = int(os.getenv("LORES_WIDTH", 320))
    lores_height: int = int(os.getenv("LORES_HEIGHT", 240))
    frame_rate: float = float(os.getenv("FRAME_RATE", 3.0))

    # Detection
    detection_mode: str = os.getenv("DETECTION_MODE", "max")  # "mean"|"max"|"total"|"windowed_*"
    displacement_threshold: float = float(os.getenv("DISPLACEMENT_THRESHOLD", 8.0))
    detection_debounce: int = int(os.getenv("DETECTION_DEBOUNCE", 3))
    min_points_ratio: float = float(os.getenv("MIN_POINTS_RATIO", 0.5))
    accumulation_window: int = int(os.getenv("ACCUMULATION_WINDOW", 10))  # frames for windowed modes

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", 8000))
    log_level: str = os.getenv("LOG_LEVEL", "info")


settings = Settings()
