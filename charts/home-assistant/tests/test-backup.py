#!/usr/bin/env python3
"""Render checks for the backup ObjectStore; no cluster required."""
import subprocess
from pathlib import Path
import yaml

CHART = Path(__file__).resolve().parents[1]


def store(*extra):
    command = ['helm', 'template', 'home-assistant', str(CHART), '-n', 'home-assistant',
               '-f', str(CHART / 'ci' / 'test-values.yaml'), *extra]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    found = [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict) and d['kind'] == 'ObjectStore']
    assert len(found) == 1, f'Expected exactly one ObjectStore, got {len(found)}'
    return found[0]['spec']['configuration']['s3Credentials']


# Region: absent by default, taken from the credentials Secret when regionKey is set.
s3 = store()
assert s3['accessKeyId'] == {'name': 'example-backup-s3', 'key': 'ACCESS_KEY_ID'}, s3
assert 'region' not in s3, s3
s3 = store('--set', 'cnpg.backup.regionKey=area')
assert s3['region'] == {'name': 'example-backup-s3', 'key': 'area'}, s3
print('PASS: backup region optional')
