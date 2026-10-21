#!/usr/bin/env bash
# Functional safety tests for the rendered SSM deploy document
# (infra/terraform/ssm_deploy_document.tftpl).
#
# NO AWS: the `aws` CLI is stubbed, `tar` is real. Builds deliberately UNSAFE
# fixture archives and asserts the rendered pipeline refuses them before any
# extraction, and that a safe bundle passes end to end.
#
# Run:  bash tests/deploy_doc_safety_test.sh     (from infra/terraform/)
# Requires: bash, GNU tar, sha256sum, grep, sed, mktemp.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/../ssm_deploy_document.tftpl"
[ -f "$TEMPLATE" ] || { echo "template not found: $TEMPLATE" >&2; exit 1; }

SHA="0123456789abcdef0123456789abcdef01234567"
TEMP_ROOT="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
WORKDIR="$(mktemp -d "$TEMP_ROOT/ccd-lab-safety.XXXXXXXX")"
# Verify the resolved absolute scratch root before any recursive deletion.
WORKDIR="$(cd "$WORKDIR" && pwd -P)"
[ "$(dirname "$WORKDIR")" = "$TEMP_ROOT" ] &&
  [ ! -L "$WORKDIR" ] &&
  [[ "$(basename "$WORKDIR")" == ccd-lab-safety.* ]] ||
  { echo "REFUSING: unexpected test scratch directory: $WORKDIR" >&2; exit 2; }
printf 'Verified isolated test scratch directory: %s\n' "$WORKDIR"
cleanup() {
  if [ "$fail" -eq 0 ]; then
    rm -rf "$WORKDIR"
  else
    echo "  (diagnostics: workdir kept at $WORKDIR)"
  fi
}
trap cleanup EXIT

pass=0
fail=0

ok() { echo "  PASS: $1"; pass=$((pass + 1)); }
ko() {
  echo "  FAIL: $1"
  if [ -f "$WORKDIR/case.out" ]; then
    echo "  ---- case output ----"
    sed 's/^/  | /' "$WORKDIR/case.out"
    echo "  ---- end case output ----"
  fi
  fail=$((fail + 1))
}
expect() { # expect <description> <expected-rc> <actual-rc>
  if [ "$2" -eq "$3" ]; then ok "$1 (rc=$3)"; else ko "$1 (expected rc=$2, got rc=$3)"; fi
}

# ---- stub aws CLI (only what the pipeline may call) ----
mkdir -p "$WORKDIR/bin"
cat >"$WORKDIR/bin/aws" <<'STUB'
#!/usr/bin/env bash
set -u
FAKE_ROOT="${FAKE_BUCKET_ROOT:?}"
echo "aws $*" >> "$FAKE_ROOT/aws-stub.log"
if [ "$1" = "s3" ] && [ "$2" = "cp" ]; then
  src="$3"; dest="$4"
  rel="${src#s3://}"
  rel="${rel#*/}"                     # strip bucket name
  cp "$FAKE_ROOT/$rel" "$dest"
  exit 0
fi
if [ "$1" = "s3" ] && [ "$2" = "sync" ]; then
  exit 0
fi
echo "stub: unexpected aws invocation: $*" >&2
exit 99
STUB
chmod +x "$WORKDIR/bin/aws"

# ---- bundle builder: real payload layout ----
build_bundle() { # build_bundle <output.tgz>
  local stage="$WORKDIR/stage"
  rm -rf "$stage"
  mkdir -p "$stage/scripts"
  cat >"$stage/scripts/deploy-k3s.sh" <<'DEPLOY'
#!/usr/bin/env bash
set -euo pipefail
[ "$1" = "--image" ] || exit 3
echo "image=$2" > "$PWD/deploy_ran"
DEPLOY
  echo "fixture-bundle" >"$stage/manifest.txt"
  tar czf "$1" -C "$stage" scripts manifest.txt
}

