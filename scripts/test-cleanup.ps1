# Offline behavioral checks. The fake client cannot contact any cluster.
$ErrorActionPreference = 'Stop'
$global:CleanupTestState = @{ observed = @(); context = 'ai-serving-local' }
function Invoke-FakeKubectl {
    $global:CleanupTestState.observed += ($args -join ' ')
    $global:LASTEXITCODE = 0
    if (($args -join ' ') -eq 'config current-context') { $global:CleanupTestState.context }
}
$cleanup = Join-Path $PSScriptRoot 'cleanup.ps1'
& $cleanup -ExpectedContext ai-serving-local -Kubectl Invoke-FakeKubectl
if ($global:CleanupTestState.observed.Count -ne 1) { throw 'Preview mutated resources.' }
$global:CleanupTestState.context = 'different-context'
$failed = $false
try { & $cleanup -ExpectedContext ai-serving-local -Kubectl Invoke-FakeKubectl -Apply -ConfirmNamespace ai-serving }
catch { $failed = $true }
if (-not $failed -or $global:CleanupTestState.observed.Count -ne 2) { throw 'Context mismatch was not rejected.' }
$global:CleanupTestState.context = 'ai-serving-local'
$failed = $false
try { & $cleanup -ExpectedContext ai-serving-local -Kubectl Invoke-FakeKubectl -Apply -ConfirmNamespace wrong }
catch { $failed = $true }
if (-not $failed -or $global:CleanupTestState.observed.Count -ne 3) { throw 'Bad confirmation was not rejected.' }
& $cleanup -ExpectedContext ai-serving-local -Kubectl Invoke-FakeKubectl -Apply -ConfirmNamespace ai-serving
if ($global:CleanupTestState.observed.Count -ne 5 -or $global:CleanupTestState.observed[-1] -cne '--context ai-serving-local delete namespace ai-serving --ignore-not-found=true --wait=true --timeout=120s') {
    throw 'Deletion did not use the exact named context and namespace.'
}
Write-Output 'PASS: cleanup preview, context mismatch, confirmation and scoped deletion.'

Remove-Variable CleanupTestState -Scope Global
