# Delete only the fixed project namespace, never AWS infrastructure or local files.
# A context must be named explicitly; without -Apply this only reports the target.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExpectedContext,
    [switch]$Apply,
    [string]$Kubectl = 'kubectl',
    [string]$ConfirmNamespace
)
$ErrorActionPreference = 'Stop'
$actualContext = & $Kubectl config current-context
if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the current Kubernetes context.' }
if ($actualContext.Trim() -cne $ExpectedContext) {
    throw "Context mismatch; expected '$ExpectedContext', found '$actualContext'. No changes made."
}
Write-Output "Target context: $ExpectedContext; namespace: ai-serving"
Write-Output 'This does NOT stop/delete EC2, disks, S3, ECR, logs or other AWS resources.'
if (-not $Apply) {
    Write-Output 'Preview only. Rerun with -Apply to request namespace deletion.'
    return
}
$confirmation = $ConfirmNamespace
if (-not $confirmation) {
    $confirmation = Read-Host 'Type ai-serving to delete all resources in this namespace'
}
if ($confirmation -cne 'ai-serving') { throw 'Confirmation did not match. No changes made.' }
& $Kubectl --context $ExpectedContext delete namespace ai-serving --ignore-not-found=true --wait=true --timeout=120s
if ($LASTEXITCODE -ne 0) { throw 'Namespace deletion failed or timed out; check remaining resources.' }