# ---- render the template into an executable script for one case ----
render_for() { # render_for <bundle-file> <bundle-sha|WRONGSHA> <out.sh>
  sed -e 's|[$]{aws_region}|ap-south-1|g' \
    -e 's|[$]{project_prefix}|ccd-lab|g' \
    -e 's|[$]{ssm_parameter_prefix}|/ccd-lab|g' \
    -e 's|[$]{ecr_repository}|ccd-lab-serving|g' \
    -e 's|[$]{artifact_bucket}|ccd-lab-artifacts-123456789012|g' \
    -e "s|{{ ObjectKey }}|deploy/$SHA.tar.gz|g" \
    -e "s|{{ BundleSha256 }}|$2|g" \
    -e "s|{{ ImageUri }}|123456789012.dkr.ecr.ap-south-1.amazonaws.com/ccd-lab-serving:$SHA|g" \
    -e "s|/opt/ccd-lab|$WORKDIR/opt/ccd-lab|g" \
    -e 's|[$][$]{|\${|g' \
    "$TEMPLATE" >"$3"
  chmod +x "$3"
}

run_pipeline() { # run_pipeline <bundle.tgz> <sha-or-WRONG>
  local out="$WORKDIR/case.sh"
  render_for "$1" "$2" "$out"
  export PATH="$WORKDIR/bin:$PATH"
  export FAKE_BUCKET_ROOT="$WORKDIR/fake-bucket"
  mkdir -p "$FAKE_BUCKET_ROOT/deploy"
  cp "$1" "$FAKE_BUCKET_ROOT/deploy/$SHA.tar.gz"
  : >"$FAKE_BUCKET_ROOT/aws-stub.log"
  bash "$out" >>"$WORKDIR/case.out" 2>&1
}

echo "== 1. safe bundle passes end to end =="
build_bundle "$WORKDIR/safe.tgz"
SAFE_SHA="$(sha256sum "$WORKDIR/safe.tgz" | cut -d' ' -f1)"
rc=0
run_pipeline "$WORKDIR/safe.tgz" "$SAFE_SHA" || rc=$?
expect "safe bundle exit code" 0 "$rc"
[ -f "$WORKDIR/opt/ccd-lab/deploy-bundles/$SHA/deploy_ran" ] &&
  ok "deploy-k3s.sh ran from extracted work dir" ||
  ko "deploy-k3s.sh did not run"
grep -q "^status=success$" "$WORKDIR/opt/ccd-lab/deploy-bundles/$SHA/results/deploy-summary.txt" &&
  ok "summary records status=success" ||
  ko "summary missing status=success"
grep -q "results/$SHA/" "$WORKDIR/fake-bucket/aws-stub.log" &&
  ok "results synced to per-commit prefix results/$SHA/" ||
  ko "results not synced to per-commit prefix"

echo "== 2. traversal archive ('..'/absolute entries) is rejected =="
echo evil >"$WORKDIR/evil.txt"
tar czf "$WORKDIR/evil.tgz" --transform 's|^|../|' -C "$WORKDIR" evil.txt
EVIL_SHA="$(sha256sum "$WORKDIR/evil.tgz" | cut -d' ' -f1)"
rc=0
run_pipeline "$WORKDIR/evil.tgz" "$EVIL_SHA" || rc=$?
[ "$rc" -ne 0 ] && ok "traversal bundle refused (rc=$rc)" || ko "traversal bundle ACCEPTED"
grep -q "REFUSING" "$WORKDIR/case.out" && ok "refusal reason logged" || ko "no refusal reason logged"
[ ! -e "$WORKDIR/opt/ccd-lab/deploy-bundles/$SHA" ] &&
  ok "nothing extracted for traversal bundle" ||
  ko "extraction happened despite traversal entries"

