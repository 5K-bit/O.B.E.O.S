param(
    [Parameter(Mandatory=$true)][string]$BaseUrl,
    [Parameter(Mandatory=$true)][string]$PairingToken,
    [switch]$SkipChat
)

$ErrorActionPreference = 'Stop'
$failures = 0
function Pass([string]$Message) { Write-Host "PASS  $Message" }
function Fail([string]$Message) { Write-Host "FAIL  $Message"; $script:failures++ }
function Check([string]$Message, [scriptblock]$Action) {
    try { & $Action; Pass $Message; return $true } catch { Fail "$Message :: $($_.Exception.Message)"; return $false }
}

$BaseUrl = $BaseUrl.TrimEnd('/')
Write-Host 'OBEOS LEGION RUNTIME ACCEPTANCE'
Write-Host '------------------------------'

$surfaceOk = Check 'Legion surface reachable' {
    $r = Invoke-WebRequest -Uri "$BaseUrl/legion" -UseBasicParsing -TimeoutSec 15
    if ($r.StatusCode -ne 200 -or $r.Content -notmatch 'Legion') { throw 'Legion cockpit did not render expected content' }
}

$paired = $false
if ($surfaceOk) {
    $paired = Check 'Legion pairing succeeds' {
        $body = @{ token = $PairingToken; label = 'Legion Runtime Acceptance' } | ConvertTo-Json
        $r = Invoke-RestMethod -Uri "$BaseUrl/api/legion/pair" -Method Post -ContentType 'application/json' -Body $body -SessionVariable obeosSession -TimeoutSec 15
        if ($r.status -ne 'paired') { throw "unexpected pairing status: $($r.status)" }
    }
}

if ($paired) {
    Check 'Legion shared operator session' {
        $r = Invoke-RestMethod -Uri "$BaseUrl/api/legion/session" -WebSession $obeosSession -TimeoutSec 15
        if (-not $r.session_id -or $r.transport -ne 'legion') { throw 'missing shared Legion session' }
    } | Out-Null

    Check 'Legion reads OBEOS device state' {
        $r = Invoke-RestMethod -Uri "$BaseUrl/api/devices" -WebSession $obeosSession -TimeoutSec 15
        if ($r.schema_version -ne '1.0' -or -not $r.devices) { throw 'device registry payload invalid' }
        $ids = @($r.devices | ForEach-Object { $_.id })
        if ($ids -notcontains 'blackcomputer') { throw 'blackcomputer missing from device registry' }
    } | Out-Null

    if (-not $SkipChat) {
        Check 'Legion DAISE round trip' {
            $body = @{
                message = 'OBEOS v0.9 runtime verification. Reply briefly that the Legion path is operational.'
                background = $false
                analysis_mode = 'fast'
                transport = 'legion'
            } | ConvertTo-Json
            $r = Invoke-RestMethod -Uri "$BaseUrl/api/chat" -Method Post -ContentType 'application/json' -Body $body -WebSession $obeosSession -TimeoutSec 90
            if (-not $r.request_id -or $r.transport -ne 'legion') { throw 'chat response missing Legion transport metadata' }
            if (-not $r.messages -or @($r.messages).Count -lt 2) { throw 'DAISE response not returned' }
        } | Out-Null
    }
}

Write-Host '------------------------------'
if ($failures -eq 0) {
    Write-Host 'RESULT: PASS'
    exit 0
}
Write-Host "RESULT: FAIL ($failures failed checks)"
exit 1
