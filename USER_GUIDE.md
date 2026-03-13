# Mole Track — User Guide

A pocket-sized detection rig that watches a surface tunnel and alerts you when the mole moves through. Set it up in the field, walk away, and your phone beeps when there's activity.

---

## How It Works

A string or thin rod is laid across the mole run. The Pi camera watches the string from above. When you open the app and press **Start**, the system tracks a set of points you tapped on the string. If those points move enough, it plays an audio alert on your phone.

Motion is measured using Lucas-Kanade optical flow — a computer-vision technique that follows small patches of texture between frames. It works on the shape and shade of the surface, not colour, so it is not fooled by the sun going behind a cloud.

---

## Physical Setup

1. **Position the camera** so the string or surface lies across roughly the middle of the frame, filling most of the width. Mount it looking straight down or at a slight angle — whatever keeps the string in frame throughout the detection period.

2. **Lighting**: The system is designed for outdoor use. CLAHE normalisation is applied before every frame, so gradual changes in brightness (clouds, time of day) are compensated automatically. Avoid pointing the camera directly into the sun.

3. **Stability**: The camera must be completely still once detection starts. Any camera shake will register as motion and trigger false alerts. Weigh or stake the mount down firmly.

4. **Network**: The Pi needs to be on the same Wi-Fi network as your phone. It serves the app on port 8000. If the Pi is configured to connect to your home network, this works automatically.

---

## Opening the App

Navigate to **http://192.168.86.30:8000** on your phone or any browser on the same network.

The header badge shows the system status:
- **OK** (green) — camera running, ready to calibrate
- **Degraded** (amber) — camera not yet initialised (give it 15 s after power-on) or an error occurred

---

## Step 1 — Calibrate

The camera stream appears at the top of the page. **Tap or click on the string** (or whatever surface you want to track) to place calibration points. Aim for 4–8 points spread along the visible section of the string.

- Points appear as coloured dots on the overlay
- **Clear** removes all points so you can start over
- **Calibrate** sends the points to the Pi — this button enables once you have placed at least 2 points

Good calibration point placement:
- Space them evenly along the string
- Avoid the very edges of the frame (LK needs surrounding texture)
- Place points on the string itself, not the bare soil, so there is actual texture to track

---

## Step 2 — Start Detection

Press **Start**. The detection indicator turns green and a timer counts up. Your phone must stay on this page (or the tab must remain active) to receive the audio alert.

While running, you will see live statistics update every 0.5 seconds:

| Stat | Meaning |
|------|---------|
| **Mean / Max / Total** | Per-frame displacement across all points |
| **W·Mean / W·Max / W·Total** | Windowed accumulation (sum of last N frames) |
| **C·Mean / C·Max / C·Total** | Cumulative — distance from where each point was when Start was pressed |
| **O·Mean / O·Max / O·Total** | Odometer — total path length since Start (never decreases) |

The per-point list below shows individual point displacements. The column highlighted in blue is the one currently being used for triggering.

---

## Audio Alert

When the trigger fires, the detection indicator turns red and pulses, and your phone plays a beep. A **Silence** button appears to dismiss the alert without stopping detection.

**Important for iPhone/iPad**: You must tap **Start** yourself — the audio will not work if the page auto-starts or if you navigate to it with detection already running, because iOS requires audio to be initiated by a physical tap.

---

## Step 3 — Stop

Press **Stop** to end detection. The indicator returns to Idle. Statistics are cleared. Press **Start** again to begin a fresh session (cumulative and odometer counters reset).

---

## Viewing the Normalised Gray Image

The **Show normalized gray** button (below the camera stream) switches the live view to the CLAHE-processed grayscale image — exactly what the detector sees. Use this to:

- Confirm that contrast is sufficient for tracking (points need nearby texture)
- Verify that illumination changes are being compensated (the image should look consistent regardless of cloud cover)

Press the button again to return to the colour view.

---

## Choosing a Detection Mode

Open the **Settings** section to change the mode. Press **Apply** to send the new settings to the Pi — you do not need to stop and restart detection.

### Quick guide

