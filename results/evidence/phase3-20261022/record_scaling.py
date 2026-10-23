"""Record HPA/Pod scaling observations to a JSONL file. Standard library only.

Runs beside a load test and samples the live cluster through kubectl: HPA
desired/current replicas, Deployment ready counts, per-Pod status and (when
Metrics Server answers) per-Pod CPU/memory. Output goes to results/raw/ (a
git-ignored path) as one JSON object per line so it can be replayed or plotted
later. Never mutates the cluster; never needs AWS credentials or boto3.

Example (while k8s/load-generator.yaml is running):
    python scripts/record_scaling.py --duration 300 --interval 5
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def kubectl_json(kubectl: str, args: list[str], context: str | None = None) -> dict:
    result = subprocess.run(
        [kubectl, *(["--context", context] if context else []), *args, "--request-timeout=10s", "-o", "json"],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"kubectl {args[0]} failed")
    return json.loads(result.stdout)


def sample(kubectl: str, namespace: str, selector: str, context: str | None = None) -> dict:
    record: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}

    hpa = kubectl_json(kubectl, ["get", "hpa", "-n", namespace], context)
    items = hpa.get("items", [])
    record["hpa"] = {
        "count": len(items),
        "targets": [
            {
                "name": item.get("metadata", {}).get("name"),
                "min_replicas": item.get("spec", {}).get("minReplicas"),
                "max_replicas": item.get("spec", {}).get("maxReplicas"),
                "desired_replicas": item.get("status", {}).get("desiredReplicas"),
                "current_replicas": item.get("status", {}).get("currentReplicas"),
                "current_cpu_percent": (
                    item.get("status", {}).get("currentMetrics", [{}])[0]
                    .get("resource", {}).get("current", {}).get("averageUtilization")
                    if item.get("status", {}).get("currentMetrics") else None
                ),
                "target_cpu_percent": (
                    item.get("status", {}).get("currentMetrics", [{}])[0]
                    .get("resource", {}).get("target", {}).get("averageUtilization")
                    if item.get("status", {}).get("currentMetrics") else None
                ),
            }
            for item in items
        ],
    }

    deploy = kubectl_json(kubectl, ["get", "deployment", "-n", namespace], context)
    record["deployments"] = [
        {
            "name": item.get("metadata", {}).get("name"),
            "spec_replicas": item.get("spec", {}).get("replicas"),
            "ready_replicas": item.get("status", {}).get("readyReplicas", 0),
            "updated_replicas": item.get("status", {}).get("updatedReplicas", 0),
            "available_replicas": item.get("status", {}).get("availableReplicas", 0),
        }
        for item in deploy.get("items", [])
    ]

    pods = kubectl_json(kubectl, ["get", "pods", "-n", namespace, "-l", selector], context)
    record["pods"] = [
        {
            "name": item.get("metadata", {}).get("name"),
            "phase": item.get("status", {}).get("phase"),
            "ready": any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in item.get("status", {}).get("conditions", [])
            ),
            "terminating": bool(item.get("metadata", {}).get("deletionTimestamp")),
            "restarts": sum(c.get("restartCount", 0) for c in item.get("status", {}).get("containerStatuses", [])),
        }
        for item in pods.get("items", [])
    ]

    # Metrics Server sample; absent or broken metrics are recorded as a note
    # rather than failing the run (CPU HPA cannot work without them though).
    top = subprocess.run(
        [kubectl, *(["--context", context] if context else []), "top", "pods", "-n", namespace, "-l", selector,
         "--no-headers", "--request-timeout=10s"],
        capture_output=True, text=True, timeout=20,
    )
    if top.returncode == 0 and top.stdout.strip():
        entries = []
        for line in top.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                entries.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
        record["pod_metrics"] = entries
    else:
        record["pod_metrics"] = None
        record["notes"] = (
            "kubectl top pods failed: Metrics Server may be missing or warming up"
        )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--namespace", default="ai-serving")
    parser.add_argument("--label-selector", default="app.kubernetes.io/name=ai-model-serving")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="seconds between samples (default 5)")
    parser.add_argument("--duration", type=float, default=300.0,
                        help="total recording seconds (default 300)")
    parser.add_argument("--once", action="store_true",
                        help="take a single sample and exit")
    parser.add_argument("--kubectl", default="kubectl",
                        help="kubectl binary (default: kubectl on PATH)")
    parser.add_argument("--context", help="Explicit Kubernetes context (recommended)")
    parser.add_argument("--output", default=None,
                        help="JSONL output path (default results/raw/scaling-<ts>.jsonl)")
    args = parser.parse_args(argv)

    if shutil.which(args.kubectl) is None:
        print(f"record_scaling: kubectl binary not found: {args.kubectl}", file=sys.stderr)
        return 1
    if args.interval <= 0 or args.duration <= 0:
        parser.error("--interval and --duration must be positive")

    if args.output:
        output = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = PROJECT_ROOT / "results" / "raw" / f"scaling-{stamp}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)

    deadline = None if args.once else datetime.now(timezone.utc).timestamp() + args.duration
    count = 0
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        while True:
            try:
                record = sample(args.kubectl, args.namespace, args.label_selector, args.context)
            except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                print(f"record_scaling: sample failed: {exc}", file=sys.stderr)
                return 1
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            count += 1
            print(f"record_scaling: {count} sample(s) -> {output}")
            if deadline is None:
                break
            if datetime.now(timezone.utc).timestamp() + args.interval > deadline:
                break
            try:
                time.sleep(args.interval)
            except KeyboardInterrupt:
                break
    print(f"record_scaling: wrote {count} samples to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
