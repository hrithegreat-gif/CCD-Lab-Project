#Requires -Version 5.1
<#
.SYNOPSIS
  Guarded stop/start/status for the CCD Lab k3s node via the AWS CLI.

.DESCRIPTION
  Requires an EXPLICIT instance id and region: there is no discovery, no
  wildcards, and no default target. Real stop/start additionally require -Yes,
  or -DryRun to validate authorization without changing anything.
  Uses only the AWS CLI (no SDK) with the caller's already-configured operator
  identity; the script never looks for or stores credentials.

.EXAMPLE
  ./aws-instance.ps1 status -InstanceId i-0123456789abcdef0 -Region ap-south-1
  ./aws-instance.ps1 stop   -InstanceId i-0123456789abcdef0 -Region ap-south-1 -DryRun
  ./aws-instance.ps1 stop   -InstanceId i-0123456789abcdef0 -Region ap-south-1 -Yes
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('stop', 'start', 'status')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^i-[0-9a-f]{8,17}$')]
    [string]$InstanceId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z]{2}(-gov)?-[a-z]+-[1-9]$')]
    [string]$Region,

    # Validate permissions only (aws ec2 ... --dry-run); never changes state.
    [switch]$DryRun,

    # Explicit acknowledgment required for a real stop/start.
    [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    throw "AWS CLI not found on PATH. Install AWS CLI v2 and authenticate with your operator identity first."
}

if ($Action -in @('stop', 'start') -and -not ($Yes -or $DryRun)) {
    throw "Refusing to $Action $InstanceId. Re-run with -Yes to proceed, or -DryRun to validate authorization only."
}

$target = @('--instance-ids', $InstanceId, '--region', $Region)

switch ($Action) {
    'status' {
        if ($DryRun) { Write-Warning "-DryRun is ignored for status." }
        & aws ec2 describe-instances @target `
            --query 'Reservations[].Instances[].{InstanceId:InstanceId,State:State.Name,Type:InstanceType,PublicIp:PublicIpAddress,PrivateIp:PrivateIpAddress}' `
            --output table
        exit $LASTEXITCODE
    }
    default {
        if ($DryRun) {
            $output = (& aws ec2 "$($Action)-instances" @target --dry-run --output json 2>&1 | Out-String)
            if ($output -match 'DryRunOperation') {
                Write-Host "DRY RUN OK: caller is authorized to $Action $InstanceId in $Region (nothing was changed)."
                exit 0
            }
            Write-Error $output
            exit 1
        }

        Write-Host "$($Action)ing instance $InstanceId in $Region ..."
        & aws ec2 "$($Action)-instances" @target --output json
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        Write-Host "Request submitted. Verify with: ./aws-instance.ps1 status -InstanceId $InstanceId -Region $Region"
        Write-Host "Reminder: every start re-arms the auto-stop timer (Terraform autostop_hours, default 4h) and assigns a NEW ephemeral public IPv4."
    }
}
