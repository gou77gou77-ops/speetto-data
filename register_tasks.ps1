<#
.SYNOPSIS
  run_collector_local.ps1을 Windows 작업 스케줄러에 등록한다(작업 폴더 \SpeettoOracle\).

.DESCRIPTION
  - stores: 매주 일요일 03:00
  - rounds: 매일 09:00, 18:00 (-IncludeRounds를 줄 때만. GitHub의 rounds 워크플로를 쓰면 등록하지 않는다)
  공통 설정:
  - 작업 실행을 위해 절전 모드 해제(WakeToRun). 실제로 깨우려면 전원 옵션의 "절전 모드 해제 타이머 허용"이 켜져 있어야 한다.
  - 놓친 실행은 PC가 켜지면 바로 실행(StartWhenAvailable)
  - 실패 시 1시간 뒤 재시도(최대 3번)와 실패 알림은 run_collector_local.ps1이 한다.
    작업 스케줄러의 "실패 시 다시 시작"은 종료 코드를 보지 않아서 쓰지 않는다.
  - 내 계정으로, 로그인해 있을 때 실행(알림을 띄우고 git 자격 증명을 쓰기 위해). 관리자 권한 필요 없음.
  이 파일을 다시 실행하면 같은 이름의 작업을 덮어쓴다.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\register_tasks.ps1 -DryRun
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\register_tasks.ps1
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\register_tasks.ps1 -IncludeRounds
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\register_tasks.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [switch]$IncludeRounds,
    [switch]$Unregister,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$TaskPath = '\SpeettoOracle\'
$Runner = Join-Path $PSScriptRoot 'run_collector_local.ps1'
$User = "$env:USERDOMAIN\$env:USERNAME"

if ($Unregister) {
    foreach ($name in 'stores', 'rounds') {
        $t = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
        if ($t) {
            if ($DryRun) { Write-Host "[DryRun] 삭제할 작업: $TaskPath$name" }
            else { Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $name -Confirm:$false; Write-Host "삭제: $TaskPath$name" }
        }
    }
    exit 0
}

if (-not (Test-Path $Runner)) { throw "run_collector_local.ps1이 없다: $Runner" }
if ($PSScriptRoot -match '[^\x20-\x7E]' -or $PSScriptRoot -match ' ') {
    Write-Warning "폴더 경로에 한글이나 공백이 있다: $PSScriptRoot. 대부분 동작하지만 문제가 생기면 C:\speetto-data 같은 곳으로 옮긴다"
}

# 미리 확인 (등록은 막지 않고 경고만)
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Warning 'git이 PATH에 없다. 수집 결과를 올릴 수 없다' }
if (-not ((Get-Command python -ErrorAction SilentlyContinue) -or (Get-Command py -ErrorAction SilentlyContinue))) {
    Write-Warning 'python(3.12 이상)이 PATH에 없다'
}
if (-not [Environment]::GetEnvironmentVariable('COLLECTOR_CONTACT', 'User')) {
    Write-Warning '사용자 환경 변수 COLLECTOR_CONTACT가 없다. User-Agent에 연락처 없이 요청한다'
}
$wake = (powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE | Select-String '0x[0-9a-fA-F]{8}' | Select-Object -First 1)
if ($wake -and $wake.ToString() -match '0x00000000\s*$') {
    Write-Warning '전원 옵션 "절전 모드 해제 타이머 허용"이 꺼져 있다(AC). 절전 중이면 예약 시각에 깨우지 못하고, 다음에 켜질 때 실행된다'
}

function New-CollectorTask([string]$Mode, $Triggers, [string]$Description) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $PSScriptRoot `
        -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Mode {1}' -f $Runner, $Mode)
    $settings = New-ScheduledTaskSettingsSet `
        -WakeToRun `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Hours 8)
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $task = New-ScheduledTask -Action $action -Trigger $Triggers -Settings $settings -Principal $principal -Description $Description
    if ($DryRun) {
        Write-Host "[DryRun] $TaskPath$Mode"
        Write-Host "  실행: powershell.exe $($action.Arguments)"
        Write-Host "  시작 폴더: $($action.WorkingDirectory)"
        foreach ($t in @($Triggers)) { Write-Host ("  일정: {0}" -f $t.StartBoundary) }
        Write-Host "  절전 해제 $($settings.WakeToRun), 놓친 실행 바로 $($settings.StartWhenAvailable), 시간 제한 $($settings.ExecutionTimeLimit), 계정 $User (로그인해 있을 때)"
        return
    }
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $Mode -InputObject $task -Force | Out-Null
    $info = Get-ScheduledTaskInfo -TaskPath $TaskPath -TaskName $Mode
    Write-Host "등록: $TaskPath$Mode — 다음 실행 $($info.NextRunTime)"
}

New-CollectorTask 'stores' @(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '03:00') `
    '스피또 오라클: 전국 판매점 → gh-pages stores.json. 실패하면 1시간 뒤 최대 3번 다시, 모두 실패하면 알림. 로그: logs\'

if ($IncludeRounds) {
    New-CollectorTask 'rounds' @(
        (New-ScheduledTaskTrigger -Daily -At '09:00'),
        (New-ScheduledTaskTrigger -Daily -At '18:00')
    ) '스피또 오라클: 회차 + 1등 배출점 → gh-pages rounds_latest.json. 실패하면 1시간 뒤 최대 3번 다시, 모두 실패하면 알림. 로그: logs\'
} elseif (-not $DryRun -and (Get-ScheduledTask -TaskPath $TaskPath -TaskName 'rounds' -ErrorAction SilentlyContinue)) {
    Write-Warning "rounds 작업이 이미 등록돼 있다. GitHub rounds 워크플로와 함께 돌지 않게 하려면 -Unregister 뒤 다시 등록한다"
}

if (-not $DryRun) {
    Write-Host ''
    Write-Host '확인: 작업 스케줄러 → 작업 스케줄러 라이브러리 → SpeettoOracle'
    Write-Host ('지금 한 번 돌려 보기: Start-ScheduledTask -TaskPath "{0}" -TaskName stores' -f $TaskPath)
}
