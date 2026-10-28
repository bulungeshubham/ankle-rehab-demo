"""Only launch fixed project tools. Arguments are passed without a shell."""
import os
from pathlib import Path
import shutil
import subprocess
import threading

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'integrations' / 'opencv' / 'output'


class Tools:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def running(self, name):
        with self.lock:
            job = self.jobs.get(name)
            return bool(job and job.poll() is None)

    def start(self, name, script, arguments):
        if os.getenv('ANKLE_HOSTED') == '1':
            raise RuntimeError('Hardware tools run only on the laptop dashboard.')
        with self.lock:
            old = self.jobs.get(name)
            if old and old.poll() is None:
                return
            OUTPUT.mkdir(parents=True, exist_ok=True)
            python = os.getenv('ANKLE_VISION_PYTHON') or shutil.which('python')
            if not python:
                raise RuntimeError('Python not found. Set ANKLE_VISION_PYTHON to the OpenCV Python executable.')
            with (OUTPUT / f'{name}.log').open('w', encoding='utf-8') as log:
                self.jobs[name] = subprocess.Popen([python, '-u', str(ROOT / script), *arguments],
                    cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def stop(self, name):
        with self.lock:
            job = self.jobs.get(name)
            if job and job.poll() is None:
                job.terminate()
                try:
                    job.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    job.kill()

    def log(self, name):
        try:
            return (OUTPUT / f'{name}.log').read_text(encoding='utf-8', errors='replace')[-5000:]
        except OSError:
            return 'No log yet.'
