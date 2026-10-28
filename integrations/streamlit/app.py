from __future__ import annotations
import json
import os
import math
import time
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.append(str(Path(__file__).resolve().parent / '.packages'))
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from dashboard_data import export_csv, number, sensor_angles, snapshot
from mqtt_service import mqtt_state, setting
from processes import OUTPUT, ROOT, Tools

st.set_page_config(page_title='Ankle Rehab · Research dashboard', page_icon='🦶', layout='wide')
st_autorefresh(interval=1000, key='ankle-refresh')

@st.cache_resource
def get_tools():
    return Tools()

jobs = get_tools()
HOSTED = os.getenv('ANKLE_HOSTED') == '1'

def metric(column, label, value, unit='°'):
    value = number(value)
    column.metric(label, '—' if value is None else f'{value:.2f}{unit}')

def launch(name, script, args):
    try:
        jobs.start(name, script, args)
        st.success('Tool launched. Check its log and the laptop windows.')
    except (OSError, RuntimeError, ValueError) as exc:
        st.error(str(exc))

with st.sidebar:
    st.title('🦶 Ankle Rehab')
    st.caption('Vision monitoring · IMU comparison')
    source = st.selectbox('Sensor source', ['Demonstration'], key='sensor_source')
    st.divider()
    st.caption('Data freshness: camera 3 s · IMU 5 s')
    st.caption('Motor: pending bench integration')

st.title('Ankle rehabilitation workspace')
st.caption('Monitor movement, check calibration and record prototype measurements.')
if HOSTED:
    st.info('Public demonstration: IMU values are simulated. Hardware acquisition and calibration run on the laptop through the private project.')
if source == 'Demonstration':
    st.warning('DEMONSTRATION MODE — sensor values are simulated; exports are labelled accordingly.')

vision, vision_status = snapshot(OUTPUT / 'latest.json', 3)
packet, imu_status = {}, 'waiting for data'
if source == 'USB serial':
    serial_data, imu_status = snapshot(OUTPUT / 'imu_latest.json', 5)
    if serial_data.get('measurement_mode'):
        packet = serial_data
    elif serial_data:
        imu_status = serial_data.get('status', 'calibrating')
elif source == 'MQTT':
    host, port = str(setting('host', '127.0.0.1')), int(setting('port', 1883))
    client, store, lock = mqtt_state(host, port, str(setting('username', '')), str(setting('password', '')),
        str(setting('topic', 'sars/rehab/+/sensors/imu')))
    with lock:
        packets = dict(store['latest'])
        mqtt_error, connected = store['error'], store['connected']
    st.sidebar.caption(f'MQTT: {"connected" if connected else "waiting"} · {host}:{port}')
    if packets:
        device = st.sidebar.selectbox('Device', sorted(packets))
        candidate = packets[device]
        if time.time() - candidate['_received_at'] <= 5:
            packet, imu_status = candidate, 'live'
        else:
            imu_status = 'stale sensor stream'
    if mqtt_error: st.sidebar.warning(mqtt_error)
else:
    now = time.time()
    packet = {'measurement_mode': 'dual_segment_validation', 'timestamp': now,
        'ankle_pitch_relative_deg': 12 * math.sin(now / 3), 'ankle_roll_relative_deg': 4 * math.sin(now / 4),
        'foot_accel_magnitude_g': 1.0, 'shank_accel_magnitude_g': 1.0}
    imu_status = 'simulated'

pitch, roll = sensor_angles(packet)
cols = st.columns(4)
cols[0].metric('IMU', imu_status.upper() if len(imu_status) < 30 else 'CHECK SETUP')
cols[1].metric('Vision', vision_status.upper())
metric(cols[2], 'Relative pitch' if packet.get('measurement_mode') == 'dual_segment_validation' else 'Pitch change', pitch)
metric(cols[3], 'Relative roll' if packet.get('measurement_mode') == 'dual_segment_validation' else 'Roll change', roll)

for key, default in [('samples', []), ('recording', False), ('recorded', []), ('last_key', None)]:
    if key not in st.session_state: st.session_state[key] = default
if st.session_state.get('previous_source') != source:
    st.session_state.samples = []
    st.session_state.previous_source = source
key = (source, packet.get('_received_at', packet.get('timestamp')), vision.get('timestamp'))
if key != st.session_state.last_key:
    row = {'time': time.time(), 'sensor_source': source,
        'sensor_time': packet.get('_received_at', packet.get('timestamp')), 'vision_time': vision.get('timestamp'),
        'pitch_deg': pitch, 'roll_deg': roll,
        'side_deg': number(vision.get('views', {}).get('side', {}).get('angle_deg')),
        'front_deg': number(vision.get('views', {}).get('front', {}).get('angle_deg'))}
    if any(row[k] is not None for k in ('pitch_deg','roll_deg','side_deg','front_deg')):
        st.session_state.samples = (st.session_state.samples + [row])[-300:]
        if st.session_state.recording and len(st.session_state.recorded) < 36000:
            st.session_state.recorded.append(row)
    st.session_state.last_key = key

