from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from collections import deque
from datetime import datetime, timezone
from typing import Any

import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh



MAX_POINTS = 240


def setting(name: str, default: Any) -> Any:
    """Read a Streamlit secret first, then an environment variable."""
    try:
        section = st.secrets.get("mqtt", {})
    except Exception:
        section = {}
    value = section.get(name.lower()) if hasattr(section, "get") else None
    if value in (None, ""):
        value = os.getenv(f"MQTT_{name.upper()}")
    return default if value in (None, "") else value


@st.cache_resource(show_spinner=False)
def mqtt_state(host: str, port: int, username: str, password: str, topic: str):
    """Create one MQTT listener per Streamlit process and keep a small live buffer."""
    import paho.mqtt.client as mqtt

    store: dict[str, Any] = {"latest": {}, "history": {}, "connected": False, "error": ""}
    lock = threading.Lock()

    def on_connect(client, userdata, flags, reason_code, properties):
        with lock:
            store["connected"] = (reason_code.value if hasattr(reason_code, "value") else int(reason_code)) == 0
            store["error"] = "" if (reason_code.value if hasattr(reason_code, "value") else int(reason_code)) == 0 else f"MQTT connection refused ({reason_code})"
        if (reason_code.value if hasattr(reason_code, "value") else int(reason_code)) == 0:
            client.subscribe(topic, qos=0)

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        with lock:
            store["connected"] = False
            if (reason_code.value if hasattr(reason_code, "value") else int(reason_code)) != 0:
                store["error"] = f"MQTT disconnected ({reason_code})"

    def on_message(client, userdata, message):
        try:
            packet = json.loads(message.payload.decode("utf-8"))
            if not isinstance(packet, dict):
                return
            patient = message.topic.split("/")[2]
            packet["_received_at"] = time.time()
            with lock:
                store["latest"][patient] = packet
                history = store["history"].setdefault(patient, deque(maxlen=MAX_POINTS))
                history.append(packet)
        except (ValueError, UnicodeDecodeError, IndexError, TypeError):
            return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ankle-streamlit-{os.getpid()}")
    if username:
        client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    try:
        client.connect_async(host, int(port), keepalive=30)
        client.loop_start()
    except Exception as exc:  # shown in the app instead of crashing Streamlit
        store["error"] = str(exc)
    return client, store, lock


