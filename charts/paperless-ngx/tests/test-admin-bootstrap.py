#!/usr/bin/env python3
"""Render the bootstrap boundary; no cluster or production credentials required."""
import argparse
import subprocess
from pathlib import Path
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--chart', default=str(Path(__file__).resolve().parents[1]))
args = parser.parse_args()

def render(*extra, succeeds=True):
    command = ['helm', 'template', 'paperless-ngx', args.chart, '-n', 'paperless-ngx', *extra]
    result = subprocess.run(command, capture_output=True, text=True)
    if not succeeds:
        assert result.returncode != 0, 'Inline bootstrap credentials were accepted'
        assert 'Inline admin credentials are forbidden' in result.stderr
        return []
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if isinstance(d, dict)]

def app(docs):
    deployments = [d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'paperless-ngx']
    assert len(deployments) == 1, 'Expected exactly one Paperless Deployment'
    return deployments[0]['spec']['template']['spec']['containers'][0]

def assert_no_inline_admin(docs):
    for doc in docs:
        if doc['kind'] == 'Secret':
            assert not any(k.startswith('PAPERLESS_ADMIN_') for k in doc.get('data', {})), 'Rendered inline admin credentials'

plain = render()
assert_no_inline_admin(plain)
assert not any(e.get('secretRef', {}).get('name') == 'paperless-ngx-admin' for e in app(plain)['envFrom'])
external = render('--set', 'paperless-ngx.adminCredentialsSecret=bootstrap-test')
assert_no_inline_admin(external)
assert sum(e.get('secretRef', {}).get('name') == 'bootstrap-test' for e in app(external)['envFrom']) == 1
for key in ['PAPERLESS_ADMIN_USER', 'PAPERLESS_ADMIN_PASSWORD', 'PAPERLESS_ADMIN_MAIL']:
    render('--set', f'paperless-ngx.paperlessVars.{key}=regression-probe', succeeds=False)
print('PASS: bootstrap disabled by default, external Secret mounted, all legacy inline fields rejected')
