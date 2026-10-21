#!/usr/bin/env bash
# Guarded stop/start/status for the CCD Lab k3s node via the AWS CLI.
#
# Requires an EXPLICIT --instance-id and --region: no discovery, no wildcards,
# no default target. Real stop/start additionally require --yes, or --dry-run
# to validate authorization without changing anything. Uses only the AWS CLI
# (no SDK) with the caller's already-configured operator identity; this script
# never looks for or stores credentials.
#
# Usage:
#   aws-instance.sh status --instance-id i-0123456789abcdef0 --region ap-south-1
#   aws-instance.sh stop   --instance-id i-0123456789abcdef0 --region ap-south-1 --dry-run
#   aws-instance.sh stop   --instance-id i-0123456789abcdef0 --region ap-south-1 --yes
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  aws-instance.sh status --instance-id i-xxxx --region ap-south-1
  aws-instance.sh stop   --instance-id i-xxxx --region ap-south-1 --dry-run
  aws-instance.sh stop   --instance-id i-xxxx --region ap-south-1 --yes
  aws-instance.sh start  --instance-id i-xxxx --region ap-south-1 --yes

Options:
  --instance-id   explicit EC2 instance id (i- followed by 8-17 hex chars). Required.
  --region        AWS region. Required.
  --dry-run       validate authorization only (aws ec2 ... --dry-run); no state change.
  --yes           explicit acknowledgment for a real stop/start.
  -h, --help      show this help.
USAGE
}

action=""
instance=""
region=""
dry_run=0
confirmed=0

while [ $# -gt 0 ]; do
  case "$1" in
    stop | start | status)
      action="$1"
      ;;
    --instance-id)
      [ $# -ge 2 ] || { echo "--instance-id needs a value" >&2; exit 2; }
      instance="$2"
      shift
      ;;
    --region)
      [ $# -ge 2 ] || { echo "--region needs a value" >&2; exit 2; }
      region="$2"
      shift
      ;;
    --dry-run) dry_run=1 ;;
    --yes) confirmed=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

[ -n "$action" ] || { echo "Missing action (stop|start|status)." >&2; usage; exit 2; }
[ -n "$instance" ] || { echo "Missing --instance-id." >&2; usage; exit 2; }
[ -n "$region" ] || { echo "Missing --region." >&2; usage; exit 2; }

case "$instance" in
  i-[0-9a-f]*) ;;
  *)
    echo "Refusing: instance id must look like i-0123456789abcdef0 (explicit id only; no discovery)." >&2
    exit 2
    ;;
esac

command -v aws >/dev/null 2>&1 || {
  echo "AWS CLI not found on PATH. Install AWS CLI v2 and authenticate with your operator identity first." >&2
  exit 2
}

if [ "$action" != "status" ] && [ "$dry_run" -ne 1 ] && [ "$confirmed" -ne 1 ]; then
  echo "Refusing to $action $instance. Re-run with --yes to proceed, or --dry-run to validate authorization only." >&2
  exit 2
fi

case "$action" in
  status)
    [ "$dry_run" -eq 1 ] && echo "Note: --dry-run is ignored for status." >&2
    aws ec2 describe-instances \
      --instance-ids "$instance" \
      --region "$region" \
      --query 'Reservations[].Instances[].{InstanceId:InstanceId,State:State.Name,Type:InstanceType,PublicIp:PublicIpAddress,PrivateIp:PrivateIpAddress}' \
      --output table
    ;;
  stop | start)
    if [ "$dry_run" -eq 1 ]; then
      out="$(aws ec2 "$action-instances" --instance-ids "$instance" --region "$region" --dry-run --output json 2>&1 || true)"
      case "$out" in
        *DryRunOperation*)
          echo "DRY RUN OK: caller is authorized to $action $instance in $region (nothing was changed)."
          ;;
        *)
          printf '%s\n' "$out" >&2
          exit 1
          ;;
      esac
    else
      echo "$action-ing instance $instance in $region ..."
      aws ec2 "$action-instances" --instance-ids "$instance" --region "$region" --output json
      echo "Request submitted. Reminder: every start re-arms the auto-stop timer (default 4h) and assigns a NEW ephemeral public IPv4."
    fi
    ;;
esac
