# CI/CD (GitHub Actions)

Two runnable workflows, not yet executed on GitHub. Routine CI uses no AWS
credentials and makes no AWS changes. The separately approved manual deployment
workflow **does** push to ECR, upload to S3, invoke SSM, and update Kubernetes.
Neither workflow runs `terraform apply`, starts instances, or creates the cloud
environment; those remain explicit operator actions.

## .github/workflows/ci.yml (push to main, PRs, manual)

| Job | What it actually does |
| --- | --- |
| tests | installs pinned requirements-dev.txt, `pip check`, `scripts/prepare_model.py --verify` (real download + real predictions), full `pytest tests/` including the real-model integration suite |
| manifests | offline artifact validation: `pytest tests/test_manifests.py --noconftest` (PyYAML only), render checks for cloud/local modes, `bash -n` of the shell scripts, `node --check` of the k6 script |
| docker | `docker build --no-cache`, hardened container run mirroring the k8s runtime (read-only rootfs, dropped capabilities, no-new-privileges, 1 CPU / 1 GiB caps, tmpfs `/tmp` + `/cache` owned by uid 10001, `MODEL_LOCAL_FILES_ONLY=true`, `MAX_REQUEST_BYTES=65536`), non-root uid check, real `scripts/smoke_test.py` over HTTP, `/health/live` + `/metrics` reachability |
| terraform | `terraform -chdir=infra/terraform fmt -check -recursive`, `init -backend=false -input=false`, `validate`, `test` with mock providers, and functional archive safety tests using a stubbed AWS CLI — no cloud credentials or resource creation |
| ci-success | gate job that fails unless every job above succeeded |

The smoke gate measures nothing about performance; it verifies real inference
responses. No numbers are printed or claimed beyond the smoke assertions.

## .github/workflows/deploy-aws.yml (manual dispatch only)

Requires: GitHub environment `aws-demo` (approval configured by the operator)
with environment variables `AWS_ROLE_ARN`, `ECR_REPOSITORY` (bare repo name),
`EC2_INSTANCE_ID`, `ARTIFACT_BUCKET`, `SSM_DEPLOY_DOCUMENT`; OIDC role with
`id-token: write`; region fixed `ap-south-1`. Deployments are additionally
gated by an enforced API check (Actions runs API with `actions: read`) that a
successful ci.yml run exists for the exact commit — the check runs before any
AWS credentials are requested and fails closed.

Flow:

1. `build-smoke-push`: CI gate (above), then OIDC login, derive the immutable
   image URI
   `<account>.dkr.ecr.ap-south-1.amazonaws.com/<ECR_REPOSITORY>:<full-40-char-sha>`,
   docker build, the same hardened container smoke + non-root check as CI,
   then push that single immutable tag. ECR `docker login` uses the bare
   registry host, not the full image URI.
2. `upload-bundle`: tar.gz of exactly `k8s/`, `loadtest/`,
   `scripts/deploy-k3s.sh`, `scripts/render_k8s.py`,
   `scripts/record_scaling.py`, `scripts/entrypoint.sh`,
   `scripts/smoke_test.py` -> `deploy/<sha>.tar.gz` (no secrets, no .git, no
   caches; paths are explicit, not directory-wide), SHA-256 over it, upload to
   `s3://<ARTIFACT_BUCKET>/deploy/<sha>.tar.gz`.
3. `deploy`: `aws ssm send-command` with the infra document
   (`vars.SSM_DEPLOY_DOCUMENT`) targeting `vars.EC2_INSTANCE_ID` and parameters
   `ObjectKey=deploy/<sha>.tar.gz`, `BundleSha256=<64 lower hex>`,
   `ImageUri=<ECR>:<sha>`; then a bounded poll (10s interval, 10 min cap) on
   `get-command-invocation` that retries only the `InvocationDoesNotExist`
   eventual-consistency race and fails immediately on any other error —
   Success passes, anything else (or timeout) fails the run and prints the
   command's stdout/stderr tails.

The SSM document (owned by the infrastructure agent) verifies the bundle
SHA-256, extracts to `/opt/ccd-lab/deploy-bundles/<sha>`, and runs
`scripts/deploy-k3s.sh --image <ImageUri>` with the bootstrap environment
(`AWS_REGION`, `SSM_PARAMETER_PREFIX`, `ECR_REPOSITORY`, `ARTIFACT_BUCKET`);
afterwards it syncs `./results/` to `s3://<bucket>/results/<commit-sha>/`, including a deployment status summary. Post-extraction deployment failures trigger a best-effort results sync while retaining the original nonzero exit code. Different commits have separate prefixes; re-deploying the **same** commit can overwrite that commit's results, so archive prior evidence first.

## What this pipeline deliberately does not do

- No image `latest` tags; every deploy is one immutable commit SHA tag.
- No secret material in git or in CI logs: Kubernetes Secrets are created on
  the instance from SSM via stdin; bundle upload paths are explicit.
- No load/performance runs in CI (k6 runs against the deployed instance, see
  loadtest/README.md); CI never fabricates measurements.
- No Terraform mutation and no instance lifecycle changes from CI.

## Verification status (honest record)

- Workflows are configured but have NOT been executed on GitHub yet (no push
  from the dev machine during implementation). First green run required
  before any deploy; deploy additionally requires the `aws-demo` approval.
- Infrastructure files exist and have passed local offline checks; see the
  [verification ledger](VERIFICATION.md). This is not a successful AWS plan/apply.
- ci.yml.example was removed when ci.yml landed; scaffold-manifest.json is
  updated by the parent.
