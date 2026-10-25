"""Synthetic unit fixtures for accounting/phase boundaries, never benchmark data."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name+'.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


recorder = load('record_scaling')
runner = load('run_experiments')
analysis = load('analyze_experiments')


def test_no_ready_condition_is_not_ready(monkeypatch):
    responses = iter([{'items': [{'spec': {'metrics': [{'resource': {'target': {'averageUtilization': 70}}}]},
                                'status': {'currentMetrics': [{'resource': {'current': {'averageUtilization': 25}}}]}}]}, {'items': []}, {'items': [{
        'metadata': {'name': 'pending'},
        'status': {'phase': 'Pending', 'conditions': [{'type': 'PodScheduled', 'status': 'True'}]},
    }]}, {'items': [{'metadata': {'name': 'node-1'}}]}])
    monkeypatch.setattr(recorder, 'kubectl_json', lambda *a: next(responses))
    monkeypatch.setattr(recorder.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=''))
    record = recorder.sample('kubectl', 'test', 'app=test')
    assert record['pods'][0]['ready'] is False
    assert record['hpa']['targets'][0]['target_cpu_percent'] == 70
    assert record['hpa']['targets'][0]['current_cpu_percent'] == 25
    assert record['pod_metrics_status']['reason'] == 'empty_or_malformed_output'
    assert record['node_names'] == ['node-1']


def test_top_metrics_records_partial_and_unavailable_reasons(monkeypatch):
    monkeypatch.setattr(
        recorder.subprocess, 'run',
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout='pod-a 500m 100Mi\n'),
    )
    metrics, status = recorder.top_metrics(
        'kubectl', 'pods', 'ai-serving', expected_names={'pod-a', 'pod-b'}
    )
    assert metrics == [{'name': 'pod-a', 'cpu': '500m', 'memory': '100Mi'}]
    assert status == {
        'available': False,
        'reason': 'partial_metrics',
        'expected_count': 2,
        'missing_names': ['pod-b'],
    }
    monkeypatch.setattr(
        recorder.subprocess, 'run',
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout='node-a 250m 12% 1024Mi 30%\n'
        ),
    )
    metrics, status = recorder.top_metrics(
        'kubectl', 'nodes', expected_names={'node-a'}
    )
    assert metrics == [{'name': 'node-a', 'cpu': '250m', 'memory': '1024Mi'}]
    assert status['reason'] == 'complete'
    monkeypatch.setattr(
        recorder.subprocess, 'run',
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout=''),
    )
    metrics, status = recorder.top_metrics('kubectl', 'nodes')
    assert metrics is None
    assert status == {'available': False, 'reason': 'command_failed', 'exit_code': 1}


def test_workload_log_requires_unique_start_and_complete_summary():
    summary={'phases': {}, 'scenario': {}}
    start, result = runner.parse_output('msg="EXPERIMENT_START=1000000"\n' + json.dumps(summary))
    assert start == 1000 and result == summary
    for text in ['EXPERIMENT_START=1000\n{', json.dumps(summary),
                 'EXPERIMENT_START=1000 EXPERIMENT_START=2000\n'+json.dumps(summary)]:
        with pytest.raises(ValueError): runner.parse_output(text)


def test_resource_aggregation_rejects_partial_metrics():
    record={'pods': [{'name': 'a'}, {'name': 'b'}], 'pod_metrics': [{'name':'a','cpu':'500m','memory':'100Mi'}]}
    assert analysis.resource_total(record) is None
    record['pod_metrics'].append({'name':'b','cpu':'0.25','memory':'102400Ki'})
    assert analysis.resource_total(record) == (750,200)
    record.update(node_names=['node-a', 'node-b'],
                  node_metrics=[{'name':'node-a','cpu':'1','memory':'1Gi'}])
    assert analysis.node_resource_total(record) is None
    record['node_metrics'].append({'name':'node-b','cpu':'500m','memory':'512Mi'})
    assert analysis.node_resource_total(record) == (1500,1536)
    partial={'pods': [{'name':'a'}, {'name':'b'}],
             'pod_metrics':[{'name':'a','cpu':'500m','memory':'100Mi'}]}
    assert analysis.metric_gap_reasons([partial], 'pod_metrics', analysis.resource_total) == {
        'not_recorded': 1,
    }


def phase(seconds=180):
    return {'phase_duration_s':seconds,'attempts':10,'completed':9,'interrupted':1,
            'success_200':8,'invalid_200':0,'rate_limited':1,'errors_4xx':0,'errors_5xx':0,'other':0,
            'successful_throughput_rps':8/seconds,'latency_success_mean_ms':12,'latency_success_p95_ms':20}


def test_request_accounting_rejects_fabricated_totals():
    data=phase(); analysis.validate_phase(data)
    data['attempts']=11
    with pytest.raises(ValueError): analysis.validate_phase(data)
    data=phase(); data['successful_throughput_rps']=999
    with pytest.raises(ValueError): analysis.validate_phase(data)


def test_phase_boundaries_and_delays_use_ready_replicas(tmp_path):
    def record(seconds,count):
        return {'timestamp':datetime.fromtimestamp(seconds,timezone.utc).isoformat(),
                'deployments':[{'name':'ai-model-serving','ready_replicas':count}],
                'pods':[{'name':'pod'}], 'pod_metrics':[{'name':'pod','cpu':'500m','memory':'100Mi'}],
                'pod_metrics_status':{'available':True,'reason':'complete'},
                'node_names':['node'], 'node_metrics':[{'name':'node','cpu':'1','memory':'1Gi'}],
                'node_metrics_status':{'available':True,'reason':'complete'}}
    samples=[record(t,n) for t,n in [(1000,1),(1015,1),(1040,1),(1045,1),(1060,2),(1220,3),
                                    (1225,3),(1525,2),(1585,1)]]
    for name,data in [('completion.json',{'recovered':True}),('timing.json',{'start_epoch':1000}),
                      ('summary.json',{'scenario':{'warmup_seconds':15,'baseline_seconds':30,
                       'sustained_seconds':180,'baseline_vus':2,'sustained_vus':8},
                       'phases':{'baseline':phase(30),'sustained':phase()}})]:
        (tmp_path/name).write_text(json.dumps(data))
    (tmp_path/'scaling.jsonl').write_text('\n'.join(json.dumps(s) for s in samples))
    env={'deployment':{'spec':{'template':{'spec':{'containers':[{'image':'test:only'}]}}}},
         'versions':{'serverVersion':{'gitVersion':'test'}}}
    rows,delays,*_=analysis.derive(tmp_path,env)
    assert delays['scale_out_delay_s']==15
    assert delays['scale_in_delay_s']==300
    assert delays['return_to_initial_delay_s']==360
    assert rows[0]['ready_replicas_max']==1  # sustained starts exactly at 1045
    assert rows[1]['error_rate']==.2  # includes 429 and interrupted, not just 5xx
    assert rows[1]['pod_metric_coverage']=='3/3'
    assert rows[1]['node_metric_coverage']=='3/3'
    assert rows[1]['node_memory_mib']==1024
    assert 'throughput_rps' not in rows[2]  # no invented idle latency/throughput
    assert rows[2]['scale_in_delay_s']==300
    compact_root=tmp_path/'compact'
    compact_root.mkdir()
    for filename in ('completion.json','timing.json','summary.json','scaling.jsonl'):
        (compact_root/f'{tmp_path.name}-{filename}').write_bytes((tmp_path/filename).read_bytes())
    replay_rows,*_=analysis.derive(compact_root/tmp_path.name,env,compact=True)
    assert replay_rows==rows
