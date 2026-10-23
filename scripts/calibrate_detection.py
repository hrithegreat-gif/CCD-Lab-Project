"""Measure real detection in temporary hardened one-CPU/1-GiB containers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from PIL import Image
ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='ai-model-serving:detection')
    parser.add_argument('--output', type=Path, default=ROOT/'results/calibration.json')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite calibration evidence')
    small = (ROOT/'loadtest/sample.jpg').read_bytes()
    stream = io.BytesIO()
    with Image.open(ROOT/'loadtest/sample.jpg') as source:
        source.resize((4000,3000)).save(stream, format='JPEG', quality=90)
    large = stream.getvalue()
    evidence = {'image': args.image, 'image_id': docker('image','inspect',args.image,'--format','{{.Id}}'),
                'sample_bytes': len(small), 'large_bytes': len(large), 'large_pixels': 12000000,
                'cpu_limit': 1, 'memory_limit_bytes': 1073741824, 'runs': []}
    for slots in (1,2):
        name = 'detection-calibration-' + secrets.token_hex(6)
        key = secrets.token_hex(24)
        child_env = dict(os.environ, API_KEY=key)
        try:
            subprocess.run(['docker','run','-d','--name',name,'-p','127.0.0.1::8000',
                '--read-only','--cap-drop=ALL','--security-opt','no-new-privileges','--memory','1g','--cpus','1',
                '--tmpfs','/tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001',
                '--tmpfs','/cache:rw,noexec,nosuid,size=256m,uid=0,gid=10001,mode=0770',
                '-e','MODEL_CACHE_DIR=/cache','-e','MODEL_LOCAL_FILES_ONLY=true','-e','API_KEY',
                '-e',f'MAX_CONCURRENT_INFERENCES={slots}', args.image], env=child_env, check=True, capture_output=True)
            port = docker('port',name,'8000/tcp').split(':')[-1]
            base = 'http://127.0.0.1:' + port
            deadline = time.monotonic()+90
            while True:
                try:
                    with urlopen(base+'/health/ready',timeout=2) as response:
                        if response.status == 200: break
                except (OSError,URLError): pass
                if time.monotonic()>deadline: raise RuntimeError('Container readiness timed out')
                time.sleep(.25)
            def request(data):
                began=time.perf_counter()
                req=Request(base+'/api/detect',data=data,headers={'Content-Type':'image/jpeg','Authorization':'Bearer '+key})
                try:
                    with urlopen(req,timeout=30) as response:
                        body=json.load(response)
                        if body.get('model')!='yolov8n' or body.get('count',0)<1: raise RuntimeError('Invalid real detection')
                        status=response.status
                except HTTPError as exc:
                    status=exc.code
                    if status != 429: raise
                return {'status':status,'latency_ms':(time.perf_counter()-began)*1000}
            sequential=[request(small) for _ in range(50)]
            if any(r['status']!=200 for r in sequential): raise RuntimeError('Sequential request failed')
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=2) as pool:
                concurrent=list(pool.map(request,[large]*20))
            elapsed=time.perf_counter()-started
            # Equal wall-clock demand avoids comparing fast 429s with completed work.
            until=time.perf_counter()+10
            def sustained_worker():
                attempts=[]
                while time.perf_counter()<until:
                    attempts.append(request(small))
                return attempts
            started_small=time.perf_counter()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(sustained_worker) for _ in range(2)]
                sustained=[item for future in futures for item in future.result()]
            sustained_seconds=time.perf_counter()-started_small
            stats=docker('stats','--no-stream','--format','{{json .}}',name)
            # Linux high-water RSS catches peaks between Docker stats snapshots.
            proc=docker('exec',name,'cat','/proc/1/status')
            hwm=int(next(line.split()[1] for line in proc.splitlines() if line.startswith('VmHWM:')))
            latencies=sorted(r['latency_ms'] for r in sequential)
            run={'slots':slots,'sequential':sequential,'p50_ms':statistics.median(latencies),
                 'p95_ms':latencies[47], 'concurrent_12mp':concurrent,'concurrent_seconds':elapsed,
                 'successful_rps':sum(r['status']==200 for r in concurrent)/elapsed,
                 'small_sustained_seconds':sustained_seconds, 'small_sustained_successes':sum(r['status']==200 for r in sustained), 'small_sustained_429':sum(r['status']==429 for r in sustained), 'small_sustained_rps':sum(r['status']==200 for r in sustained)/sustained_seconds, 'process_peak_rss_mib':hwm/1024,'docker_stats_after':json.loads(stats)}
            evidence['runs'].append(run)
            print(json.dumps({k:v for k,v in run.items() if k not in ('sequential','concurrent_12mp','docker_stats_after')}),flush=True)
        finally:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__': main()