overview, cameras, calibration, recording, setup = st.tabs(['Live monitoring', 'Cameras', 'Calibration', 'Recording', 'Connections'])
with overview:
    st.subheader('Movement over time')
    rows = st.session_state.samples
    if rows:
        fig = go.Figure()
        for field, label in [('pitch_deg','IMU pitch'),('roll_deg','IMU roll'),('side_deg','Side camera'),('front_deg','Front camera')]:
            fig.add_trace(go.Scatter(x=[datetime.fromtimestamp(r['time'], tz=timezone.utc) for r in rows],
                y=[r.get(field) for r in rows], name=label, mode='lines', connectgaps=False))
        fig.update_layout(height=340, margin=dict(l=12,r=12,t=20,b=12), yaxis_title='Degrees', legend=dict(orientation='h'))
        st.plotly_chart(fig, use_container_width=True)
    else: st.info('Measurements will appear when a source is connected. Use Demonstration in the sidebar to preview the interface.')
    st.caption('Camera values are image-plane rotations; IMU values are tilt estimates. These streams have independent timing and have not been aligned for accuracy comparison.')
    c = st.columns(2)
    metric(c[0], 'Foot acceleration', packet.get('foot_accel_magnitude_g', packet.get('accel_magnitude_g')), ' g')
    metric(c[1], 'Shank acceleration', packet.get('shank_accel_magnitude_g'), ' g')
    with st.expander('Latest sensor packet'): st.json(packet or {'status': imu_status})

with cameras:
    st.subheader('Front and side views')
    for col, name in zip(st.columns(2), ['side','front']):
        view = vision.get('views', {}).get(name, {})
        metric(col, f'{name.title()} marker angle', view.get('angle_deg'))
        col.caption(view.get('status', vision_status))
        col.caption('Optics: ' + ('calibration loaded' if view.get('calibrated_optics') else 'calibration not loaded'))
        preview = OUTPUT / f'{name}_preview.jpg'
        if vision and preview.exists() and time.time() - preview.stat().st_mtime < 3:
            col.image(str(preview), caption=f'{name.title()} tracking preview')
    st.info('Tracking windows open on this laptop. Both markers must be visible per view. Press Z there for neutral and Q to quit.')
    with st.form('camera_settings'):
        side = st.text_input('Side webcam index or MJPEG URL', '0')
        front = st.text_input('Front webcam index or MJPEG URL (optional)', '')
        optical = st.checkbox('Load saved camera lens calibrations', value=False)
        start = st.form_submit_button('Start camera tracking', disabled=HOSTED or jobs.running('vision'))
    if start:
        if jobs.running('calibration'): st.error('Stop lens calibration first.')
        elif not side.strip(): st.error('Enter a side camera source.')
        else:
            args = ['track', '--side', side.strip()]
            if front.strip(): args += ['--front', front.strip()]
            if optical:
                args += ['--side-calibration', str(OUTPUT / 'side_calibration.json')]
                if front.strip(): args += ['--front-calibration', str(OUTPUT / 'front_calibration.json')]
            launch('vision', 'integrations/opencv/vision.py', args)
    if st.button('Stop camera tracking', disabled=HOSTED):
        jobs.stop('vision'); (OUTPUT / 'latest.json').unlink(missing_ok=True); st.rerun()
    st.caption('Tracker: ' + ('running' if jobs.running('vision') else 'stopped'))
    with st.expander('Camera log'): st.code(jobs.log('vision'))

