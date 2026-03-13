# mole-track

Motion-detection system for monitoring mole tunnel activity. A Raspberry Pi Zero W with a camera module watches a calibration target laid across a surface tunnel; Lucas-Kanade sparse optical flow detects when the mole pushes through. A mobile web app streams the camera live, handles calibration, and plays a browser audio alert when movement is triggered.

---

## Hardware

| Component | Detail |
|-----------|--------|
| Pi Zero W | `molerig` · `192.168.86.30` · user `dave` |
| Camera | OV5647 (Pi Camera Module v1) via CSI |
| OS | Raspbian 13 (Trixie) |
| Python | 3.13.5 |

---

## Architecture

```
picamera2 (libcamera threads)
  ├── JpegEncoder → FrameBuffer (Condition) ─────────► /api/stream.mjpeg
  └── post_callback (lores YUV420)
        └── Y-plane → CLAHE normalize
              ├── gray_jpeg_buffer ───────────────────► /api/stream-gray.mjpeg
              └── get_gray_frame() ──► MoleDetector
                                          └── calcOpticalFlowPyrLK
                                                └── DetectionEvent → pollStatus
```

### Threading model
- picamera2 runs on its own libcamera threads — never called from asyncio
- `FrameBuffer` wraps a `threading.Condition`; MJPEG endpoints call `run_in_executor` to wait without blocking the event loop
- `MoleDetector` runs its LK loop in a daemon thread; state transitions are protected by `threading.Lock`

### Camera streams
| Stream | Resolution | Format | Use |
|--------|-----------|--------|-----|
| `main` | 640×480 | RGB888 → JPEG | browser display |
| `lores` | 320×240 | YUV420 → Y-plane | optical flow input |

Frame rate: **3 fps** (sufficient for moles; conserves Pi Zero CPU).

### Detection pipeline
1. User taps live feed to place 2–12 calibration points
2. Frontend sends normalised (0–1) coordinates to `POST /api/calibrate`
3. Backend scales to lores resolution; stores as `(N,1,2) float32` array
4. On `Start`: snapshot of current positions taken as cumulative/odometer reference; LK loop begins
5. Each frame: `cv2.calcOpticalFlowPyrLK(prev, curr, pts)` — surviving points scored against threshold
6. Triggered when the active metric ≥ threshold for `detection_debounce` consecutive frames
7. If >50% of points lost → `TRACKING_LOST`; user prompted to recalibrate

---

## Project Structure

```
mole-track/
├── Makefile                    # deploy, run-pi, logs-pi, install-pi, setup-service
├── deploy.sh                   # rsync to Pi; --restart kills/relaunches server
├── mole-track.service          # systemd unit file
├── requirements.txt
├── mole_track/
│   ├── main.py                 # FastAPI app + lifespan (camera/detector startup)
│   ├── config.py               # Settings dataclass — all tunables via env vars
│   ├── camera.py               # CameraManager: picamera2, FrameBuffer, CLAHE
│   ├── detector.py             # MoleDetector: LK flow, debounce, all metric families
│   └── api/
│       ├── routes.py           # API endpoints + MJPEG StreamingResponse helper
│       └── schemas.py          # Pydantic request/response models
└── static/
    ├── index.html              # Single-page mobile-first app
    ├── style.css               # Mobile layout, camera overlay, pulse animation
    └── app.js                  # Canvas overlay, calibration, polling, Web Audio
```

---

## Detection Modes

Four metric families. Each has `max`, `mean`, and `total` variants.

### Per-frame
Displacement between the **previous frame and current frame** per point. Resets every frame.
- Best for: fast transient motion (sudden shove)
- Risk: misses slow sustained movement if each frame's step is below threshold

### Windowed accumulation
Sum of per-frame displacements over the last **N frames** (default window = 10).
- Best for: slow sustained movement that builds up gradually
- `Window` setting controls how many frames are summed

### Cumulative (from Start)
Straight-line distance from each point's **position when Start was pressed** to its current position.
- Best for: detecting net displacement from a known resting position
- Caveat: decreases if motion reverses (mole backs out)

