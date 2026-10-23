# Phase 4 delivery, CI and demonstration

This phase connects the measured implementation to hosted CI, a reproducible
demo and verified local cleanup. Optional providers and AWS are deferred.

## Changes

- CI runs experiment/accounting tests and PowerShell cleanup guard checks.
  Pushes to `phase4/**` branches can validate a review commit without merging main.
- Cleanup accepts the matching kubectl executable and an explicit namespace
  confirmation for automation, while retaining preview and context checks.
- The demo and final report distinguish live checks, recorded measurements,
  mock-tested optional features and unverified work.

## 1. Verify local prerequisites and tests

From the repository root in Windows PowerShell, follow Phase 1 setup if needed:

```powershell
$env:MODEL_LOCAL_FILES_ONLY = 'true'
$env:API_KEY = ''
$env:ENABLE_QWEN = 'false'
$env:ENABLE_KITTEN = 'false'
$env:ENABLE_GROQ = 'false'
$env:ENABLE_GROQ_TRANSCRIPTION = 'false'
& ./.venv-review/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider
node --test tests/frontend/studio-core.test.mjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-cleanup.ps1
```

Check `$LASTEXITCODE` after each command. Expected: 296 Python tests, 81 frontend
tests, no failures/skips, and the cleanup guard PASS message. Missing model
assets require Phase 1 preparation; a skipped integration test is not acceptance.

## 2. Verify hosted CI for the delivered commit

Open [repository Actions](https://github.com/hrithegreat-gif/CCD-Lab-Project/actions).
Select **CI** on the review branch and compare its full head SHA with:

```powershell
git rev-parse HEAD
git status --short
```

Required green jobs: real sentiment tests, manifest/script validation, Docker
build/container smoke, Terraform offline policy checks, frontend tests and CI
gate. Inspect job logs; a queued, cancelled or unrelated older run is not a pass.
Record the exact run URL and SHA in the verification ledger. Subsequent code
changes need a new run. Workflow dispatch runs CI only; do not dispatch AWS deploy.

## 3. Rehearse the demonstration

Use [DEMO.md](DEMO.md) for the timed walkthrough and
[FINAL_REPORT.md](FINAL_REPORT.md) for the submission narrative. Startup and model
downloads happen before the timed demo. Show live inference and cluster state;
label the Phase 3 plots as recorded measurements. A full three-run benchmark
takes roughly 30–35 minutes and does not fit a 5–8 minute presentation.

## 4. Verify cleanup and recovery

Save raw experiment logs first. Stop port-forwards and watches with Ctrl+C.
Use Minikube's matching client, after confirming the current context:

```powershell
$client = Join-Path $env:USERPROFILE '.minikube/cache/windows/amd64/v1.37.0/kubectl.exe'
& $client config current-context
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/cleanup.ps1 -ExpectedContext ai-serving-local -Kubectl $client
```

Expected: preview names only context `ai-serving-local` and namespace `ai-serving`.
A mismatched current context must stop with no changes. When ready to remove
this application's local resources:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/cleanup.ps1 -ExpectedContext ai-serving-local -Kubectl $client -Apply
```

Type `ai-serving` at the prompt. This deletes the namespace, including its Secret
and completed benchmark Jobs; it does not delete saved evidence or the profile.
Automation may use `-ConfirmNamespace ai-serving` to supply the same confirmation.

```powershell
& $client --context ai-serving-local get namespace ai-serving
```

Expected: `NotFound`. Recover using the existing tested image:

```powershell
$env:API_KEY = 'local-review-only'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-local.ps1
```

Expected: rollout/metrics PASS and zero startup restarts. Repeat the Service smoke
from Phase 2. To release compute after the demo:

```powershell
& ./.validation/tools/minikube.exe stop -p ai-serving-local
& ./.validation/tools/minikube.exe status -p ai-serving-local
```

Expected: stopped host; the status command can return nonzero when stopped.
Resume with `deploy-local.ps1`. Do not assume the API is reachable while stopped.

## Completion evidence

The [verification ledger](VERIFICATION.md) records actual executed results and
the CI run identity. Team names and individual contributions require the team's
confirmation; unassigned entries in the final report are not invented credit.
Optional Qwen/Kitten/Groq, dashboards, async jobs, Floci and AWS remain deferred
until selected and independently accepted.
