# Camera and dual-IMU calibration: prototype workflow

This is an independent OpenCV marker-based bench tool. It does not replace the existing MediaPipe application or command the motor. It estimates **relative image-plane rotation**, not a validated anatomical ankle angle. It uses four printed markers: 0 foot and 1 shank for the side camera; 2 foot and 3 shank for the front camera. Each camera must actually see both of its markers throughout the movement. The proposed ankle-mounted cameras might not have sufficient view or distance; check actual video and change the mounting if necessary. Distinct markers should be rigidly attached to the segments and parallel to the camera image plane. Perspective, camera/marker motion, strap movement, bending and occlusion cause errors. Moving wearable cameras need extra validation.

## 1. Set up in VS Code

Open the project folder and a PowerShell terminal. These dependencies are installed in `integrations/opencv/.packages` on this computer. If the computer changes, run:

```powershell
python -m pip install --target integrations\opencv\.packages -r integrations\opencv\requirements.txt
python integrations\opencv\vision.py markers
```

Print the four images in `integrations/opencv/markers/`, with their white borders. Ensure IDs 0 and 1 can be seen in the side view and 2 and 3 in the front view. Use a sufficiently large, flat, clearly printed marker; verify detection in the preview. Do not place markers over a wound.

## 2. Camera lens calibration (repeat separately for each camera)

Use the supplied `markers/checkerboard_20mm.svg`: it has **9 by 6 internal corners** (10 by 7 squares). Print on A4 landscape at 100% (no fit-to-page), mount it flat, and measure the actual square side in millimetres; a printer may scale it. Use the intended camera resolution and fixed settings. Enter the measured square size in place of `20` below:

```powershell
python integrations\opencv\vision.py calibrate --source 0 --square-mm 20 --output integrations\opencv\output\side_calibration.json
```

Show the full board at different angles and positions across the image. Press Space for each clear detection, collect at least 10 varied views, and press C. Q quits. Repeat for the front camera, changing `--source` and output file. The reported RMS is a fit statistic, not a guarantee of angle accuracy. Calibration applies only to that camera at the recorded resolution; rerun if lens, resolution or relevant settings change.

For an ESP32-CAM, flash an appropriate CameraWebServer or equivalent stream firmware for the actual board and use its own confirmed MJPEG stream URL, e.g. `--source http://CAMERA_IP:81/stream`. The exact port and path depend on its firmware. Both cameras and laptop must be reachable on the same network. Test each stream individually first. The controller ESP32 and each ESP32-CAM are separate boards.

## 3. Run tracking

Test with a webcam now:

```powershell
python integrations\opencv\vision.py track --side 0 --side-calibration integrations\opencv\output\side_calibration.json --csv integrations\opencv\output\camera_log.csv
```

For two configured ESP32-CAM streams later (replace the IPs):

```powershell
python integrations\opencv\vision.py track --side http://192.168.1.21:81/stream --front http://192.168.1.22:81/stream --side-calibration integrations\opencv\output\side_calibration.json --front-calibration integrations\opencv\output\front_calibration.json --csv integrations\opencv\output\camera_log.csv
```

Place both segments in a neutral reference position while both markers in each view are visible, then press **Z** in an OpenCV window. The shown angle is the change from that reference. The sign depends on camera direction and marker orientation: record controlled known-direction movements and compare against a goniometer. Do not label the sign as dorsiflexion or plantarflexion until verified. Press Q to stop. Each view has its own neutral. Missing markers show no measurement; a stale camera requires a new neutral. Optical calibration is recommended; omit the relevant calibration flag only for initial framing checks and treat results as uncalibrated.

The tracker writes `output/latest.json` for the existing local Streamlit dashboard and optionally a CSV. Run Streamlit separately as described in `integrations/streamlit/README.md`. It reads the local file even when MQTT is disconnected. This is local display only: camera images are not sent to Blynk or ThingSpeak. CSV and JSON may contain personal movement information; keep them private. Output logs are overwritten when started again.

## 4. Calibrate the two IMUs you have now

Use `firmware/diagnostics/dual_mpu_i2c_scan/dual_mpu_i2c_scan.ino` first, then `firmware/diagnostics/dual_mpu_calibration/dual_mpu_calibration.ino` in Arduino IDE. Confirm the selected ESP32 board and serial port. Foot address 0x68 (AD0 low), shank 0x69 (AD0 high), shared SDA GPIO21 and SCL GPIO22 on the assumed ESP32 DevKit. Confirm the exact breakout board supply, pull-up and logic levels; ESP32 GPIO is 3.3 V. Tie all grounds together and do not connect 5 V I²C signals to ESP32 GPIO.

Use Serial Monitor at 115200 baud. Attach both IMUs with axes consistently aligned and keep them stationary in the neutral reference during the startup countdown and sampling. If calibration is rejected or a sensor disconnects, correct it and send `r` to retry. Values are RAM-only and recalibration is needed after reset or remounting. Readings are preliminary tilt changes, not validated 3D joint angles.

To save serial data instead, close Serial Monitor and run (replace COM3 with your ESP32 port):

```powershell
python integrations\opencv\imu_log.py --port COM3 --output integrations\opencv\output\imu_log.csv
```

Opening the serial port may reset the ESP32 and restart calibration. To retry, stop the logger and use Serial Monitor to send `r`, or reset and restart the logger with the sensors stationary. The logger records the PC receive time, not the sensor sample time. Camera and IMU CSVs are not synchronized or automatically fused: use a common visible motion event, account for unknown latency, and compare against an independent reference in a later experiment.

## 5. When the servo arrives

Use a separate appropriate regulated servo supply, common signal ground and a bench fixture without a person. Verify the actual MG996R, horn, mounting, power and travel before testing. This vision tool never enables the servo; safe control, independent stop, force limits and human-use validation are not implemented. Do not use it to move a person's ankle.

## Sources

- OpenCV camera calibration: https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
- Espressif CameraWebServer example: https://github.com/espressif/arduino-esp32/tree/master/libraries/ESP32/examples/Camera/CameraWebServer
