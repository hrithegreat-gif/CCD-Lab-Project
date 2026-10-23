# Build and exercise the actual hardened Linux container; remove only our test container.
[CmdletBinding()]
param(
    [string]$Image = 'ai-model-serving:detection',
    [ValidateRange(1024,65535)][int]$Port = 8001,
    [switch]$SkipBuild,
    [switch]$NoCache
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$containerName = 'ai-serving-check-' + [guid]::NewGuid().ToString('N').Substring(0,12)
$previousKey = $env:API_KEY
$created = $false
Push-Location $projectRoot
try {
    $python = Join-Path $projectRoot '.venv-review/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/setup-phase1.ps1 first.' }
    $osType = & docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or $osType -ne 'linux') { throw 'Start Docker Desktop with Linux containers.' }
    if (-not $SkipBuild) {
        $buildArgs = @('build', '-t', $Image)
        if ($NoCache) { $buildArgs += '--no-cache' }
        & docker @buildArgs .
        if ($LASTEXITCODE -ne 0) { throw 'Docker build failed.' }
    }
    $env:API_KEY = [guid]::NewGuid().ToString('N')
    & docker run -d --name $containerName -p "127.0.0.1:${Port}:8000" `
        --read-only --cap-drop=ALL --security-opt no-new-privileges --memory 1g --cpus 1 `
        --tmpfs /tmp:rw,noexec,nosuid,size=32m,uid=10001,gid=10001 `
        --tmpfs /cache:rw,noexec,nosuid,size=256m,uid=0,gid=10001,mode=0770 `
        -e MODEL_CACHE_DIR=/cache -e MODEL_LOCAL_FILES_ONLY=true `
        -e BACKGROUND_MODEL_LOAD=true -e API_KEY $Image
    # docker run can create a container even when publishing its port fails.
    $runExit = $LASTEXITCODE
    & docker container inspect $containerName --format '{{.Id}}' 2>$null | Out-Null
    $created = $LASTEXITCODE -eq 0
    if ($runExit -ne 0) { throw 'Container startup failed.' }
    $ready = $false
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$Port/health/ready" -TimeoutSec 3
            if ($health.status -eq 'ready') { $ready = $true; break }
        } catch { }
        $running = & docker inspect $containerName --format '{{.State.Running}}'
        if ($LASTEXITCODE -ne 0 -or $running -ne 'true') { break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw 'Container did not become ready within 180 seconds.' }
    & $python scripts/smoke_test.py --base-url "http://127.0.0.1:$Port"
    if ($LASTEXITCODE -ne 0) { throw 'Container HTTP acceptance failed.' }
    $runtimeUid = & docker exec $containerName id -u
    if ($LASTEXITCODE -ne 0 -or $runtimeUid -ne '10001') { throw 'Unexpected runtime UID.' }
    & docker exec $containerName python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Container dependency check failed.' }
    & docker stats --no-stream $containerName
    if ($LASTEXITCODE -ne 0) { throw 'Container resource snapshot failed.' }
    & docker image inspect $Image --format '{{.Id}}'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot identify tested image.' }
    Write-Output 'PASS: hardened container, real inference, authentication, dependencies and UID.'
} finally {
    if ($created) {
        & docker logs $containerName
        & docker rm -f $containerName | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warning "Cleanup failed: inspect $containerName" }
    }
    $env:API_KEY = $previousKey
    Pop-Location
}
