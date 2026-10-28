"""Validated local snapshots and session export helpers."""
import csv
import io
import json
import math
import time
from pathlib import Path


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def snapshot(path, max_age, now=None):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {}, 'invalid packet'
        if 'views' in data and (not isinstance(data['views'], dict) or
                any(not isinstance(view, dict) for view in data['views'].values())):
            return {}, 'invalid camera packet'
        stamp = number(data.get('timestamp'))
        if stamp is None:
            return {}, 'invalid timestamp'
        age = (time.time() if now is None else now) - stamp
        if age < -2:
            return {}, 'clock mismatch'
        if age > max_age:
            return {}, f'stale ({age:.0f}s old)'
        return data, 'live'
    except (OSError, ValueError, AttributeError, TypeError):
        return {}, 'waiting for data'


def sensor_angles(packet):
    dual = packet.get('measurement_mode') == 'dual_segment_validation'
    return (number(packet.get('ankle_pitch_relative_deg' if dual else 'pitch_delta_deg')),
            number(packet.get('ankle_roll_relative_deg' if dual else 'roll_delta_deg')))


def export_csv(rows):
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output.getvalue()
