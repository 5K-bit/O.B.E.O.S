param(
    [string]$Branch = "main",
    [string]$RemoteHost = $env:OBEOS_CLOUD_SSH_TARGET,
    [string]$RemoteAppDir = "/opt/obeos",
    [string]$RemoteDeployScript = "tools/cloud-deploy.sh",
    [switch]$SkipVerify,
    [switch]$FullVerify,
    [switch]$Push,
    [switch]$Deploy,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$git = (Get-Command git -ErrorAction Stop).Source
$ssh = $null
$releaseSmokeTargets = @(
    "tests\test_verification_and_integrations.py",
    "tests\test_unified_retrieval_profiles.py",
    "tests\test_ui_refactor_architecture.py",
    "tests\test_desktop_chat_pane.py",
    "tests\test_phase2_improvements.py",
    "blackfong_ecosystem\test_phase5_operational_interfaces.py"
)

try {
    $ssh = (Get-Command ssh -ErrorAction Stop).Source
}
catch {
    $ssh = $null
}

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $Executable @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Get-CheckedOutput {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    $output = & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
    return $output
}

function Get-PreferredPython {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "Python was not found. Create the local venv or install Python before running release automation."
}

function Get-BranchRelation {
    param([string]$TrackedRef)

    $relation = (Get-CheckedOutput `
        -Executable $git `
        -Arguments @("-C", $repoRoot, "rev-list", "--left-right", "--count", "HEAD...$TrackedRef") `
        -FailureMessage "Unable to compare HEAD with $TrackedRef." | Out-String).Trim()
    $parts = $relation -split "\s+"
    if ($parts.Length -lt 2) {
        throw "Unexpected branch relation output: $relation"
    }

    return [pscustomobject]@{
        Ahead  = [int]$parts[0]
        Behind = [int]$parts[1]
    }
}

function Assert-CleanForRelease {
    param([string[]]$StatusLines)

    if ($AllowDirty) {
        return
    }

    if ($StatusLines.Count -gt 0) {
        throw "Working tree is dirty. Commit or stash local changes before using -Push or -Deploy. Use -AllowDirty only if you intentionally want to release the last committed revision."
    }
}

$python = Get-PreferredPython

Push-Location $repoRoot
try {
    Write-Section "Repository state"
    Invoke-Checked `
        -Executable $git `
        -Arguments @("-C", $repoRoot, "status", "--short", "--branch") `
        -FailureMessage "git status failed."

    $statusLines = @(Get-CheckedOutput `
        -Executable $git `
        -Arguments @("-C", $repoRoot, "status", "--porcelain=v1", "--untracked-files=normal") `
        -FailureMessage "Unable to read git working tree status.")

    if (-not $SkipVerify) {
        Write-Section "Local verification"
        if ($FullVerify) {
            Invoke-Checked `
                -Executable $python `
                -Arguments @("-m", "blackfong_ecosystem.cli", "verify", "automated") `
                -FailureMessage "Full automated verification failed."
        }
        else {
            $smokeArguments = @("-m", "pytest", "-q") + $releaseSmokeTargets
            Invoke-Checked `
                -Executable $python `
                -Arguments $smokeArguments `
                -FailureMessage "Release smoke verification failed."
        }
    }

    Write-Section "Sync with origin/$Branch"
    Invoke-Checked `
        -Executable $git `
        -Arguments @("-C", $repoRoot, "fetch", "origin", $Branch, "--prune") `
        -FailureMessage "git fetch failed."

    $trackedRef = "origin/$Branch"
    $relation = Get-BranchRelation -TrackedRef $trackedRef
    $headRef = ((Get-CheckedOutput `
        -Executable $git `
        -Arguments @("-C", $repoRoot, "rev-parse", "--short", "HEAD") `
        -FailureMessage "Unable to read local HEAD." | Out-String).Trim())

    Write-Host "HEAD: $headRef"
    Write-Host "Ahead of ${trackedRef}: $($relation.Ahead)"
    Write-Host "Behind ${trackedRef}: $($relation.Behind)"

    if ($Push) {
        Assert-CleanForRelease -StatusLines $statusLines
        if ($relation.Behind -gt 0) {
            throw "Local branch is behind $trackedRef. Pull and reconcile before pushing."
        }

        Write-Section "Push approved revision to origin/$Branch"
        Invoke-Checked `
            -Executable $git `
            -Arguments @("-C", $repoRoot, "push", "origin", "HEAD:$Branch") `
            -FailureMessage "git push failed."

        Invoke-Checked `
            -Executable $git `
            -Arguments @("-C", $repoRoot, "fetch", "origin", $Branch, "--prune") `
            -FailureMessage "git fetch after push failed."
        $relation = Get-BranchRelation -TrackedRef $trackedRef
    }

    if ($Deploy) {
        Assert-CleanForRelease -StatusLines $statusLines
        if ([string]::IsNullOrWhiteSpace($RemoteHost)) {
            throw "Remote host is required for -Deploy. Pass -RemoteHost or set OBEOS_CLOUD_SSH_TARGET, for example: ubuntu@obeos-cloud.tail12c001.ts.net"
        }
        if (-not $ssh) {
            throw "OpenSSH client was not found. Install ssh before using -Deploy."
        }
        if ($relation.Behind -gt 0 -or $relation.Ahead -gt 0) {
            throw "Deploy only when local HEAD exactly matches $trackedRef. Use -Push first or sync the branch before deploying."
        }

        $remoteCommand = "cd '$RemoteAppDir' && OBEOS_APP_DIR='$RemoteAppDir' bash '$RemoteDeployScript'"
        Write-Section "Deploy to $RemoteHost"
        Invoke-Checked `
            -Executable $ssh `
            -Arguments @($RemoteHost, $remoteCommand) `
            -FailureMessage "Remote deployment failed."
    }

    Write-Section "Release summary"
    Write-Host "Repo root: $repoRoot"
    Write-Host "Branch: $Branch"
    Write-Host "HEAD: $headRef"
    if ($Deploy) {
        Write-Host "Cloud runtime updated on: $RemoteHost"
    }
    elseif ($Push) {
        Write-Host "GitHub updated. Cloud deploy is the next step."
    }
    else {
        if ($FullVerify) {
            Write-Host "Full verification complete. Re-run with -Push or -Push -Deploy when you approve the revision."
        }
        else {
            Write-Host "Release smoke verification complete. Re-run with -Push or -Push -Deploy when you approve the revision."
        }
    }
}
finally {
    Pop-Location
}
