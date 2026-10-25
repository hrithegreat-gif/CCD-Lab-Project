"""Validate recorded experiments and derive CSV rows and scaling figures.

No synthetic measurements: missing samples/latencies remain blank, and a missing
summary/completion file stops analysis. Resource averages are sums across the
serving Pods per sample, averaged over the corresponding phase (not per Pod).
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import hashlib
import json
import statistics
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def epoch(record):
    return datetime.fromisoformat(record['timestamp']).timestamp()


def replicas(record):
    return next(d['ready_replicas'] for d in record['deployments'] if d['name'] == 'ai-model-serving')


def cpu_millicores(value):
    for suffix, multiplier in [('n', 1e-6), ('u', .001), ('m', 1)]:
        if value.endswith(suffix):
            return float(value[:-len(suffix)]) * multiplier
    return float(value) * 1000


def memory_mib(value):
    for suffix, multiplier in [('Ki', 1/1024), ('Mi', 1), ('Gi', 1024), ('Ti', 1024**2),
                               ('K', 1000/1024**2), ('M', 1000**2/1024**2), ('G', 1000**3/1024**2)]:
        if value.endswith(suffix):
            return float(value[:-len(suffix)]) * multiplier
    return float(value) / 1024**2


def resource_total(record):
    live = {p['name'] for p in record['pods'] if not p.get('terminating')}
    metrics = record.get('pod_metrics')
    if not metrics or not live or not live.issubset({m['name'] for m in metrics}):
        return None
    relevant = [m for m in metrics if m['name'] in live]
    return sum(cpu_millicores(m['cpu']) for m in relevant), sum(memory_mib(m['memory']) for m in relevant)


def node_resource_total(record):
    expected = set(record.get('node_names', []))
    metrics = record.get('node_metrics')
    if not expected or not metrics or not expected.issubset({m['name'] for m in metrics}):
        return None
    relevant = [m for m in metrics if m['name'] in expected]
    return sum(cpu_millicores(m['cpu']) for m in relevant), sum(memory_mib(m['memory']) for m in relevant)


def metric_gap_reasons(records, metrics_name, total_fn):
    reasons = Counter()
    for record in records:
        if total_fn(record) is not None:
            continue
        status = record.get(metrics_name + '_status', {})
        reasons[status.get('reason', 'not_recorded')] += 1
    return dict(sorted(reasons.items()))


def run_file(folder, filename, compact):
    path = folder.parent / f'{folder.name}-{filename}' if compact else folder / filename
    return read_json(path)


def validate_phase(data):
    completed = sum(data[k] for k in ['success_200', 'invalid_200', 'rate_limited', 'errors_4xx', 'errors_5xx', 'other'])
    if completed != data['completed'] or data['attempts'] != completed + data['interrupted']:
        raise ValueError('Inconsistent request accounting')
    if data['interrupted'] < 0 or data['phase_duration_s'] <= 0:
        raise ValueError('Invalid request counts or duration')
    expected = data['success_200'] / data['phase_duration_s']
    if abs(expected - data['successful_throughput_rps']) > 1e-6:
        raise ValueError('Successful throughput disagrees with counts/duration')
    if data['success_200'] and (data['latency_success_mean_ms'] is None or data['latency_success_p95_ms'] is None):
        raise ValueError('Successful requests have no measured latency')


def derive(folder: Path, environment: dict, compact: bool = False):
    if not run_file(folder, 'completion.json', compact).get('recovered'):
        raise ValueError('Incomplete recovery')
    summary = run_file(folder, 'summary.json', compact)
    start = run_file(folder, 'timing.json', compact)['start_epoch']
    samples_path = folder.parent / f'{folder.name}-scaling.jsonl' if compact else folder / 'scaling.jsonl'
    samples = [json.loads(line) for line in samples_path.read_text(encoding='utf-8').splitlines() if line]
    if len(samples) < 3 or any(epoch(b) <= epoch(a) for a, b in zip(samples, samples[1:])):
        raise ValueError('Missing or non-monotonic resource samples')
    scenario = summary['scenario']
    baseline_start = start + scenario['warmup_seconds']
    sustained_start = baseline_start + scenario['baseline_seconds']
    load_end = sustained_start + scenario['sustained_seconds']
    baseline = [s for s in samples if baseline_start <= epoch(s) < sustained_start]
    sustained = [s for s in samples if sustained_start <= epoch(s) < load_end]
    recovery = [s for s in samples if epoch(s) >= load_end]
    if not baseline or not sustained or not recovery:
        raise ValueError('A measured phase has no resource samples')
    initial = replicas(baseline[0])
    peak = max(replicas(s) for s in sustained)
    extra = next((s for s in sustained if replicas(s) > initial), None)
    first_down = next((s for s in recovery if replicas(s) < peak), None) if peak > initial else None
    back = next((s for s in recovery if replicas(s) == initial), None) if peak > initial else None
    delays = {
        'scale_out_delay_s': round(epoch(extra)-sustained_start, 3) if extra else None,
        'scale_in_delay_s': round(epoch(first_down)-load_end, 3) if first_down else None,
        'return_to_initial_delay_s': round(epoch(back)-load_end, 3) if back else None,
        'peak_ready_replicas': peak,
        'baseline_ready_min': min(replicas(s) for s in baseline),
        'baseline_ready_max': max(replicas(s) for s in baseline),
        'max_sample_gap_s': round(max(epoch(b)-epoch(a) for a,b in zip(samples,samples[1:])),3),
        'resource_sampling': 'Pod and node metrics are retrieval-time values; top exposes windowed values without source timestamps. Missing data is never zero.',
    }
    deployment = environment['deployment']['spec']['template']['spec']['containers'][0]
    host = environment.get('host', {})
    hardware = environment.get('hardware', 'Windows 11 / Docker WSL2; node limit 4 CPU / 6 GiB')
    if host:
        capacity = environment.get('node_capacity', [])
        node_description = ''
        if capacity:
            node_description = '; node capacity ' + ', '.join(
                f"{node.get('cpu', 'unknown')} CPU / {node.get('memory', 'unknown')}"
                for node in capacity
            )
        hardware = (
            f"{host.get('platform', 'unknown host')}; "
            f"{host.get('logical_cpu_count', 'unknown')} logical CPU; "
            f"{host.get('memory_total_mib', 'unknown')} MiB RAM{node_description}"
        )
    rows = []
    for phase, selected in [('baseline',baseline), ('sustained',sustained), ('recovery',recovery)]:
        resources = [r for s in selected if (r := resource_total(s)) is not None]
        node_resources = [r for s in selected if (r := node_resource_total(s)) is not None]
        pod_gaps = metric_gap_reasons(selected, 'pod_metrics', resource_total)
        node_gaps = metric_gap_reasons(selected, 'node_metrics', node_resource_total)
        delays[phase+'_complete_metric_samples'] = len(resources)
        delays[phase+'_samples'] = len(selected)
        delays[phase+'_complete_node_metric_samples'] = len(node_resources)
        delays[phase+'_pod_metric_gap_reasons'] = pod_gaps
        delays[phase+'_node_metric_gap_reasons'] = node_gaps
        row = {
            'run_id': folder.name, 'scenario': phase,
            'hardware': hardware,
            'kubernetes_version': environment['versions']['serverVersion']['gitVersion'],
            'model_revision': environment.get('model', {}).get('sha256', 'unrecorded'),
            'image_tag': deployment['image'],
            'pod_cpu_millicores': round(statistics.mean(r[0] for r in resources),3) if resources else '',
            'pod_memory_mib': round(statistics.mean(r[1] for r in resources),3) if resources else '',
            'pod_metric_coverage': f'{len(resources)}/{len(selected)}',
            'node_cpu_millicores': round(statistics.mean(r[0] for r in node_resources),3) if node_resources else '',
            'node_memory_mib': round(statistics.mean(r[1] for r in node_resources),3) if node_resources else '',
            'node_metric_coverage': f'{len(node_resources)}/{len(selected)}',
            'ready_replicas_min': min(replicas(s) for s in selected),
            'ready_replicas_max': max(replicas(s) for s in selected),
            'scale_out_delay_s': delays['scale_out_delay_s'] if phase == 'sustained' else '',
            'scale_in_delay_s': delays['scale_in_delay_s'] if phase == 'recovery' else '',
        }
        notes = ['resource means use only complete retrieval-time samples; values may lag workload']
        if pod_gaps:
            notes.append('Pod metric gaps: ' + ', '.join(f'{reason}={count}' for reason, count in pod_gaps.items()))
        if node_gaps:
            notes.append('Node metric gaps: ' + ', '.join(f'{reason}={count}' for reason, count in node_gaps.items()))
        if len(resources) < .8 * len(selected):
            notes.append('low metrics coverage: resource mean is not representative of entire phase')
        if phase != 'recovery':
            data = summary['phases'][phase]
            validate_phase(data)
            row.update(concurrency=scenario[phase+'_vus'], duration_s=data['phase_duration_s'],
                       total_requests=data['attempts'], successful_requests=data['success_200'],
                       throughput_rps=round(data['successful_throughput_rps'],3),
                       mean_latency_ms=round(data['latency_success_mean_ms'],3) if data['latency_success_mean_ms'] is not None else '',
                       p95_latency_ms=round(data['latency_success_p95_ms'],3) if data['latency_success_p95_ms'] is not None else '',
                       error_rate=round((data['attempts']-data['success_200'])/data['attempts'],6) if data['attempts'] else '')
            notes.append(f"429={data['rate_limited']}; interrupted={data['interrupted']}; invalid200={data['invalid_200']}; "
                         f"other4xx={data['errors_4xx']}; 5xx={data['errors_5xx']}; other={data['other']}; error_rate includes all unsuccessful attempts")
        else:
            row.update(concurrency=0, duration_s=round(epoch(selected[-1])-load_end,3))
            notes.append(f"no recovery traffic; latency/throughput blank; return_to_initial_s={delays['return_to_initial_delay_s']}")
        if delays['baseline_ready_max'] != 1:
            notes.append('baseline was not fixed at one ready replica')
        row['notes'] = '; '.join(notes)
        rows.append(row)
    return rows, delays, samples, start, sustained_start, load_end


def plot_run(path, samples, start, sustained_start, end):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(5,1,figsize=(10,12),sharex=True,layout='constrained')
    x = [epoch(s)-start for s in samples]
    totals = [resource_total(s) for s in samples]
    node_totals = [node_resource_total(s) for s in samples]
    axes[0].step(x,[replicas(s) for s in samples],where='post',label='Ready replicas')
    axes[0].step(x,[s['hpa']['targets'][0]['desired_replicas'] for s in samples],where='post',linestyle='--',label='HPA desired')
    axes[0].set_ylabel('Replicas'); axes[0].set_yticks([1,2,3]); axes[0].legend(loc='upper right')
    axes[1].plot(x,[r[0] if r else float('nan') for r in totals],color='#c14b27')
    axes[1].set_ylabel('Total serving CPU\n(millicores)')
    axes[2].plot(x,[r[1] if r else float('nan') for r in totals],color='#267258')
    axes[2].set_ylabel('Total serving memory\n(MiB)')
    axes[3].plot(x,[r[0] if r else float('nan') for r in node_totals],color='#6f42c1')
    axes[3].set_ylabel('Total node CPU\n(millicores)')
    axes[4].plot(x,[r[1] if r else float('nan') for r in node_totals],color='#168a9a')
    axes[4].set_ylabel('Total node memory\n(MiB)')
    axes[4].set_xlabel('Seconds since workload start')
    for axis in axes:
        axis.axvspan(0,15,color='#999999',alpha=.1)
        axis.axvspan(sustained_start-start,end-start,color='#e8b64d',alpha=.18)
        axis.axvline(end-start,color='#777777',linestyle=':')
        axis.grid(alpha=.2)
    fig.suptitle(path.stem + '\nWarm-up (gray), sustained load (yellow), then recovery; metric gaps are missing data')
    fig.savefig(path,dpi=160)
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment',type=Path,
                        help='raw experiment directory or committed compact evidence directory')
    parser.add_argument('--output',type=Path,default=ROOT/'results/performance.csv')
    args=parser.parse_args()
    environment=read_json(args.experiment/'environment.json')
    compact=not any(args.experiment.glob('*/completion.json'))
    if compact:
        run_ids=sorted(
            path.name[:-len('-completion.json')]
            for path in args.experiment.glob('*-completion.json')
        )
        folders=[args.experiment/run_id for run_id in run_ids]
    else:
        folders=sorted(p.parent for p in args.experiment.glob('*/completion.json'))
    if not folders: raise ValueError('No completed experiments found')
    rows=[]; measurements={}; hashes={}
    figures=ROOT/'results/figures'; figures.mkdir(exist_ok=True)
    evidence=ROOT/'results/evidence'/args.experiment.name; evidence.mkdir(parents=True,exist_ok=True)
    for source in ['k6-script.js','sample.jpg','load-generator.yaml','record_scaling.py',
                   'run_experiments.py','analyze_experiments.py']:
        original=args.experiment/source
        if original.exists():
            (evidence/source).write_bytes(original.read_bytes())
        elif source == 'analyze_experiments.py':
            (evidence/source).write_bytes(Path(__file__).read_bytes())
    compact_environment={
        'recorded_at':environment.get('recorded_at'),
        'model':environment.get('model', {}),
        'versions':{'serverVersion':environment['versions']['serverVersion']},
        'tools':environment.get('tools', {}),
        'host':environment.get('host', {}),
        'node_capacity':environment.get('node_capacity', []),
        'deployment':{
            'spec':{'template':{'spec':{'containers':[{
                'image':environment['deployment']['spec']['template']['spec']['containers'][0]['image'],
                'resources':environment['deployment']['spec']['template']['spec']['containers'][0].get('resources', {}),
            }]}}}
        },
        'source_sha256':environment.get('source_sha256', {}),
        'git_head':environment.get('git_head'),
        'working_tree_dirty':environment.get('working_tree_dirty'),
    }
    (evidence/'environment.json').write_text(
        json.dumps(compact_environment,indent=2)+'\n',encoding='utf-8'
    )
    for folder in folders:
        derived,delays,samples,start,high,end=derive(folder,environment,compact)
        rows.extend(derived); measurements[folder.name]=delays
        plot_run(figures/(folder.name+'.png'),samples,start,high,end)
        # Preserve compact, non-secret measurements in Git, beyond ignored raw logs.
        for filename in ['summary.json','timing.json','scaling.jsonl','completion.json']:
            source=folder.parent/f'{folder.name}-{filename}' if compact else folder/filename
            data=source.read_bytes()
            hashes[folder.name+'/'+filename]=hashlib.sha256(data).hexdigest()
            destination=evidence/(folder.name+'-'+filename)
            if source.resolve() != destination.resolve():
                destination.write_bytes(data)
    fields=next(csv.reader((ROOT/'results/performance.csv').open(newline='',encoding='utf-8-sig')))
    # Keep unrelated experiments; rerunning analysis replaces only these run IDs.
    previous=[]
    if args.output.exists():
        with args.output.open(newline='',encoding='utf-8-sig') as handle:
            previous=[r for r in csv.DictReader(handle) if r['run_id'] not in measurements]
    with args.output.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields)
        writer.writeheader(); writer.writerows(previous+rows)
    (evidence/'derived.json').write_text(json.dumps({'runs':measurements,'sha256':hashes,
        'source_sha256':environment['source_sha256'],'git_head':environment['git_head'],
        'working_tree_dirty':environment['working_tree_dirty'],
        'analyzer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'image':environment['deployment']['spec']['template']['spec']['containers'][0]['image'],
        'container_resources':environment['deployment']['spec']['template']['spec']['containers'][0].get('resources', {}),
        'tools':environment.get('tools', {}),
        'host':environment.get('host', {})},indent=2)+'\n',encoding='utf-8')
    print(json.dumps(measurements,indent=2))
    print(f'Wrote {len(rows)} measured rows to {args.output}')


if __name__=='__main__': main()
