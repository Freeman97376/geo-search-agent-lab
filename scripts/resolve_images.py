"""Resolve official registry tags ONCE to immutable Linux/amd64 image digests.

Existing locks are never overwritten. No Docker daemon or login is required.
"""
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPT = ', '.join([
    'application/vnd.oci.image.index.v1+json',
    'application/vnd.docker.distribution.manifest.list.v2+json',
    'application/vnd.oci.image.manifest.v1+json',
    'application/vnd.docker.distribution.manifest.v2+json',
])


def resolve(repo, tag):
    url = 'https://auth.docker.io/token?' + urllib.parse.urlencode({
        'service': 'registry.docker.io', 'scope': f'repository:{repo}:pull'})
    with urllib.request.urlopen(url, timeout=30) as response:
        token = json.load(response)['token']
    headers = {'Authorization': f'Bearer {token}', 'Accept': ACCEPT}

    def request(path):
        req = urllib.request.Request(f'https://registry-1.docker.io/v2/{repo}/{path}', headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
            advertised = response.headers.get('Docker-Content-Digest')
            if advertised and advertised != digest:
                raise RuntimeError('Registry digest verification failed')
            return json.loads(raw), digest

    manifest, index_digest = request(f'manifests/{tag}')
    digest = index_digest
    if 'manifests' in manifest:
        matches = [m for m in manifest['manifests']
                   if m.get('platform', {}).get('os') == 'linux'
                   and m.get('platform', {}).get('architecture') == 'amd64']
        if len(matches) != 1:
            raise RuntimeError('Expected exactly one Linux/amd64 manifest')
        manifest, digest = request('manifests/' + matches[0]['digest'])
        if digest != matches[0]['digest']:
            raise RuntimeError('Platform manifest digest mismatch')
    config, config_digest = request('blobs/' + manifest['config']['digest'])
    if config_digest != manifest['config']['digest']:
        raise RuntimeError('Image config digest mismatch')
    return {'image': f'docker.io/{repo}@{digest}', 'resolved_tag': tag,
            'index_digest': index_digest, 'platform': 'linux/amd64',
            'labels': config.get('config', {}).get('Labels', {})}


def main():
    target = ROOT / 'infra/searxng/images.lock.json'
    if target.exists():
        print('Image lock already exists; refusing automatic upgrade.')
        return
    upstreams = json.loads((ROOT / 'upstreams.lock.json').read_text(encoding='utf-8'))
    searx = next(r for r in upstreams['repositories'] if r['directory'] == 'searxng')
    tag = searx['commit_date'][:10].replace('-0', '-').replace('-', '.') + '-' + searx['commit'][:9]
    lock = {'schema_version': 1, 'resolved_at_utc': datetime.now(timezone.utc).isoformat(),
            'searxng': resolve('searxng/searxng', tag),
            'valkey': resolve('valkey/valkey', '9-alpine')}
    revision = lock['searxng']['labels'].get('org.opencontainers.image.revision', '')
    if revision and not searx['commit'].startswith(revision):
        raise RuntimeError('SearXNG image revision does not match source checkout')
    target.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    print(f'Locked {lock["searxng"]["image"]}')


if __name__ == '__main__':
    main()
