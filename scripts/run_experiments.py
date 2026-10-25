"""Run sequential bounded local k6/HPA experiments, preserving each raw run.

Uses the existing Secret without reading its value. Never changes HPA settings
or forces replica counts. Completed Jobs are kept for inspection; only a Job
created by this process is removed on interruption/failure to stop its load.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from record_scaling import sample

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = 'app.kubernetes.io/name=ai-model-serving'


def parse_output(log: str) -> tuple[float, dict]:
    starts = re.findall(r'EXPERIMENT_START=(\d+)', log)
    if len(starts) != 1:
        raise ValueError('Expected exactly one workload start timestamp')
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', log):
        try:
            value, _ = decoder.raw_decode(log[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and 'phases' in value and 'scenario' in value:
            return int(starts[0]) / 1000, value
    raise ValueError('No complete k6 summary found; do not invent measurements')


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def host_memory_total_mib() -> int | None:
    if os.name == 'nt':
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return round(status.ullTotalPhys / 1024**2)
    if hasattr(os, 'sysconf'):
        try:
            return round(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024**2)
        except (OSError, ValueError):
            return None
    return None


def tool_version(command: list[str]) -> dict:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return {'available': False, 'reason': 'not_found'}
    except subprocess.TimeoutExpired:
        return {'available': False, 'reason': 'timeout'}
    except OSError:
        return {'available': False, 'reason': 'execution_error'}
    if result.returncode != 0:
        return {'available': False, 'reason': 'command_failed', 'exit_code': result.returncode}
    return {'available': True, 'version': result.stdout.strip()[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubectl', required=True)
    parser.add_argument('--context', default='ai-serving-local', choices=['ai-serving-local'])
    parser.add_argument('--runs', type=int, default=3, choices=range(1, 4))
    parser.add_argument('--prefix', required=True, help='Unique short DNS-safe experiment prefix')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,35}', args.prefix):
        parser.error('prefix must be a short lowercase DNS label')
    root = ROOT / 'results' / 'raw' / args.prefix
    root.mkdir(parents=True, exist_ok=False)

    def command(*parts: str, stdin: str | None = None) -> str:
        result = subprocess.run(
            [args.kubectl, '--context', args.context, *parts, '--request-timeout=15s'],
            input=stdin, capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def get(*parts):
        return json.loads(command('get', *parts, '-o', 'json'))

    def observation():
        return sample(args.kubectl, 'ai-serving', SELECTOR, args.context)

    def ready_count(record):
        return next(d['ready_replicas'] for d in record['deployments'] if d['name'] == 'ai-model-serving')

    def idle(record):
        target = next(t for t in record['hpa']['targets'] if t['name'] == 'ai-model-serving')
        return (ready_count(record) == 1 and target['desired_replicas'] == 1
                and target['current_cpu_percent'] is not None
                and target['current_cpu_percent'] < 30)

    jobs = get('jobs', '-n', 'ai-serving')
    if any(job.get('status', {}).get('active', 0) for job in jobs['items']):
        raise RuntimeError('Another Job is active; finish it before benchmarking')
    initial = observation()
    if not idle(initial):
        raise RuntimeError('Require one ready replica and idle valid HPA metrics before starting')
    load_generator = yaml.safe_load((ROOT/'k8s/load-generator.yaml').read_text(encoding='utf-8'))
    k6_image = load_generator['spec']['template']['spec']['containers'][0]['image']
    minikube = shutil.which('minikube') or str(ROOT/'.validation/tools/minikube.exe')
    nodes = get('nodes')
    provenance = {
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'context': args.context,
        'model': json.loads((ROOT/'models/yolov8n-export.json').read_text(encoding='utf-8')),
        'versions': json.loads(command('version', '-o', 'json')),
        'tools': {
            'python': platform.python_version(),
            'kubectl_client': json.loads(command('version', '--client', '-o', 'json')),
            'docker': tool_version(['docker', '--version']),
            'minikube': tool_version([minikube, 'version', '--short']),
            'terraform': tool_version(['terraform', 'version']),
            'k6_image': k6_image,
        },
        'host': {
            'platform': platform.platform(),
            'architecture': platform.machine(),
            'logical_cpu_count': os.cpu_count(),
            'memory_total_mib': host_memory_total_mib(),
        },
        'nodes': nodes,
        'node_capacity': [
            {
                'cpu': node.get('status', {}).get('capacity', {}).get('cpu'),
                'memory': node.get('status', {}).get('capacity', {}).get('memory'),
            }
            for node in nodes.get('items', [])
        ],
        'deployment': get('deployment', 'ai-model-serving', '-n', 'ai-serving'),
        'hpa': get('hpa', 'ai-model-serving', '-n', 'ai-serving'),
        'pods': get('pods', '-n', 'ai-serving', '-l', SELECTOR),
        'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                          for name in ['loadtest/k6-script.js', 'loadtest/sample.jpg', 'k8s/load-generator.yaml',
                                       'scripts/record_scaling.py', 'scripts/run_experiments.py',
                                       'scripts/analyze_experiments.py']},
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'working_tree_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
    }
    # Do not export environment plaintext: the workload uses Secret references.
    for container in provenance['deployment']['spec']['template']['spec']['containers']:
        for entry in container.get('env', []):
            if 'KEY' in entry['name'] or 'TOKEN' in entry['name'] or 'SECRET' in entry['name']:
                entry.pop('value', None)
    write_json(root/'environment.json', provenance)
    # Preserve exact measured sources even when later fixes change the working tree.
    for source in ['loadtest/k6-script.js', 'loadtest/sample.jpg', 'k8s/load-generator.yaml',
                   'scripts/record_scaling.py', 'scripts/run_experiments.py', 'scripts/analyze_experiments.py']:
        (root/Path(source).name).write_bytes((ROOT/source).read_bytes())
    config = json.loads(command('create', 'configmap', 'k6-script', '-n', 'ai-serving',
                                '--from-file=' + str(ROOT/'loadtest/k6-script.js'), '--from-file=' + str(ROOT/'loadtest/sample.jpg'), '--dry-run=client', '-o', 'json'))
    command('apply', '-f', '-', stdin=json.dumps(config))

    for index in range(1, args.runs + 1):
        if not idle(observation()):
            raise RuntimeError('Previous experiment has not recovered to idle')
        name = f'{args.prefix}-{index:02d}'
        folder = root/name
        folder.mkdir()
        job = yaml.safe_load((ROOT/'k8s/load-generator.yaml').read_text(encoding='utf-8'))
        job['metadata']['name'] = name
        job['spec']['template']['metadata']['labels']['experiment'] = name
        write_json(folder/'job.json', job)
        command('create', '-f', '-', stdin=json.dumps(job))
        completed = None
        stable = 0
        began = time.monotonic()
        last_report = 0.0
        successful = False
        try:
            with (folder/'scaling.jsonl').open('x', encoding='utf-8') as handle:
                while True:
                    loop_start = time.monotonic()
                    record = observation()
                    handle.write(json.dumps(record) + '\n')
                    handle.flush()
                    state = get('job', name, '-n', 'ai-serving')
                    status = state.get('status', {})
                    if status.get('failed') or status.get('succeeded'):
                        if completed is None:
                            completed = time.monotonic()
                            log = command('logs', 'job/'+name, '-n', 'ai-serving')
                            (folder/'k6.log').write_text(log, encoding='utf-8')
                            write_json(folder/'job-result.json', state)
                            start, summary = parse_output(log)
                            write_json(folder/'summary.json', summary)
                            write_json(folder/'timing.json', {'start_epoch': start, 'start_source': 'k6 setup log',
                                                           'phase_boundary_uncertainty': 'setup-return overhead plus resource sample interval'})
                            if status.get('failed'):
                                raise RuntimeError('k6 Job failed; preserved summary/logs, inspect thresholds')
                        stable = stable + 1 if idle(record) else 0
                        if time.monotonic() - completed >= 300 and stable >= 3:
                            successful = True
                            break
                        if time.monotonic() - completed > 660:
                            raise RuntimeError('Scale-in/idle recovery not observed within 660 seconds')
                    if time.monotonic() - began > 1600:
                        raise RuntimeError('Experiment exceeded bounded wall time')
                    if time.monotonic() - last_report >= 30:
                        hpa = record['hpa']['targets'][0]
                        print(f'{name}: elapsed={time.monotonic()-began:.0f}s ready={ready_count(record)} '
                              f'CPU={hpa["current_cpu_percent"]}% desired={hpa["desired_replicas"]} '
                              f'stage={"recovery" if completed else "load/startup"}', flush=True)
                        last_report = time.monotonic()
                    time.sleep(max(0, 5 - (time.monotonic() - loop_start)))
            write_json(folder/'completion.json', {'recovered': True, 'samples_path': 'scaling.jsonl'})
            print(f'PASS {name}: load completed and idle recovery observed; raw evidence at {folder}', flush=True)
        finally:
            if not successful:
                try:
                    (folder/'failure-k6.log').write_text(command('logs', 'job/'+name, '-n', 'ai-serving'), encoding='utf-8')
                except Exception:
                    pass
                command('delete', 'job', name, '-n', 'ai-serving', '--ignore-not-found=true', '--wait=false')
    print(f'Experiments complete: {root}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
