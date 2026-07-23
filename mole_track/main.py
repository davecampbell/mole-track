import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from mole_track.api.routes import router
from mole_track.camera import CameraManager
from mole_track.config import settings
from mole_track.detector import DetectionEvent, MoleDetector

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    camera = CameraManager(settings)
    detector = MoleDetector(settings, camera)

    def on_detection(event: DetectionEvent) -> None:
        if event.triggered:
            logger.info(
                "MOLE DETECTED  [%s] disp=%.1f px  points=%d/%d",
                event.displacement_mode,
                event.displacement_value,
                event.active_points,
                event.total_points,
            )

    detector.set_detection_callback(on_detection)
    camera.start()

    app.state.camera = camera
    app.state.detector = detector
    app.state.settings = settings

    logger.info("mole-track ready on http://%s:%d", settings.host, settings.port)
    yield

    detector.stop()
    camera.stop()
    logger.info("Shutdown complete")


app = FastAPI(title="mole-track", version="0.1.0", lifespan=lifespan)
app.include_router(router)

# Static files must be mounted AFTER API routes.
# Custom subclass adds no-cache headers so the browser always loads fresh JS/CSS.
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, FileResponse):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            if "etag" in response.headers:
                del response.headers["etag"]
            if "last-modified" in response.headers:
                del response.headers["last-modified"]
        return response

app.mount("/", NoCacheStaticFiles(directory=str(STATIC_DIR), html=True), name="static")