| Situation | Recommended mode | Threshold suggestion |
|-----------|-----------------|----------------------|
| Mole gives a single sharp shove | `max` | 6–10 px |
| Mole pushes slowly and steadily | `windowed_total` or `cumulative_total` | 30–60 px |
| Mole moves back and forth (oscillates) | `odometer_total` | 40–80 px |
| Want any net displacement from rest | `cumulative_max` | 15–25 px |
| General purpose starting point | `max`, threshold 8 px, debounce 3 | — |

### Mode families explained

**Per-frame** (`max`, `mean`, `total`)
Measures the movement between the last frame and the current frame. Resets every frame. Very responsive — catches a sudden shove in 3 frames (debounce default). Will miss motion where each individual frame's step is tiny.

**Windowed accumulation** (`windowed_max`, `windowed_mean`, `windowed_total`)
Sums the last *N* frames' worth of per-frame displacements. A slow push that moves 1 px per frame becomes 10 px after 10 frames in `windowed_total`. Adjust `Window (frames)` to control the summation period.

**Cumulative** (`cumulative_max`, `cumulative_mean`, `cumulative_total`)
Measures the straight-line distance from where each point was when you pressed Start to where it is now. Good for detecting net displacement from a resting position. The number will decrease if the mole backs up.

**Odometer** (`odometer_max`, `odometer_mean`, `odometer_total`)
Adds up every pixel of movement since Start — like a car's trip odometer. Never decreases, even if the mole reverses. The most sensitive indicator of total activity over time.

---

## Settings Reference

| Setting | Default | Notes |
|---------|---------|-------|
| **Detection mode** | `max` | See mode guide above |
| **Threshold (px)** | `8` | Pixel displacement at lores scale (320×240). At this resolution 8 px ≈ a few millimetres of real movement depending on camera height. |
| **Debounce (frames)** | `3` | Number of consecutive frames above threshold before alert fires. At 3 fps, debounce 3 = 1 second of sustained movement required. Lower = more sensitive, more false alerts. |
| **Window (frames)** | `10` | Only used by windowed modes. At 3 fps, window 10 = a 3-second accumulation window. |

Press **Apply** after any change. Settings take effect immediately without restarting detection.

---

## Tracking Lost

If the indicator shows **Tracking Lost**, optical flow has lost too many calibration points (e.g. the string has moved completely out of frame, or the image changed dramatically). To recover:

1. Press **Stop** if running
2. Re-position the camera or string if needed
3. Tap **Clear**, then place new calibration points on the current view
4. Press **Calibrate**, then **Start**

---

## Troubleshooting

**App won't load**
- Give the Pi 15–20 seconds after power-on for the camera to initialise
- Confirm your phone is on the same Wi-Fi network as the Pi
- Check the header badge: if it shows **Degraded**, the camera may have failed to start — power-cycle the Pi

**Start button stays disabled**
- You need to calibrate first — place at least 2 points and press Calibrate

**Calibrate button is disabled**
- Place at least 2 points on the image first

**Alert fires immediately / constant false triggers**
- The camera is moving — make sure the mount is rock-solid
- Threshold is too low — increase it in Settings
- Switch to a windowed or odometer mode which are less sensitive to single-frame noise

**Alert never fires even with clear motion**
- Check the "Show normalized gray" view — if the image looks completely flat/grey, tracking will fail
- Threshold may be too high — reduce it or try `odometer_total` with a lower threshold
- Try placing more calibration points directly on the string, not the bare soil

**Points drift or go wrong**
- The lores frame rate is 3 fps; very fast motion can exceed LK's tracking range
- Increase `winSize` in code (default 15×15 pixels) for coarser but more robust tracking, or reduce the distance between the camera and the subject

**SSH drops while camera is recording**
- Normal on Pi Zero — CPU spikes during camera operation cause SSH timeouts
- The server keeps running; reconnect after 10–15 seconds
- Use `make logs-pi` (which uses `journalctl -f`) rather than direct SSH for log monitoring

---

## Power and Auto-start

The Pi runs the server as a systemd service (`mole-track.service`) that starts automatically on boot and restarts on failure. Once powered on and connected to Wi-Fi, the app will be available at `http://192.168.86.30:8000` within about 20 seconds — no manual intervention needed.

To check the service status from your dev machine:

```bash
make status-pi    # one-shot status
make logs-pi      # live log stream
```
