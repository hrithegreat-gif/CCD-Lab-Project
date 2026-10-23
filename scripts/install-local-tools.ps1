# Project-local Minikube installation; no global PATH changes.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$toolDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.validation/tools'
$version = 'v1.39.0'
$base = "https://github.com/kubernetes/minikube/releases/download/$version"
New-Item -ItemType Directory -Force $toolDir | Out-Null
$download = Join-Path $toolDir 'minikube.download'
$checksum = Join-Path $toolDir 'minikube.sha256'
Invoke-WebRequest -UseBasicParsing "$base/minikube-windows-amd64.exe" -OutFile $download
Invoke-WebRequest -UseBasicParsing "$base/minikube-windows-amd64.exe.sha256" -OutFile $checksum
$expected = ((Get-Content -LiteralPath $checksum -Raw).Trim() -split '\s+')[0]
if ($expected -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid checksum response.' }
if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ine $expected) {
    throw 'Minikube checksum mismatch; downloaded file was not activated.'
}
Move-Item -LiteralPath $download -Destination (Join-Path $toolDir 'minikube.exe') -Force
& (Join-Path $toolDir 'minikube.exe') version
if ($LASTEXITCODE -ne 0) { throw 'Minikube executable verification failed.' }
