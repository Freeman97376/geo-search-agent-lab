"""Restore the exact official Git objects in upstreams.lock.json, never pull latest."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args, input=None):
    return subprocess.run(
        ['git', '-C', str(path), *args], input=input, text=True,
        capture_output=True, check=True, timeout=180,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
    ).stdout.strip()


def main():
    lock = json.loads((ROOT / 'upstreams.lock.json').read_text(encoding='utf-8'))
    for repo in lock['repositories']:
        path = ROOT / 'third_party' / repo['directory']
        if path.exists():
            if git(path, 'remote', 'get-url', 'origin') != repo['url']:
                raise RuntimeError(f'Unexpected origin: {path}')
            if git(path, 'rev-parse', 'HEAD') != repo['commit'] or git(path, 'status', '--porcelain'):
                raise RuntimeError(f'Existing checkout differs; preserve and review it: {path}')
            print(f"Verified {repo['directory']} {repo['commit']}")
            continue
        path.mkdir(parents=True)
        git(path, 'init')
        git(path, 'remote', 'add', 'origin', repo['url'])
        git(path, '-c', 'core.autocrlf=false', 'fetch', '--depth', '1', 'origin', repo['commit'])
        exclusions = repo.get('windows_excluded_paths', []) if os.name == 'nt' else []
        options = ['-c', 'core.autocrlf=false']
        if exclusions:
            git(path, 'config', 'core.sparseCheckout', 'true')
            git(path, 'config', 'core.sparseCheckoutCone', 'false')
            (path / '.git/info/sparse-checkout').write_text(
                '/*\n' + ''.join(f'!/{p}\n' for p in exclusions), encoding='utf-8')
            # NTFS validation otherwise rejects names before sparse exclusion.
            # Only this command disables it; excluded files are never materialized.
            options += ['-c', 'core.protectNTFS=false']
        git(path, *options, 'checkout', '--detach', repo['commit'])
        if git(path, 'status', '--porcelain'):
            raise RuntimeError(f'Checkout is not clean: {path}')
        print(f"Restored {repo['directory']} {repo['commit']}")


if __name__ == '__main__':
    main()
