"""Publish live prototype data to the public dashboard over an authenticated TLS broker."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import ProxyHandler, build_opener

sys.path.append(str(Path(__file__).resolve().parent / '.packages'))
import paho.mqtt.client as mqtt

DEFAULT_TOPIC = 'ankle/review/live'
MAX_IMAGE = 150_000
MAX_MESSAGE = 410_000
MAX_AGE = 8
LOCAL_RANGES = tuple(ipaddress.IPv4Network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))


def private_ip(value):
    ip = ipaddress.IPv4Address(value)
    if not any(ip in network for network in LOCAL_RANGES):
        raise ValueError('Use the current private IPv4 address for each board.')
    return str(ip)


def open_client(username, password):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(username, password)
    client.tls_set()
    return client


def read_board(address, path):
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f'http://{address}/{path}', timeout=1.6) as response:
            data = response.read(MAX_IMAGE + 1)
        if len(data) <= MAX_IMAGE:
            return data
    except (OSError, ValueError):
        pass
    return None


def imu_angles(data):
    if not isinstance(data, dict):
        return None
    try:
        pitch = float(data['ankle_pitch_relative_deg'])
        roll = float(data['ankle_roll_relative_deg'])
        age = float(data['age_ms'])
        if data.get('valid') is True and 0 <= age <= 5000 and all(math.isfinite(x) for x in (pitch, roll)):
            return {'pitch': pitch, 'roll': roll}
    except (KeyError, TypeError, ValueError):
        pass
    return None


def capture_packet(pool, top, side, imu):
    futures = {label: pool.submit(read_board, addr, path) for label, addr, path in (
        ('top', top, 'capture'), ('side', side, 'capture'), ('imu', imu, 'imu'))}
    frames = {}
    for label in ('top', 'side'):
        image = futures[label].result()
        if image and image.startswith(b'\xff\xd8') and image.endswith(b'\xff\xd9'):
            frames[label] = base64.b64encode(image).decode('ascii')
    try:
        angles = imu_angles(json.loads(futures['imu'].result() or '{}'))
    except (ValueError, TypeError):
        angles = None
    return {'version': 1, 'captured_at': time.time(), 'frames': frames, 'imu': angles}


def parse_packet(payload, now):
    if len(payload) > MAX_MESSAGE:
        return None
    try:
        data = json.loads(payload)
        stamp = float(data['captured_at'])
        if data['version'] != 1 or not -2 <= now - stamp <= MAX_AGE:
            return None
        frames = {}
        for name, encoded in data['frames'].items():
            if name not in ('top','side') or not isinstance(encoded, str):
                continue
            image = base64.b64decode(encoded, validate=True)
            if len(image) <= MAX_IMAGE and image.startswith(b'\xff\xd8') and image.endswith(b'\xff\xd9'):
                frames[name] = image
        raw_imu = data.get('imu')
        imu = None
        if isinstance(raw_imu, dict):
            pitch, roll = float(raw_imu['pitch']), float(raw_imu['roll'])
            if all(math.isfinite(x) for x in (pitch, roll)):
                imu = {'pitch': pitch, 'roll': roll}
        return stamp, frames, imu
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None


def listen(host, port, username, password, topic):
    client = open_client(username, password)
    lock = threading.Lock()
    state = {'packet': None, 'connected': False}

    def on_connect(client, userdata, flags, reason_code, properties):
        with lock:
            state['connected'] = reason_code == 0
        if reason_code == 0:
            client.subscribe(topic, qos=0)

    def on_disconnect(client, userdata, flags, reason_code, properties):
        with lock:
            state.update(connected=False, packet=None)

    def on_message(client, userdata, message):
        if message.retain or message.topic != topic:
            return
        packet = parse_packet(message.payload, time.time())
        if packet:
            with lock:
                state['packet'] = packet

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(host, port, keepalive=20)
    client.loop_start()
    return state, lock


def show_live_share():
    import streamlit as st

    st.title('Live prototype')
    try:
        section = st.secrets.get('ankle_live', {})
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        section = {}
    required = ('host','username','password','viewer_passcode')
    if not all(section.get(key) for key in required):
        st.info('Live sharing is waiting for the owner to configure the encrypted relay.')
        return

    passcode = st.text_input('Live viewing passcode', type='password')
    if not hmac.compare_digest(passcode.encode(), str(section['viewer_passcode']).encode()):
        if passcode:
            st.error('Incorrect passcode.')
        return

    @st.cache_resource(show_spinner=False)
    def subscriber(host, port, username, password, topic):
        return listen(host, port, username, password, topic)

    state, lock = subscriber(str(section['host']), int(section.get('port', 8883)),
                             str(section['username']), str(section['password']),
                             str(section.get('topic', DEFAULT_TOPIC)))

    @st.fragment(run_every=1.0)
    def display():
        with lock:
            packet = state['packet']
            connected = state['connected']
        if not connected:
            st.warning('Waiting for the relay connection.')
            return
        if not packet or time.time() - packet[0] > MAX_AGE:
            st.info('Waiting for a fresh capture from the prototype laptop.')
            return
        _, frames, imu = packet
        for name, column in zip(('top','side'), st.columns(2)):
            if name in frames:
                column.image(frames[name], caption=f'{name.title()} camera', width='stretch')
            else:
                column.warning(f'{name.title()} camera unavailable')
        left, right = st.columns(2)
        left.metric('MPU relative pitch', f"{imu['pitch']:.2f}°" if imu else 'Unavailable')
        right.metric('MPU relative roll', f"{imu['roll']:.2f}°" if imu else 'Unavailable')
        st.caption('Live snapshots and dual-IMU tilt estimates. The devices are sampled independently.')

    display()


def publish(args):
    host = os.getenv('ANKLE_LIVE_HOST')
    username = os.getenv('ANKLE_LIVE_USERNAME')
    password = os.getenv('ANKLE_LIVE_PASSWORD')
    if not all((host, username, password)):
        raise SystemExit('Configure ANKLE_LIVE_HOST, ANKLE_LIVE_USERNAME and ANKLE_LIVE_PASSWORD first.')
    try:
        top, side, imu = map(private_ip, (args.top, args.side, args.imu))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    topic = os.environ.get('ANKLE_LIVE_TOPIC', DEFAULT_TOPIC)
    client = open_client(username, password)
    ready = threading.Event()
    connected = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            connected.set()
        ready.set()

    def on_disconnect(client, userdata, flags, reason_code, properties):
        connected.clear()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(host, int(os.getenv('ANKLE_LIVE_PORT', '8883')), keepalive=20)
    client.loop_start()
    try:
        if not ready.wait(10) or not connected.is_set():
            raise SystemExit('TLS relay authentication did not succeed.')
        print('Sharing live snapshots and MPU readings. Press Ctrl+C to stop.', flush=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            while True:
                start = time.monotonic()
                if connected.is_set():
                    packet = capture_packet(pool, top, side, imu)
                    if packet['frames'] or packet['imu']:
                        client.publish(topic, json.dumps(packet, separators=(',',':')), qos=0, retain=False)
                time.sleep(max(0.1, 1.0 - (time.monotonic() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Share current prototype measurements via TLS MQTT.')
    parser.add_argument('--top', default='10.208.160.145')
    parser.add_argument('--side', default='10.208.160.108')
    parser.add_argument('--imu', default='10.208.160.25')
    parser.add_argument('--share-live', action='store_true', required=True,
                        help='Send snapshots and MPU readings to the configured relay until stopped.')
    publish(parser.parse_args())
