"""File-based review tools for the hosted demonstration."""
from __future__ import annotations

import csv
import io
import json
import math

import plotly.graph_objects as go
import streamlit as st

from dashboard_data import number


def read_trace(file_bytes):
    text = file_bytes.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or ())
    samples = {'sample_index', 'relative_roll_deg', 'relative_pitch_deg'}
    dashboard = {'pitch_deg', 'roll_deg'}
    if samples <= fields:
        columns = ('relative_roll_deg', 'relative_pitch_deg')
    elif dashboard <= fields:
        columns = ('roll_deg', 'pitch_deg', 'side_deg', 'front_deg', 'top_deg')
    else:
        raise ValueError('Use a worn-trial CSV or an exported dashboard CSV.')
    blocks = {}
    for i, row in enumerate(reader, 1):
        if i > 36000:
            raise ValueError('The review limit is 36,000 rows.')
        index = number(row.get('sample_index', i))
        values = [number(row.get(field)) for field in columns]
        if index is not None and any(value is not None for value in values):
            blocks.setdefault(row.get('ready_block') or 'Session', []).append((index, *values))
    if not blocks:
        raise ValueError('The CSV has no valid angle readings.')
    return columns, blocks


def decode(file, grayscale=False):
    from integrations.opencv.vision import cv2, np
    if file.size > 4 * 1024 * 1024:
        raise ValueError('Use images under 4 MB.')
    mode = cv2.IMREAD_GRAYSCALE if grayscale else cv2.IMREAD_COLOR
    img = cv2.imdecode(np.frombuffer(file.getvalue(), dtype=np.uint8), mode)
    if img is None or img.shape[0] * img.shape[1] > 2_500_000:
        raise ValueError('Use a readable JPG or PNG of up to 2.5 megapixels.')
    return img


def projected_change(neutral, movement, view, calibration=None):
    from integrations.opencv.vision import cv2, detect, np, relative_angle, wrap
    ids = (0, 1) if view == 'Side' else (2, 3)
    if neutral.shape != movement.shape:
        raise ValueError('Both frames must use the same camera resolution.')
    if calibration:
        if (neutral.shape[1], neutral.shape[0]) != (calibration['width'], calibration['height']):
            raise ValueError('Frame resolution differs from the saved lens calibration.')
        camera_matrix = np.array(calibration['camera_matrix'])
        distortion = np.array(calibration['distortion'])
        neutral = cv2.undistort(neutral, camera_matrix, distortion)
        movement = cv2.undistort(movement, camera_matrix, distortion)
    raw = []
    for name, frame in [('Neutral', neutral), ('Movement', movement)]:
        angle = relative_angle(detect(frame), *ids)
        if angle is None:
            raise ValueError(f'{name} frame must show marker IDs {ids[0]} and {ids[1]}.')
        raw.append(angle)
    return wrap(raw[1] - raw[0]), (neutral, movement)


def calibrate(images, square_mm):
    from integrations.opencv.vision import cv2, np
    image_size = (images[0].shape[1], images[0].shape[0])
    object_points = np.zeros((9 * 6, 3), np.float32)
    object_points[:, :2] = np.mgrid[0:9, 0:6].T.reshape(-1, 2) * square_mm
    corners_for_frames = []
    for gray in images:
        if (gray.shape[1], gray.shape[0]) != image_size:
            raise ValueError('All checkerboard images must have the same resolution.')
        found, corners = cv2.findChessboardCorners(gray, (9, 6))
        if found:
            corners_for_frames.append(cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 30, .001)))
    count = len(corners_for_frames)
    if count < 10:
        raise ValueError(f'Checkerboard detected in {count} images. Provide at least ten different clear views.')
    rms, matrix, distortion, _, _ = cv2.calibrateCamera(
        [object_points] * count, corners_for_frames, image_size, None, None)
    if not math.isfinite(rms) or not np.isfinite(matrix).all() or not np.isfinite(distortion).all():
        raise ValueError('Calibration failed. Try images at more varied positions and tilts.')
    return {'width':image_size[0], 'height':image_size[1], 'camera_matrix':matrix.tolist(),
            'distortion':distortion.tolist(), 'rms_pixels':rms, 'views':count, 'square_mm':square_mm}