echo "== 3. symlink entry is rejected =="
rm -rf "$WORKDIR/stage2" && mkdir -p "$WORKDIR/stage2"
ln -s /etc/passwd "$WORKDIR/stage2/link"
tar czf "$WORKDIR/sym.tgz" -C "$WORKDIR/stage2" link
SYM_SHA="$(sha256sum "$WORKDIR/sym.tgz" | cut -d' ' -f1)"
rc=0
run_pipeline "$WORKDIR/sym.tgz" "$SYM_SHA" || rc=$?
[ "$rc" -ne 0 ] && ok "symlink bundle refused (rc=$rc)" || ko "symlink bundle ACCEPTED"
grep -q "symlink/hardlink/device" "$WORKDIR/case.out" && ok "type-check refusal logged" || ko "wrong refusal reason"

echo "== 4. hardlink entry is rejected =="
rm -rf "$WORKDIR/stage3" && mkdir -p "$WORKDIR/stage3"
echo data >"$WORKDIR/stage3/a.txt"
ln "$WORKDIR/stage3/a.txt" "$WORKDIR/stage3/b.txt"
tar czf "$WORKDIR/hard.tgz" -C "$WORKDIR/stage3" a.txt b.txt
HARD_SHA="$(sha256sum "$WORKDIR/hard.tgz" | cut -d' ' -f1)"
rc=0
run_pipeline "$WORKDIR/hard.tgz" "$HARD_SHA" || rc=$?
[ "$rc" -ne 0 ] && ok "hardlink bundle refused (rc=$rc)" || ko "hardlink bundle ACCEPTED"

echo "== 5. corrupt bundle is rejected (fail-closed tar check) =="
# Build a bundle LARGER than the truncation point so the cut really corrupts it.
rm -rf "$WORKDIR/stage4" && mkdir -p "$WORKDIR/stage4/scripts"
cp "$WORKDIR/stage/scripts/deploy-k3s.sh" "$WORKDIR/stage4/scripts/"
head -c 100000 /dev/urandom | base64 >"$WORKDIR/stage4/manifest.txt"
tar czf "$WORKDIR/big.tgz" -C "$WORKDIR/stage4" scripts manifest.txt
head -c 512 "$WORKDIR/big.tgz" >"$WORKDIR/corrupt.tgz"
[ "$(stat -c%s "$WORKDIR/big.tgz")" -gt 512 ] ||
  { echo "  fixture error: big.tgz not bigger than truncation" >&2; exit 1; }
CORRUPT_SHA="$(sha256sum "$WORKDIR/corrupt.tgz" | cut -d' ' -f1)"
rc=0
run_pipeline "$WORKDIR/corrupt.tgz" "$CORRUPT_SHA" || rc=$?
[ "$rc" -ne 0 ] && ok "corrupt bundle refused (rc=$rc)" || ko "corrupt bundle ACCEPTED"
grep -q "unreadable or corrupt" "$WORKDIR/case.out" && ok "corruption refusal logged" || ko "wrong refusal reason"
[ ! -e "$WORKDIR/opt/ccd-lab/deploy-bundles/$SHA" ] &&
  ok "nothing extracted for corrupt bundle" ||
  ko "extraction happened despite corruption"

echo "== 6. SHA-256 mismatch is rejected before any extraction/sync =="
: >"$WORKDIR/fake-bucket/aws-stub.log"
rc=0
run_pipeline "$WORKDIR/safe.tgz" "0000000000000000000000000000000000000000000000000000000000000000" || rc=$?
[ "$rc" -ne 0 ] && ok "sha mismatch refused (rc=$rc)" || ko "sha mismatch ACCEPTED"
[ ! -e "$WORKDIR/opt/ccd-lab/deploy-bundles/$SHA" ] &&
  ok "nothing extracted on sha mismatch" ||
  ko "extraction happened despite sha mismatch"
if grep -q "s3 sync" "$WORKDIR/fake-bucket/aws-stub.log"; then
  ko "results sync ran despite sha mismatch"
else
  ok "no results sync on sha mismatch"
fi
[ ! -e "$WORKDIR/opt/ccd-lab/deploy-bundles/.bundle-listing.txt" ] &&
  ok "screening temp file cleaned up" ||
  ko "screening temp file leaked"

echo
echo "deploy_doc_safety_test: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
