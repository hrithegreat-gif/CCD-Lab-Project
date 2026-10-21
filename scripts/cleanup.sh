#!/usr/bin/env bash
# Delete only the fixed project namespace, never AWS infrastructure/local files.
set -euo pipefail
usage() { printf 'Usage: %s --context EXPECTED_CONTEXT [--apply]\n' "$0"; }
expected_context=''
apply=false
while (($#)); do
  case "$1" in
    --context) [[ $# -ge 2 ]] || { usage; exit 2; }; expected_context="$2"; shift 2 ;;
    --apply) apply=true; shift ;;
    --help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done
[[ -n "$expected_context" ]] || { usage; exit 2; }
actual_context="$(kubectl config current-context)"
[[ "$actual_context" == "$expected_context" ]] || { printf 'Context mismatch. No changes made.\n' >&2; exit 1; }
printf 'Target context: %s; namespace: ai-serving\n' "$expected_context"
printf 'This does NOT stop/delete EC2, disks, S3, ECR, logs or other AWS resources.\n'
if [[ "$apply" != true ]]; then
  printf 'Preview only. Rerun with --apply to request namespace deletion.\n'
  exit 0
fi
read -r -p 'Type ai-serving to delete all resources in this namespace: ' confirmation
[[ "$confirmation" == ai-serving ]] || { printf 'Confirmation did not match. No changes made.\n' >&2; exit 1; }
kubectl --context "$expected_context" delete namespace ai-serving --ignore-not-found=true --wait=true --timeout=120s
