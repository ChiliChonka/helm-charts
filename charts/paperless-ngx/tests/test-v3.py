#!/usr/bin/env python3
"""Render checks for paperless-ngx 3; no cluster required."""
import argparse
import base64
import json
import subprocess
from pathlib import Path
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--chart', default=str(Path(__file__).resolve().parents[1]))
args = parser.parse_args()

def render(*extra, fails_with=None):
    command = ['helm', 'template', 'paperless-ngx', args.chart, '-n', 'paperless-ngx', *extra]
    result = subprocess.run(command, capture_output=True, text=True)
    if fails_with:
        assert result.returncode != 0, f'Render accepted {extra}'
        assert fails_with in result.stderr, result.stderr
        return []
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict)]

def one(docs, kind, name):
    found = [d for d in docs if d['kind'] == kind and d['metadata']['name'] == name]
    assert len(found) == 1, f'Expected exactly one {kind}/{name}, got {len(found)}'
    return found[0]

docs = render()
container = one(docs, 'Deployment', 'paperless-ngx')['spec']['template']['spec']['containers'][0]
assert container['image'].endswith(':3.2.1'), container['image']

# Secret key: from the generated Secret, never from the ConfigMap.
secret_env = [e for e in container['env'] if e['name'] == 'PAPERLESS_SECRET_KEY']
assert len(secret_env) == 1, secret_env
assert secret_env[0]['valueFrom']['secretKeyRef'] == {'name': 'paperless-ngx-secret-key', 'key': 'secret-key'}
secret = one(docs, 'Secret', 'paperless-ngx-secret-key')
assert len(base64.b64decode(secret['data']['secret-key'])) == 64
assert secret['metadata']['annotations']['helm.sh/resource-policy'] == 'keep'
assert 'PAPERLESS_SECRET_KEY' not in one(docs, 'ConfigMap', 'paperless-ngx-configuration')['data']

# New v3 settings reach the ConfigMap.
new = {
    'PAPERLESS_CONSUMER_POLLING_INTERVAL': '30',
    'PAPERLESS_CONSUMER_STABILITY_DELAY': '5',
    'PAPERLESS_CONSUMER_IGNORE_DIRS': 'tmp',
    'PAPERLESS_ARCHIVE_FILE_GENERATION': 'always',
    'PAPERLESS_DB_OPTIONS': 'sslmode=prefer',
    'PAPERLESS_ALLAUTH_TRUSTED_PROXY_COUNT': '1',
    'PAPERLESS_ALLAUTH_TRUSTED_CLIENT_IP_HEADER': 'X-Real-IP',
}
sets = [a for k, v in new.items() for a in ('--set-string', f'paperless-ngx.paperlessVars.{k}={v}')]
data = one(render(*sets), 'ConfigMap', 'paperless-ngx-configuration')['data']
for key, value in new.items():
    assert data.get(key) == value, f'{key}: {data.get(key)!r}'

# Removed or replaced v2 settings abort the render instead of being dropped silently.
removed = ['PAPERLESS_CONSUMER_POLLING', 'PAPERLESS_CONSUMER_INOTIFY_DELAY', 'PAPERLESS_CONSUMER_POLLING_DELAY',
           'PAPERLESS_CONSUMER_POLLING_RETRY_COUNT', 'PAPERLESS_CONSUMER_BARCODE_SCANNER',
           'PAPERLESS_OCR_SKIP_ARCHIVE_FILE', 'PAPERLESS_DBSSLMODE', 'PAPERLESS_DBSSLROOTCERT',
           'PAPERLESS_DBSSLCERT', 'PAPERLESS_DBSSLKEY', 'PAPERLESS_DB_TIMEOUT', 'PAPERLESS_SECRET_KEY']
for key in removed:
    render('--set-string', f'paperless-ngx.paperlessVars.{key}=probe', fails_with=f'{key} is not supported')
for mode in ['skip', 'skip_noarchive']:
    render('--set-string', f'paperless-ngx.paperlessVars.PAPERLESS_OCR_MODE={mode}', fails_with='were removed')
assert one(render('--set-string', 'paperless-ngx.paperlessVars.PAPERLESS_OCR_MODE=auto'),
           'ConfigMap', 'paperless-ngx-configuration')['data']['PAPERLESS_OCR_MODE'] == 'auto'

print(f'PASS: image 3.2.1, secret key from Secret, {len(new)} new settings rendered, '
      f'{len(removed) + 2} removed settings rejected')

# startupProbe: absent by default, rendered when configured.
assert 'startupProbe' not in container
probe = {'httpGet': {'path': '/', 'port': 'http'}, 'periodSeconds': 10, 'failureThreshold': 180}
with_probe = render('--set-json', 'paperless-ngx.startupProbe=' + json.dumps(probe))
rendered = one(with_probe, 'Deployment', 'paperless-ngx')['spec']['template']['spec']['containers'][0]
assert rendered['startupProbe'] == probe, rendered.get('startupProbe')
print('PASS: startupProbe optional')

# Every image pinned: no implicit or explicit `latest`.
images = [c['image'] for d in docs if d['kind'] in ('Deployment', 'StatefulSet')
          for c in d['spec']['template']['spec'].get('initContainers', []) + d['spec']['template']['spec']['containers']]
assert images, 'no images rendered'
for image in images:
    name = image.split('@')[0]
    assert ':' in name.rsplit('/', 1)[-1] and not name.endswith(':latest'), f'unpinned image {image}'
print(f'PASS: {len(images)} images pinned')
