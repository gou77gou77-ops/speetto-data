<#
.SYNOPSIS
  스피또 데이터를 내 PC에서 수집해 gh-pages에 올린다: 접속 확인 → 수집 → 검증 → push.

.DESCRIPTION
  Claude Code나 GitHub 러너 없이 Windows PowerShell 5.1만으로 돈다. 작업 스케줄러가 이 파일을 실행한다(register_tasks.ps1).
  - 실패하면(종료 코드 0이 아니면) 1시간 뒤 처음부터 다시 한다. 최대 3번 다시 한다.
  - 모두 실패하면 Windows 알림을 띄우고 로그 파일 위치를 알려 준다.
  - 실패하면 push하지 않으므로 gh-pages의 기존 JSON은 그대로다.
  - 로그: logs\yyyy-MM-dd-<모드>.log (git에 올리지 않음)
  - gh-pages 작업 폴더: .gh-pages\ (git에 올리지 않음, 매번 원격 gh-pages로 맞춘 뒤 시작)
  - 연락처(User-Agent)는 사용자 환경 변수 COLLECTOR_CONTACT에서 읽는다. 행안부 키는 JUSO_API_KEY(선택).
  - 이 폴더가 speetto-data 저장소를 clone한 것이면, 시작할 때 main을 받아 수집기 코드를 최신으로 맞춘다(fast-forward만).

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_collector_local.ps1 -Mode stores
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_collector_local.ps1 -Mode rounds -Retries 0
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_collector_local.ps1 -TestNotify
#>
[CmdletBinding()]
param(
    [ValidateSet('stores', 'rounds')]
    [string]$Mode,
    [int]$Retries = 3,
    [int]$RetryDelayMinutes = 60,
    [string]$Remote = 'https://github.com/gou77gou77-ops/speetto-data.git',
    [switch]$NoPush,
    [switch]$NoUpdate,
    [switch]$TestNotify
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$LogDir = Join-Path $Root 'logs'
$Pages = Join-Path $Root '.gh-pages'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = $Utf8 } catch { }

$Titles = @{
    stores = '스피또 판매점 수집 실패'
    rounds = '스피또 회차 수집 실패'
}
$Outputs = @{
    stores = 'stores.json'
    rounds = 'rounds_latest.json'
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogName = if ($Mode) { $Mode } else { 'notify' }
$LogFile = Join-Path $LogDir ('{0}-{1}.log' -f (Get-Date -Format 'yyyy-MM-dd'), $LogName)

function Write-Log([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $Utf8)
    Write-Host $line
}

function Show-FailureNotice([string]$Title, [string]$Body) {
    $esc = { param($s) [System.Security.SecurityElement]::Escape($s) }
    try {
        $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
        $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml(("<toast duration='long'><visual><binding template='ToastGeneric'><text>{0}</text><text>{1}</text></binding></visual></toast>" -f (& $esc $Title), (& $esc $Body)))
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
            [Windows.UI.Notifications.ToastNotification]::new($xml))
        Write-Log "알림 표시: $Title"
    } catch {
        Write-Log "Windows 알림 실패($($_.Exception.Message)). msg.exe로 대신 띄운다"
        try { & msg.exe $env:USERNAME "$Title`r`n$Body" } catch { Write-Log "msg.exe도 실패: $($_.Exception.Message)" }
    }
}

if ($TestNotify) {
    Show-FailureNotice '스피또 수집 알림 시험' "알림이 보이면 정상입니다. 로그: $LogFile"
    exit 0
}
if (-not $Mode) { throw '-Mode stores 또는 -Mode rounds를 준다' }

# 수집하는 동안 PC가 절전 모드로 들어가지 않게 한다(끝나면 원래대로).
Add-Type -Namespace Speetto -Name Power -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
$ES_CONTINUOUS = [uint32]'0x80000000'
$ES_SYSTEM_REQUIRED = [uint32]'0x00000001'