### Odometer (path length)
Monotonically increasing sum of **all frame-to-frame steps** since Start. Never decreases.
- Best for: total activity budget — any movement counts, even back-and-forth
- The most sensitive measure for slow or oscillating motion

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/health` | Camera running + detector state |
| `GET`  | `/api/stream.mjpeg` | Live colour MJPEG stream |
| `GET`  | `/api/stream-gray.mjpeg` | CLAHE-normalised grayscale stream |
| `POST` | `/api/calibrate` | Set key points `{"points":[{"x":0.5,"y":0.5},…]}` |
| `POST` | `/api/detection/start` | Begin LK loop |
| `POST` | `/api/detection/stop` | Halt LK loop |
| `GET`  | `/api/status` | Full detection state + all metrics |
| `GET`  | `/api/settings` | Current settings |
| `POST` | `/api/settings` | Update settings (partial update, all fields optional) |

---

## Configuration

All settings are read from environment variables at startup. Defaults are suitable for most deployments.

| Variable | Default | Description |
|----------|---------|-------------|
| `CAPTURE_WIDTH` | `640` | Main stream width (px) |
| `CAPTURE_HEIGHT` | `480` | Main stream height (px) |
| `LORES_WIDTH` | `320` | Optical flow frame width (px) |
| `LORES_HEIGHT` | `240` | Optical flow frame height (px) |
| `FRAME_RATE` | `3.0` | Capture and detection frame rate (fps) |
| `DETECTION_MODE` | `max` | Starting detection mode |
| `DISPLACEMENT_THRESHOLD` | `8.0` | Trigger threshold (px, lores scale) |
| `DETECTION_DEBOUNCE` | `3` | Consecutive frames above threshold to trigger |
| `MIN_POINTS_RATIO` | `0.5` | Minimum fraction of points that must survive LK |
| `ACCUMULATION_WINDOW` | `10` | Frames summed for windowed modes |
| `LOG_LEVEL` | `info` | Python logging level |
| `PORT` | `8000` | Server listen port |

Environment variables can be set in the systemd unit (`Environment=` lines in `mole-track.service`) or passed directly when running manually.

---

## Setup

### First-time Pi setup

```bash
# 1. Install system packages (pre-compiled ARM binaries — no pip/venv needed)
make install-pi

# 2. Deploy code
make deploy

# 3. Install and enable systemd service (auto-starts on boot)
make setup-service

# 4. Start it now
ssh dave@192.168.86.30 "sudo systemctl start mole-track.service"
```

### Dev machine prerequisites

```bash
pip install -r requirements.txt   # FastAPI, uvicorn, pydantic (for IDE support only)
```

---

## Deploy Workflow

```bash
make deploy            # rsync only — live server keeps running with old code
make deploy-restart    # rsync + kill old server + start new one
make logs-pi           # tail systemd journal (live)
make status-pi         # systemctl status
make run-pi            # run uvicorn directly over SSH (interactive, Ctrl-C to stop)
```

`deploy.sh --restart` uses `setsid --fork` to detach the new uvicorn process from the SSH channel, so the SSH session closes cleanly after launch. Pi Zero takes ~10–12 s to initialise libcamera; the script waits and then prints the tail of `/tmp/mole-track.log`.

---

## Systemd Service

`/etc/systemd/system/mole-track.service` — installed by `make setup-service`.

- Runs as user `dave`
- `Restart=on-failure` with 5 s back-off
- Logs visible via `make logs-pi` or `journalctl -u mole-track.service`

---

## Notes

- **SSH flakiness during recording**: Pi Zero CPU spikes when libcamera is active; SSH sessions sometimes drop. Wait 10–15 s and retry — the server is fine.
- **CLAHE normalisation**: applied to the Y-plane before every LK frame. Makes tracking robust to outdoor illumination changes (cloud cover, shade). The "Show normalized gray" toggle lets you verify what the detector actually sees.
- **Point re-anchoring**: LK points re-anchor to their new positions every frame for the per-frame and windowed metrics. The cumulative and odometer metrics maintain their own fixed/accumulated references from `Start`.