with calibration:
    st.subheader('1. Calibrate both MPU-6050 sensors')
    st.write('Use foot address 0x68 and shank 0x69. Upload the diagnostic sketch in Arduino IDE. Keep both sensors still for startup calibration; repeat after reset or remounting.')
    st.code(str(ROOT / 'firmware/diagnostics/dual_mpu_calibration/dual_mpu_calibration.ino'))
    st.caption('Firmware performs this calibration. Receiving readings does not independently confirm angle accuracy.')
    st.subheader('2. Calibrate each camera lens')
    board = ROOT / 'integrations/opencv/markers/checkerboard_20mm.svg'
    if board.exists(): st.download_button('Download checkerboard', board.read_bytes(), board.name, 'image/svg+xml')
    st.write('Print A4 landscape at 100%; mount flat and measure a square. The board has 9 × 6 internal corners. Capture at least 10 varied views with Space, then press C in the laptop calibration window.')
    with st.form('lens_calibration'):
        view_name = st.selectbox('Camera to calibrate', ['side','front'])
        calibration_source = st.text_input('Camera index or MJPEG URL', '0')
        square = st.number_input('Measured square side (mm)', min_value=1.0, max_value=100.0, value=20.0)
        run_cal = st.form_submit_button('Open lens calibration on laptop', disabled=HOSTED or jobs.running('calibration'))
    if run_cal:
        if jobs.running('vision'): st.error('Stop tracking before opening calibration.')
        elif not calibration_source.strip(): st.error('Enter a camera source.')
        else: launch('calibration', 'integrations/opencv/vision.py', ['calibrate','--source',calibration_source.strip(),
            '--square-mm',str(square),'--output',str(OUTPUT / f'{view_name}_calibration.json')])
    if st.button('Stop lens calibration', disabled=HOSTED): jobs.stop('calibration')
    for name in ['side','front']:
        try:
            saved = json.loads((OUTPUT / f'{name}_calibration.json').read_text())
            st.success(f'{name.title()} file: {saved["width"]} × {saved["height"]} · {saved["views"]} views · RMS {saved["rms_pixels"]:.3f} pixels')
        except (OSError, ValueError, KeyError, TypeError): st.caption(f'{name.title()}: no valid saved calibration')
    with st.expander('Calibration log'): st.code(jobs.log('calibration'))
    st.subheader('3. Set camera neutral')
    st.write('Attach markers 0 (foot) and 1 (shank) for the side view, and 2 (foot) and 3 (shank) for the front view. Hold a reference pose and press Z in the tracking window. Verify the angle sign using known movement directions.')
    for marker_id in range(4):
        marker = ROOT / f'integrations/opencv/markers/marker_{marker_id}.png'
        if marker.exists(): st.download_button(f'Marker {marker_id}', marker.read_bytes(), marker.name, 'image/png', key=f'marker{marker_id}')

with recording:
    st.subheader('Record this browser session')
    label = st.text_input('Session label', 'ankle-bench-test')
    a, b = st.columns(2)
    if a.button('Start recording', disabled=st.session_state.recording): st.session_state.recording = True; st.rerun()
    if b.button('Stop recording', disabled=not st.session_state.recording): st.session_state.recording = False; st.rerun()
    st.metric('Saved samples', len(st.session_state.recorded))
    st.caption('Recording' if st.session_state.recording else 'Stopped')
    st.caption('Records about one row per second while this page refreshes. Separate timestamps are included; streams are not synchronized. Raw USB logging is also available through the USB reader.')
    if st.session_state.recorded:
        safe_label = ''.join(c for c in label if c.isalnum() or c in '-_') or 'ankle-session'
        st.download_button('Download recorded CSV', export_csv(st.session_state.recorded), f'{safe_label}.csv', 'text/csv')
        st.dataframe(st.session_state.recorded[-20:], use_container_width=True)
    if st.button('Clear recorded samples', disabled=st.session_state.recording): st.session_state.recorded = []; st.rerun()
    if len(st.session_state.recorded) >= 36000: st.warning('Recording limit reached. Download and clear to continue.')

with setup:
    st.subheader('USB connection · your existing ESP32')
    st.write('Close Arduino Serial Monitor first. Opening USB may reset the ESP32: keep the sensors still during calibration. Select USB serial in the sidebar to display these readings.')
    with st.form('serial_connection'):
        serial_port = st.text_input('ESP32 serial port (from Arduino IDE)', 'COM5')
        connect_serial = st.form_submit_button('Connect USB reader', disabled=HOSTED or jobs.running('serial'))
    if connect_serial:
        if not serial_port.strip(): st.error('Enter the ESP32 port.')
        else: launch('serial', 'integrations/opencv/imu_log.py', ['--port',serial_port.strip(),'--output',str(OUTPUT / 'imu_raw.csv'),'--latest',str(OUTPUT / 'imu_latest.json')])
    if st.button('Disconnect USB reader', disabled=HOSTED):
        jobs.stop('serial'); (OUTPUT / 'imu_latest.json').unlink(missing_ok=True); st.rerun()
    st.caption('USB reader: ' + ('running' if jobs.running('serial') else 'stopped'))
    with st.expander('USB / calibration messages'): st.code(jobs.log('serial'))
    raw = OUTPUT / 'imu_raw.csv'
    if raw.exists(): st.download_button('Download raw USB CSV', raw.read_bytes(), 'imu_raw.csv', 'text/csv')
    st.subheader('MQTT and network cameras')
    st.write('MQTT uses the existing .streamlit/secrets.toml or MQTT environment variables. Choose MQTT in the sidebar once the firmware publishes. Enter the cameras’ confirmed MJPEG URLs in Cameras.')
    st.caption('Tool processes are shared by this local dashboard server. Stop them here when finished. Tool raw logs are overwritten on restart.')
    guide = ROOT / 'integrations/opencv/README.md'
    st.download_button('Download full integration guide', guide.read_text(encoding='utf-8'), 'ANKLE-integration-guide.md', 'text/markdown')

st.divider()
st.caption('Research prototype · measurements require reference testing · motor assistance pending bench verification.')
