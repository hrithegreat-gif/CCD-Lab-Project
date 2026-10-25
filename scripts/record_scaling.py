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


def top_metrics(
    kubectl: str,
    resource: str,
    namespace: str | None = None,
    selector: str | None = None,
    context: str | None = None,
    expected_names: set[str] | None = None,
) -> tuple[list[dict] | None, dict]:
    args = ["top", resource]
    if namespace:
        args.extend(["-n", namespace])
    if selector:
        args.extend(["-l", selector])
    args.extend(["--no-headers", "--request-timeout=10s"])
    result = subprocess.run(
        [kubectl, *(["--context", context] if context else []), *args],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode != 0:
        return None, {"available": False, "reason": "command_failed", "exit_code": result.returncode}

    entries = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if resource == "nodes" and len(parts) >= 4:
            entries.append({"name": parts[0], "cpu": parts[1], "memory": parts[3]})
        elif resource == "pods" and len(parts) >= 3:
            entries.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
    if not entries:
        return None, {"available": False, "reason": "empty_or_malformed_output"}

    returned_names = {entry["name"] for entry in entries}
    missing_names = sorted(expected_names - returned_names) if expected_names is not None else []
    status = {
        "available": not missing_names,
        "reason": "partial_metrics" if missing_names else "complete",
    }
    if expected_names is not None:
        status["expected_count"] = len(expected_names)
        status["missing_names"] = missing_names
    return entries, status


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
                    item.get("spec", {}).get("metrics", [{}])[0]
                    .get("resource", {}).get("target", {}).get("averageUtilization")
                    if item.get("spec", {}).get("metrics") else None
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
    live_pod_names = {
        item.get("metadata", {}).get("name")
        for item in pods.get("items", [])
        if not item.get("metadata", {}).get("deletionTimestamp")
    }
    live_pod_names.discard(None)
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

    record["pod_metrics"], record["pod_metrics_status"] = top_metrics(
        kubectl, "pods", namespace, selector, context, live_pod_names
    )
    nodes = kubectl_json(kubectl, ["get", "nodes"], context)
    node_names = {
        item.get("metadata", {}).get("name")
        for item in nodes.get("items", [])
        if item.get("metadata", {}).get("name")
    }
    record["node_names"] = sorted(node_names)
    record["node_metrics"], record["node_metrics_status"] = top_metrics(
        kubectl, "nodes", context=context, expected_names=node_names
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
