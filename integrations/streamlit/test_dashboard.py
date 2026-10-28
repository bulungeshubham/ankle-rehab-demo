import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dashboard_data import number, snapshot, sensor_angles, export_csv


class DataTests(unittest.TestCase):
    def test_missing_and_nonfinite_values(self):
        self.assertIsNone(number(None))
        self.assertIsNone(number('nan'))
        self.assertEqual(number(0), 0)
        self.assertEqual(sensor_angles({}), (None, None))

    def test_stale_and_invalid_snapshots(self):
        path = Path('latest.json')
        with patch.object(Path, 'read_text', return_value=json.dumps({'timestamp': 100, 'views': {}})):
            self.assertEqual(snapshot(path, 3, now=101)[1], 'live')
            self.assertEqual(snapshot(path, 3, now=110)[0], {})
        with patch.object(Path, 'read_text', return_value='{'):
            self.assertEqual(snapshot(path, 3)[0], {})
        with patch.object(Path, 'read_text', return_value=json.dumps({'timestamp': 100, 'views': []})):
            self.assertEqual(snapshot(path, 3, now=101)[0], {})

    def test_csv_keeps_missing_values_and_source_label(self):
        data = export_csv([{'source': 'Demonstration', 'angle': None}])
        self.assertIn('Demonstration,', data)
        self.assertNotIn('0.0', data)

if __name__ == '__main__': unittest.main()