function Invoke-Logged([string]$Exe, [string[]]$Arguments) {
    Write-Log ("> {0} {1}" -f $Exe, ($Arguments -join ' '))
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Log ("  " + $_.ToString()) }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($null -eq $code) { $code = 0 }
    return [int]$code
}

function Find-Python {
    foreach ($cand in @(@('python'), @('py', '-3'))) {
        $cmd = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prefix = @($cand | Select-Object -Skip 1)
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $ver = & $cmd.Source @prefix --version 2>&1
        $ErrorActionPreference = $prev
        if ($LASTEXITCODE -eq 0 -and "$ver" -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 12) {
            return @{ Exe = $cmd.Source; Prefix = $prefix; Version = "$ver" }
        }
    }
    throw 'Python 3.12 이상을 찾지 못했다. python 또는 py가 PATH에 있어야 한다'
}

function Sync-Pages {
    if (-not (Test-Path (Join-Path $Pages '.git'))) {
        if (Test-Path $Pages) { Remove-Item -Recurse -Force $Pages }
        return Invoke-Logged 'git' @('clone', '--quiet', '--branch', 'gh-pages', '--single-branch', $Remote, $Pages)
    }
    $c = Invoke-Logged 'git' @('-C', $Pages, 'fetch', '--quiet', 'origin', 'gh-pages')
    if ($c -ne 0) { return $c }
    $c = Invoke-Logged 'git' @('-C', $Pages, 'reset', '--quiet', '--hard', 'origin/gh-pages')
    if ($c -ne 0) { return $c }
    return Invoke-Logged 'git' @('-C', $Pages, 'clean', '-fdq')
}

function Publish-Pages([string]$File) {
    Copy-Item (Join-Path $Root 'public\config.json') (Join-Path $Pages 'config.json') -Force
    $c = Invoke-Logged 'git' @('-C', $Pages, 'add', $File, 'config.json')
    if ($c -ne 0) { return $c }
    & git -C $Pages diff --cached --quiet
    if ($LASTEXITCODE -eq 0) { Write-Log '바뀐 것 없음. push하지 않는다'; return 0 }
    $msg = '{0} {1} (내 PC)' -f $Mode, (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm'Z'")
    $c = Invoke-Logged 'git' @('-C', $Pages, '-c', 'user.name=speetto-collector (local)',
        '-c', 'user.email=gou77gou77-ops@users.noreply.github.com', 'commit', '--quiet', '-m', $msg)
    if ($c -ne 0) { return $c }
    if ($NoPush) { Write-Log '-NoPush: push하지 않는다'; return 0 }
    for ($i = 1; $i -le 3; $i++) {
        $c = Invoke-Logged 'git' @('-C', $Pages, 'push', '--quiet', 'origin', 'HEAD:gh-pages')
        if ($c -eq 0) { Write-Log 'gh-pages push 완료'; return 0 }
        Write-Log "push 실패($i/3). GitHub의 rounds가 먼저 올렸을 수 있어 원격을 받아 다시 쌓는다"
        $c = Invoke-Logged 'git' @('-C', $Pages, 'pull', '--quiet', '--rebase', 'origin', 'gh-pages')
        if ($c -ne 0) { return $c }
    }
    return 1
}

function Update-Code {
    if ($NoUpdate) { return }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $top = & git -C $Root rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $top) { Write-Log '코드 업데이트 건너뜀: git 저장소가 아니다'; return }
        if ((Resolve-Path $top).Path.TrimEnd('\') -ne (Resolve-Path $Root).Path.TrimEnd('\')) {
            Write-Log "코드 업데이트 건너뜀: $Root는 speetto-data 저장소 루트가 아니다"
            return
        }
        $url = & git -C $Root remote get-url origin 2>$null
        $origin = ("$url".TrimEnd('/')) -replace '\.git$', ''
        if ($origin -ne ($Remote -replace '\.git$', '')) {
            Write-Log "코드 업데이트 건너뜀: origin이 $Remote가 아니다"
            return
        }
    } finally {
        $ErrorActionPreference = $prev
    }
    $c = Invoke-Logged 'git' @('-C', $Root, 'pull', '--ff-only', '--quiet', 'origin', 'main')
    if ($c -ne 0) { Write-Log "코드 업데이트 실패(종료 코드 $c). 지금 있는 코드로 수집한다" }
}

