"""Validate Python-visible default dependencies and baseline/dev direct pins.

Unlike native uv pip check, this sees packages in uv run's temporary overlay.
It checks distribution metadata, not imports, optional extras, or model profiles.
Run with the baseline development requirements installed.
"""
from importlib import metadata
from pathlib import Path
import sys

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name


def main() -> int:
    installed = {}
    for dist in metadata.distributions():
        name = dist.metadata.get('Name')
        if name:
            # Preserve first-visible metadata when sys.path contains overlays.
            installed.setdefault(canonicalize_name(name), dist)
    failures = []
    checked = 0

    def check(requirement: Requirement, origin: str) -> None:
        nonlocal checked
        if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
            return
        checked += 1
        dist = installed.get(canonicalize_name(requirement.name))
        if dist is None:
            failures.append(f'{origin}: missing {requirement.name}')
        elif requirement.specifier and not requirement.specifier.contains(dist.version, prereleases=True):
            failures.append(f'{origin}: {requirement.name} {dist.version} does not satisfy {requirement.specifier}')

    for name, dist in installed.items():
        python_range = dist.metadata.get('Requires-Python')
        if python_range and not SpecifierSet(python_range).contains('.'.join(map(str, sys.version_info[:3]))):
            failures.append(f'{name}: incompatible Requires-Python {python_range}')
        for value in dist.requires or []:
            check(Requirement(value), name)

    root = Path(__file__).resolve().parents[1]
    for name in ('requirements.txt', 'requirements-dev.txt'):
        for line in (root / name).read_text(encoding='utf-8').splitlines():
            value = line.split('#', 1)[0].strip()
            if value and not value.startswith('-'):
                check(Requirement(value), name)
    if len(installed) < 20:
        failures.append(f'Unexpectedly incomplete environment: {len(installed)} distributions')
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        return 1
    print(f'PASS: {len(installed)} visible distributions, {checked} active requirements; all baseline/dev direct pins match.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
