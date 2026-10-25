# Fixed local profile only. Does not touch AWS or select a remote Kubernetes context.
[CmdletBinding()]
param(
    [string]$Image = 'ai-model-serving:detection',
    [ValidateRange(2,16)][int]$Cpus = 4,
    [ValidateRange(4096,32768)][int]$MemoryMb = 6144
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$profileName = 'ai-serving-local'
Push-Location $projectRoot
try {
    $python = Join-Path $projectRoot '.venv-review/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/setup-phase1.ps1 first.' }
    $minikube = Join-Path $projectRoot '.validation/tools/minikube.exe'
    if (-not (Test-Path -LiteralPath $minikube)) {
        $minikube = (Get-Command minikube -ErrorAction Stop).Source
    }
    if ([string]::IsNullOrWhiteSpace($env:API_KEY)) { throw 'Set API_KEY to a nonempty local test key before deployment.' }
    & docker image inspect $Image --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0) { throw 'Build and test the image with scripts/test-container.ps1 first.' }
    & $minikube start -p $profileName --driver=docker --kubernetes-version=v1.37.0 --cpus=$Cpus --memory=$MemoryMb
    if ($LASTEXITCODE -ne 0) { throw 'Local cluster startup failed.' }
    & $minikube -p $profileName image load $Image
    if ($LASTEXITCODE -ne 0) { throw 'Local image import failed.' }
    & $minikube -p $profileName addons enable metrics-server
    if ($LASTEXITCODE -ne 0) { throw 'Metrics Server addon failed.' }
    & $minikube -p $profileName kubectl -- --context $profileName apply -f k8s/namespace.yaml
    if ($LASTEXITCODE -ne 0) { throw 'Namespace creation failed.' }
    # Send secret data via stdin rather than arguments or a file.
    $secret = @{
        apiVersion='v1'; kind='Secret'; type='Opaque'
        metadata=@{name='serving-secrets'; namespace='ai-serving'}
        stringData=@{'api-key'=$env:API_KEY}
    } | ConvertTo-Json -Depth 5 -Compress
    $secret | & $minikube -p $profileName kubectl -- --context $profileName apply --server-side --field-manager=local-setup -f -
    if ($LASTEXITCODE -ne 0) { throw 'Local secret apply failed.' }
    & $python scripts/render_k8s.py --image $Image --pull-secret none --output .validation/rendered-local.yaml
    if ($LASTEXITCODE -ne 0) { throw 'Manifest rendering failed.' }
    & $minikube -p $profileName kubectl -- --context $profileName apply -f .validation/rendered-local.yaml
    if ($LASTEXITCODE -ne 0) { throw 'Manifest apply failed.' }
    # Restart also refreshes environment secrets when rerunning with an unchanged image.
    & $minikube -p $profileName kubectl -- --context $profileName -n ai-serving rollout restart deployment/ai-model-serving
    if ($LASTEXITCODE -ne 0) { throw 'Rollout restart failed.' }
    & $minikube -p $profileName kubectl -- --context $profileName -n ai-serving rollout status deployment/ai-model-serving --timeout=240s
    if ($LASTEXITCODE -ne 0) { throw 'Application rollout failed; inspect Pod events/logs.' }
    & $minikube -p $profileName kubectl -- --context $profileName -n kube-system rollout status deployment/metrics-server --timeout=240s
    if ($LASTEXITCODE -ne 0) { throw 'Metrics Server rollout failed.' }
    & $minikube -p $profileName kubectl -- --context $profileName -n ai-serving wait --for=condition=ScalingActive hpa/ai-model-serving --timeout=240s
    if ($LASTEXITCODE -ne 0) { throw 'HPA has no usable metrics; inspect HPA/metrics-server.' }
    & $minikube -p $profileName kubectl -- --context $profileName top nodes
    if ($LASTEXITCODE -ne 0) { throw 'Node metrics unavailable.' }
    & $minikube -p $profileName kubectl -- --context $profileName -n ai-serving top pods
    if ($LASTEXITCODE -ne 0) { throw 'Pod metrics unavailable.' }
    & $minikube -p $profileName kubectl -- --context $profileName -n ai-serving get pods,svc,hpa
    if ($LASTEXITCODE -ne 0) { throw 'Status inspection failed.' }
    $podJson = & $minikube -p $profileName kubectl -- --context $profileName -n ai-serving get pods -l app.kubernetes.io/name=ai-model-serving -o json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect startup restarts.' }
    $pods = ($podJson | ConvertFrom-Json).items | Where-Object { -not $_.metadata.deletionTimestamp }
    foreach ($pod in $pods) {
        foreach ($status in $pod.status.containerStatuses) {
            if ($status.restartCount -gt 0) {
                throw "Pod $($pod.metadata.name) restarted during startup; inspect logs --previous before accepting deployment."
            }
        }
    }
    Write-Output 'PASS: local rollout and metrics. Complete Service smoke checks in docs/RUNBOOK.md.'
} finally {
    Pop-Location
}
