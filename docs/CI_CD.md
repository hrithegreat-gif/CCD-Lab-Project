# CI and delivery

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main`, `phase4/**`, and
`feature/object-detection`, pull requests, and manual dispatch. Its jobs cover
real detection tests, offline manifest/script checks, Docker build and hardened
smoke, Terraform formatting/validation/mock-policy tests, and Studio contract
tests. `ci-success` fails unless all required jobs succeed.

The real test job downloads and verifies the pinned model release asset. Docker
builds also bake and verify the model. These checks need network access to the
public asset; they do not deploy AWS resources.

## Manual browser release gate

Real browser acceptance is an explicit manual release gate; it is not part of
hosted CI. Run it after the Python and frontend contract suites when changing
Studio behavior. Use an installed Microsoft Edge browser and the pinned
Playwright package in the project environment:

```powershell
.\.venv-review\Scripts\python.exe -m pip install playwright==1.58.0
```

Start a loopback-only service with a throwaway nonempty key in one PowerShell
window:

```powershell
$env:API_KEY = [guid]::NewGuid().ToString('N')
.\.venv-review\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --workers 1 --no-access-log
```

In a second window, set the same temporary key and run the gate:

```powershell
$env:API_KEY = '<the same throwaway key>'
.\.venv-review\Scripts\python.exe scripts\test_studio_browser.py `
  --base-url http://127.0.0.1:8003 --channel msedge `
  --output results\evidence\browser-YYYYMMDD
```

The script waits for real model readiness and checks three sample detections,
file upload, visible overlays, the accessible detection table and keyboard
tabs, annotated PNG download, mobile overflow, and tab-session-only key/history
behavior. A passing command must end with `PASS: real browser ...`. Keep the
server bound to loopback, stop it after the test, and review screenshots for
private information before adding them to a submission. Record the date,
commit, browser version, result, and evidence path in the evidence ledger.

## AWS delivery

`.github/workflows/deploy-aws.yml` is manually dispatched and uses the protected
`aws-demo` environment and GitHub OIDC. It checks CI for the exact commit,
builds and smoke-tests the container, pushes an immutable commit-SHA image to
ECR, uploads a checksummed deployment bundle to S3, and invokes the scoped SSM
document. Terraform apply and starting a stopped EC2 instance are operator
actions, not workflow actions.

No hosted CI result or successful cloud delivery is asserted here; see
[the evidence ledger](EVIDENCE.md).