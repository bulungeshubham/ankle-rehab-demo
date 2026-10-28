"""Hosted entry point; laptop hardware tools remain local."""
import os
from pathlib import Path
import runpy
import sys

os.environ['ANKLE_HOSTED'] = '1'
app_dir = Path(__file__).resolve().parent / 'integrations' / 'streamlit'
sys.path.insert(0, str(app_dir))
runpy.run_path(str(app_dir / 'app.py'), run_name='__main__')