function Invoke-Attempt($Py) {
    $c = Sync-Pages
    if ($c -ne 0) { Write-Log "gh-pages 받기 실패(종료 코드 $c)"; return $c }

    $out = Join-Path $Pages $Outputs[$Mode]
    $c = Invoke-Logged $Py.Exe ($Py.Prefix + @('-u', '-m', 'collector.check', '--target', $Mode))
    if ($c -ne 0) { return $c }
    if ($Mode -eq 'stores') {
        $c = Invoke-Logged $Py.Exe ($Py.Prefix + @('-u', '-m', 'collector.stores', '--out', $out))
    } else {
        $c = Invoke-Logged $Py.Exe ($Py.Prefix + @('-u', '-m', 'collector.rounds',
            '--stores', (Join-Path $Pages 'stores.json'), '--out', $out))
    }
    if ($c -ne 0) { return $c }

    $verifyArgs = @('-u', '-m', 'collector.verify', $out, (Join-Path $Root 'public\config.json'))
    $c = Invoke-Logged $Py.Exe ($Py.Prefix + $verifyArgs)
    if ($c -ne 0) { return $c }
    return Publish-Pages $Outputs[$Mode]
}

# ---------- 시작 ----------

$mutex = New-Object System.Threading.Mutex($false, 'Local\SpeettoCollectorLocal')
$exitCode = 1
[void][Speetto.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED)
try {
    Write-Log "===== $Mode 수집 시작 (재시도 최대 $Retries 번, 간격 $RetryDelayMinutes 분) ====="
    try {
        if (-not $mutex.WaitOne([TimeSpan]::FromHours(6))) { throw '다른 수집이 6시간 넘게 끝나지 않았다' }
    } catch [System.Threading.AbandonedMutexException] { }

    foreach ($name in 'COLLECTOR_CONTACT', 'COLLECTOR_INTERVAL', 'JUSO_API_KEY', 'JUSO_COORD_API_KEY') {
        if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
            $v = [Environment]::GetEnvironmentVariable($name, 'User')
            if ($v) { [Environment]::SetEnvironmentVariable($name, $v, 'Process') }
        }
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    Remove-Item Env:RUNNER_ENVIRONMENT -ErrorAction SilentlyContinue

    Update-Code
    $py = Find-Python
    Write-Log "Python: $($py.Version) ($($py.Exe))"
    Push-Location $Root
    try {
        for ($attempt = 0; $attempt -le $Retries; $attempt++) {
            if ($attempt -gt 0) {
                Write-Log "$RetryDelayMinutes 분 뒤 다시 시도 ($attempt/$Retries)"
                Start-Sleep -Seconds ($RetryDelayMinutes * 60)
            }
            $started = Get-Date
            $exitCode = Invoke-Attempt $py
            $took = (Get-Date) - $started
            Write-Log ("시도 {0}: 종료 코드 {1}, {2:N0}분 {3:D2}초" -f ($attempt + 1), $exitCode, [math]::Floor($took.TotalMinutes), $took.Seconds)
            if ($exitCode -eq 0) { break }
        }
    } finally {
        Pop-Location
    }
} catch {
    Write-Log "스크립트 오류: $($_.Exception.Message)"
    $exitCode = 1
} finally {
    [void][Speetto.Power]::SetThreadExecutionState($ES_CONTINUOUS)
    try { $mutex.ReleaseMutex() } catch { }
}

if ($exitCode -eq 0) {
    Write-Log "===== $Mode 성공 ====="
} else {
    Write-Log "===== $Mode 실패 (종료 코드 $exitCode). gh-pages의 기존 JSON은 그대로다 ====="
    Show-FailureNotice $Titles[$Mode] "재시도까지 모두 실패했습니다. 로그: $LogFile"
}
exit $exitCode