def show_review_tools(root):
    st.title('Ankle rehabilitation file review')
    st.caption('Files selected here are processed by the hosted app. Use the laptop dashboard to keep your measurements offline.')
    trial, camera, optics, connections = st.tabs(['Recorded trial', 'Camera angles', 'Lens calibration', 'Live connections'])
    with trial:
        upload = st.file_uploader('Recorded trial or dashboard CSV', type='csv', max_upload_size=5)
        if upload:
            try:
                columns, blocks = read_trace(upload.getvalue())
                st.metric('Valid rows', f'{sum(map(len, blocks.values())):,}')
                block = st.selectbox('Recording block', list(blocks))
                rows = blocks[block]
                graph = go.Figure()
                for i, field in enumerate(columns, 1):
                    values = [row[i] for row in rows]
                    if any(value is not None for value in values):
                        graph.add_scatter(x=[row[0] for row in rows], y=values,
                                          mode='lines+markers' if len(rows)==1 else 'lines',
                                          name=field.replace('_deg','').replace('_',' ').title())
                graph.update_layout(xaxis_title='Sample index', yaxis_title='Reported angle (°)')
                st.plotly_chart(graph, width='stretch')
                st.download_button('Download original CSV', upload.getvalue(), upload.name, 'text/csv')
            except (ValueError, csv.Error) as exc:
                st.error(str(exc))
    with camera:
        st.caption('Side view uses foot/shank markers 0/1. Top view uses 2/3. Keep both markers visible in each frame.')
        with st.form('frame_comparison'):
            view = st.selectbox('Camera view', ['Side','Top'])
            left, right = st.columns(2)
            neutral = left.file_uploader('Neutral frame', type=['jpg','jpeg','png'], max_upload_size=4)
            movement = right.file_uploader('Movement frame', type=['jpg','jpeg','png'], max_upload_size=4)
            use_cal = st.checkbox('Use this view’s saved lens calibration', value=False)
            measure = st.form_submit_button('Measure projected angle')
        if measure:
            try:
                if not neutral or not movement:
                    raise ValueError('Add both frames before measuring.')
                saved = st.session_state.get('lens_' + view) if use_cal else None
                if use_cal and saved is None:
                    raise ValueError('Calibrate this view first or clear the lens calibration option.')
                angle, frames = projected_change(decode(neutral), decode(movement), view, saved)
                st.metric(f'{view} projected angle change', f'{angle:.2f}°')
                for col, frame in zip(st.columns(2),frames):
                    col.image(frame, channels='BGR', width='stretch')
            except (ValueError, ImportError, RuntimeError) as exc:
                st.error(str(exc))
            except Exception as exc:
                if type(exc).__module__ == 'cv2':
                    st.error('OpenCV could not process these frames.')
                else:
                    raise
        for marker_id in range(4):
            marker = root/'integrations'/'opencv'/'markers'/f'marker_{marker_id}.png'
            if marker.is_file():
                st.download_button(f'Marker {marker_id}', marker.read_bytes(), marker.name, 'image/png')
    with optics:
        board = root/'integrations'/'opencv'/'markers'/'checkerboard_20mm.svg'
        if board.is_file():
            st.download_button('Download 9 × 6 checkerboard', board.read_bytes(), board.name, 'image/svg+xml')
        with st.form('lens_files'):
            view = st.selectbox('Calibrate view', ['Side','Top'])
            square = st.number_input('Printed square size (mm)', min_value=1.0, max_value=100.0, value=20.0)
            images = st.file_uploader('10–24 checkerboard images at different positions and tilts',
                                      type=['jpg','jpeg','png'], accept_multiple_files=True, max_upload_size=4)
            submitted = st.form_submit_button('Compute lens calibration')
        if submitted:
            try:
                if not 10 <= len(images) <= 24:
                    raise ValueError('Select 10–24 checkerboard images from one camera.')
                st.session_state['lens_' + view] = calibrate([decode(file, grayscale=True) for file in images], square)
            except (ValueError, ImportError, RuntimeError) as exc:
                st.error(str(exc))
            except Exception as exc:
                if type(exc).__module__ == 'cv2':
                    st.error('OpenCV could not calibrate these checkerboard views.')
                else:
                    raise
        for calibrated_view in ('Side','Top'):
            result = st.session_state.get('lens_' + calibrated_view)
            if result:
                st.caption(f'{calibrated_view} · {result["views"]} views · RMS {result["rms_pixels"]:.2f} px')
                st.download_button(f'Download {calibrated_view.lower()} lens calibration',
                                   json.dumps(result, indent=2), f'{calibrated_view.lower()}_calibration.json', 'application/json')
    with connections:
        st.write('Run the laptop dashboard for live ESP32-CAM previews, dual-MPU telemetry, USB recording and on-device neutral calibration.')
        st.code(r'& .\integrations\streamlit\run.ps1', language='powershell')
        st.write('Open http://127.0.0.1:8501 on the laptop. Choose Hotspot Wi-Fi or USB serial. Export the CSV to review it here.')
