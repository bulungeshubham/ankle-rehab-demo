"""Experimental marker-based, single-plane vision measurements; no motor control."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).parent / '.packages'))
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
ARUCO = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)


def wrap(value):
    return (value + 180.0) % 360.0 - 180.0


def marker_angle(corners):
    # The marker's printed top edge defines its orientation in the image plane.
    p = np.asarray(corners, dtype=float).reshape(4, 2)
    edge = p[1] - p[0]
    if np.linalg.norm(edge) < 5:
        raise ValueError('marker is too small')
    return math.degrees(math.atan2(edge[1], edge[0]))


def relative_angle(markers, foot=0, shank=1):
    if foot not in markers or shank not in markers:
        return None
    return wrap(marker_angle(markers[foot]) - marker_angle(markers[shank]))


def source_value(value):
    return int(value) if value.isdecimal() else value


class Camera:
    def __init__(self, source):
        self.source = source
        self.cap = cv2.VideoCapture(source_value(source))
        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f'Cannot open camera {source!r}. Check its URL, network, or webcam index.')
        self.lock = threading.Lock()
        self.frame = None
        self.timestamp = 0.0
        self.running = True
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                with self.lock:
                    self.frame, self.timestamp = frame, time.monotonic()
            else:
                time.sleep(.1)

    def latest(self):
        with self.lock:
            return (None if self.frame is None else self.frame.copy(), self.timestamp)

    def close(self):
        self.running = False
        self.cap.release()


def camera_calibration(args):
    if args.columns < 2 or args.rows < 2 or args.square_mm <= 0:
        raise ValueError('Board dimensions must be at least 2x2 internal corners; square size must be positive.')
    cam = Camera(args.source)
    obj = np.zeros((args.columns * args.rows, 3), np.float32)
    obj[:, :2] = np.mgrid[0:args.columns, 0:args.rows].T.reshape(-1, 2) * args.square_mm
    objects, images, image_size = [], [], None
    waiting_since = time.monotonic()
    try:
        print('Show the board at different positions and tilts. SPACE captures, C calculates, Q quits.')
        while True:
            frame, ts = cam.latest()
            if frame is None or time.monotonic() - ts > 2:
                if time.monotonic() - waiting_since > 15:
                    raise RuntimeError('Camera has not delivered a fresh frame for 15 seconds.')
                time.sleep(.05); continue
            waiting_since = time.monotonic()
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            size = (gray.shape[1], gray.shape[0])
            if image_size is not None and size != image_size:
                raise RuntimeError('Camera resolution changed during calibration; start again.')
            found, corners = cv2.findChessboardCorners(gray, (args.columns, args.rows))
            if found:
                corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                    (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, .001))
                cv2.drawChessboardCorners(frame, (args.columns, args.rows), corners, found)
            cv2.putText(frame, f'Views: {len(images)} | SPACE capture C calculate Q quit', (10, 25), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 255, 0), 1)
            cv2.imshow('Camera calibration', frame)
            key = cv2.waitKey(20) & 255
            if key == ord('q'): return
            if key == 32 and found:
                image_size = size; objects.append(obj.copy()); images.append(corners.copy())
                print(f'Captured view {len(images)}')
            if key == ord('c'):
                if len(images) < 10:
                    print('Need at least 10 different views.'); continue
                rms, matrix, distortion, _, _ = cv2.calibrateCamera(objects, images, image_size, None, None)
                output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({'width': image_size[0], 'height': image_size[1], 'camera_matrix': matrix.tolist(), 'distortion': distortion.tolist(), 'rms_pixels': rms, 'views': len(images), 'square_mm': args.square_mm}, indent=2))
                print(f'Saved {output}. Reprojection RMS = {rms:.3f} px; check against independent images.')
                return
    finally:
        cam.close(); cv2.destroyAllWindows()


def load_calibration(path, frame):
    if not path: return frame
    data = json.loads(Path(path).read_text())
    if (frame.shape[1], frame.shape[0]) != (data['width'], data['height']):
        raise ValueError(f'Calibration {path} resolution differs from stream. Recalibrate at this resolution.')
    return cv2.undistort(frame, np.array(data['camera_matrix']), np.array(data['distortion']))


def detect(frame):
    detector = cv2.aruco.ArucoDetector(ARUCO, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if ids is None: return {}
    cv2.aruco.drawDetectedMarkers(frame, corners, ids)
    markers = {}
    for i, marker_id in enumerate(ids.flatten()):
        marker_id = int(marker_id)
        if marker_id in markers: raise ValueError(f'Duplicate marker ID {marker_id} visible')
        markers[marker_id] = corners[i]
    return markers


def track(args):
    sources = [('side', args.side, args.side_calibration)]
    if args.front: sources.append(('front', args.front, args.front_calibration))
    cameras = []
    zero = {name: None for name, _, _ in sources}
    last_written = 0.0
    log = None
    try:
        for name, src, cal in sources: cameras.append((name, Camera(src), cal))
        if args.csv:
            path = Path(args.csv); path.parent.mkdir(parents=True, exist_ok=True)
            log = path.open('w', newline='', encoding='utf-8')
            writer = csv.DictWriter(log, fieldnames=['unix_time', 'side_deg', 'front_deg', 'side_status', 'front_status'])
            writer.writeheader()
        print(f'Side: foot ID {args.foot_id}, shank ID {args.shank_id}. Front: foot ID {args.front_foot_id}, shank ID {args.front_shank_id}. Press Z for neutral; Q to quit.')
        while True:
            measurements, statuses, raw, previews = {}, {}, {}, {}
            for name, cam, calibration in cameras:
                frame, ts = cam.latest()
                if frame is None or time.monotonic() - ts > 2:
                    statuses[name] = 'camera unavailable or stale'; zero[name] = None; continue
                frame = load_calibration(calibration, frame)
                try:
                    foot_id = args.front_foot_id if name == 'front' else args.foot_id
                    shank_id = args.front_shank_id if name == 'front' else args.shank_id
                    raw[name] = relative_angle(detect(frame), foot_id, shank_id)
                except ValueError as exc:
                    raw[name] = None; statuses[name] = str(exc)
                if raw[name] is None:
                    statuses.setdefault(name, 'markers missing or too small')
                elif zero[name] is None:
                    statuses[name] = 'press Z in neutral'
                else:
                    measurements[name] = wrap(raw[name] - zero[name]); statuses[name] = 'tracking'
                label = f'{name}: {measurements[name]:+.1f} deg' if name in measurements else f'{name}: {statuses[name]}'
                cv2.putText(frame, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, .55, (0,255,0) if name in measurements else (0,0,255), 2)
                previews[name] = frame
                cv2.imshow(f'Ankle {name}', frame)
            key = cv2.waitKey(20) & 255
            if key == ord('q'): break
            if key == ord('z'):
                for name, _, _ in cameras:
                    zero[name] = raw.get(name)
                print('Neutral captured only for views with both markers visible:', zero)
            now = time.time()
            if now - last_written >= .2:
                packet = {'timestamp': now, 'measurement_type': 'experimental_marker_image_plane', 'views': {n: {'angle_deg': measurements.get(n), 'status': statuses.get(n, 'not configured'), 'calibrated_optics': bool(c)} for n, _, c in sources}}
                output = Path(args.latest); output.parent.mkdir(parents=True, exist_ok=True)
                for name, image in previews.items():
                    preview = output.parent / f'{name}_preview.jpg'
                    temporary = output.parent / f'{name}_preview.tmp.jpg'
                    if cv2.imwrite(str(temporary), image): os.replace(temporary, preview)
                tmp = output.with_suffix('.tmp'); tmp.write_text(json.dumps(packet)); os.replace(tmp, output)
                if log:
                    writer.writerow({'unix_time': now, 'side_deg': measurements.get('side'), 'front_deg': measurements.get('front'), 'side_status': statuses.get('side'), 'front_status': statuses.get('front', 'not configured')}); log.flush()
                last_written = now
    finally:
        for _, cam, _ in cameras: cam.close()
        if log: log.close()
        cv2.destroyAllWindows()


def markers(args):
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    for marker_id in (0, 1, 2, 3):
        if not 0 <= marker_id < 50: raise ValueError('Marker IDs must be between 0 and 49.')
        img = cv2.aruco.generateImageMarker(ARUCO, marker_id, 600)
        # A white margin is required for reliable detection against dark backgrounds.
        img = cv2.copyMakeBorder(img, 100, 100, 100, 100, cv2.BORDER_CONSTANT, value=255)
        path = output / f'marker_{marker_id}.png'
        if not cv2.imwrite(str(path), img): raise RuntimeError(f'Unable to write {path}')
        print(path)


def board(args):
    if args.square_mm <= 0: raise ValueError('Square size must be positive.')
    s = args.square_mm
    width, height = 12 * s, 9 * s
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">', f'<rect width="{width}" height="{height}" fill="white"/>']
    for row in range(7):
        for col in range(10):
            if (row + col) % 2 == 0:
                parts.append(f'<rect x="{(col+1)*s}" y="{(row+1)*s}" width="{s}" height="{s}" fill="black"/>')
    parts.append('</svg>')
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(parts), encoding='utf-8')
    print(f'{output}: 10x7 squares, nominal {s:g} mm; print at 100% and measure.')


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    m = sub.add_parser('markers'); m.add_argument('--output', default=str(ROOT / 'markers')); m.set_defaults(func=markers)
    b = sub.add_parser('board'); b.add_argument('--square-mm', type=float, default=20); b.add_argument('--output', default=str(ROOT / 'markers' / 'checkerboard_20mm.svg')); b.set_defaults(func=board)
    c = sub.add_parser('calibrate'); c.add_argument('--source', required=True); c.add_argument('--columns', type=int, default=9); c.add_argument('--rows', type=int, default=6); c.add_argument('--square-mm', type=float, required=True); c.add_argument('--output', required=True); c.set_defaults(func=camera_calibration)
    t = sub.add_parser('track'); t.add_argument('--side', required=True); t.add_argument('--front'); t.add_argument('--side-calibration'); t.add_argument('--front-calibration'); t.add_argument('--foot-id', type=int, default=0); t.add_argument('--shank-id', type=int, default=1); t.add_argument('--front-foot-id', type=int, default=2); t.add_argument('--front-shank-id', type=int, default=3); t.add_argument('--latest', default=str(ROOT / 'output' / 'latest.json')); t.add_argument('--csv'); t.set_defaults(func=track)
    args = p.parse_args()
    if hasattr(args, 'foot_id') and args.foot_id == args.shank_id: p.error('Foot and shank marker IDs must differ.')
    if args.command == 'track' and args.front_foot_id == args.front_shank_id: p.error('Front foot and shank marker IDs must differ.')
    try: args.func(args)
    except (RuntimeError, ValueError, OSError, cv2.error) as exc: print(f'Error: {exc}', file=sys.stderr); return 1
    return 0

if __name__ == '__main__': raise SystemExit(main())
