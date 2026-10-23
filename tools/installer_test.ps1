<#
Isolated installer lifecycle test. Uses a variant of the real installer built
with a different AppId, name and mutex (see -Setup), so it can never touch a
real Master Download Manager installation, its shortcuts or associations.

    powershell -ExecutionPolicy Bypass -File tools\installer_test.ps1 -Setup build\installer-test\MDM-InstallTest-Setup.exe

Build the variant with:
    ISCC /DAppVersion=4.0.0 /DSourceDir=dist\MasterDownloadManager "/DAppName=MDM Install Test"
         "/DAppIdGuid={{0D1B6E54-7F0A-4C55-9E7B-3C6A2E9D1F00}" "/DAppMutexName=Local\MDMInstallTest.Mutex"
         /DOutputName=MDM-InstallTest-Setup /DInstallTest=1 /Obuild\installer-test installer\MasterDownloadManager.iss
#>
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$Python = "$PSScriptRoot\..\.venv\Scripts\python.exe"
)
$ErrorActionPreference = "Stop"
$Name = "MDM Install Test"
$UninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{0D1B6E54-7F0A-4C55-9E7B-3C6A2E9D1F00}_is1"
$Work = Join-Path $env:TEMP ("mdm-installer-test-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$Dir = Join-Path $Work "app"
$Data = Join-Path $Work "data"
$StartMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "$Name.lnk"
$Desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk"
$results = New-Object System.Collections.Generic.List[object]

function Check($label, $ok, $detail = "") {
    $results.Add([pscustomobject]@{ Check = $label; Result = $(if ($ok) { "PASS" } else { "FAIL" }); Detail = $detail })
    Write-Host ("{0}  {1}  {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail)
}

function Install($components, $tasks, $log) {
    $p = Start-Process -FilePath $Setup -PassThru -Wait -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/DIR=`"$Dir`"",
        "/COMPONENTS=`"$components`"", "/TASKS=`"$tasks`"", "/LOG=`"$log`"")
    return $p.ExitCode
}

New-Item -ItemType Directory -Force $Work, $Data | Out-Null
try {
    # ---------------------------------------------------------------- clean install
    $t0 = Get-Date
    $code = Install "app,browser" "startmenuicon,desktopicon" "$Work\install1.log"
    Check "clean install exits 0" ($code -eq 0) "exit $code in $([int]((Get-Date) - $t0).TotalSeconds) s"
    Check "program installed" (Test-Path "$Dir\MasterDownloadManager.exe")
    Check "bundled tools installed (ffmpeg, deno)" ((Test-Path "$Dir\tools\ffmpeg\ffmpeg.exe") -and (Test-Path "$Dir\tools\deno\deno.exe"))
    Check "browser extension component installed" ((Test-Path "$Dir\extensions\chrome\manifest.json") -and (Test-Path "$Dir\extensions\firefox\manifest.json"))
    Check "licenses and notices installed" ((Test-Path "$Dir\licenses\ab-download-manager-1.11.1\LICENSE.txt") -and (Test-Path "$Dir\THIRD_PARTY_NOTICES.md"))
    Check "Start menu shortcut created" (Test-Path $StartMenu)
    Check "desktop shortcut created" (Test-Path $Desktop)
    $reg = Get-ItemProperty $UninstallKey -ErrorAction SilentlyContinue
    Check "one uninstall entry with version 4.0.0" ($reg -and $reg.DisplayVersion -eq "4.0.0") "$($reg.DisplayName) $($reg.DisplayVersion)"
    Check "publisher links registered" ($reg.URLInfoAbout -like "https://github.com/*") "$($reg.URLInfoAbout)"

    # ------------------------------------------------------------- run installed app
    $token = -join ((48..57) + (97..102) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    $downloads = Join-Path $Data "downloads"
    New-Item -ItemType Directory -Force $downloads | Out-Null
    @{ api_port = 47931; api_token = $token; minimize_to_tray = $false; download_dir = $downloads } |
        ConvertTo-Json | Set-Content -Encoding utf8 "$Data\settings.json"
    $env:MDM_DATA_DIR = $Data
    $app = Start-Process -FilePath "$Dir\MasterDownloadManager.exe" -PassThru
    Remove-Item Env:\MDM_DATA_DIR
    $h = @{ "X-MDM-Token" = $token }
    $ver = $null
    for ($i = 0; $i -lt 40 -and -not $ver; $i++) {
        Start-Sleep -Milliseconds 500
        try { $ver = Invoke-RestMethod "http://127.0.0.1:47931/version" -TimeoutSec 2 } catch {}
    }
    Check "installed app starts and its API answers" ($ver.version -eq "4.0.0") "$($ver.version)"
    # a real download through the installed app (local server, no internet)
    $srvDir = Join-Path $Work "srv"
    New-Item -ItemType Directory -Force $srvDir | Out-Null
    $bytes = New-Object byte[] (12MB); (New-Object Random 7).NextBytes($bytes)
    [IO.File]::WriteAllBytes("$srvDir\payload.bin", $bytes)
    $srv = Start-Process -FilePath $Python -ArgumentList @("-m", "http.server", "47932", "--bind", "127.0.0.1", "--directory", "`"$srvDir`"") -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $job = Invoke-RestMethod -Method Post "http://127.0.0.1:47931/downloads" -Headers $h -ContentType "application/json" `
        -Body (@{ url = "http://127.0.0.1:47932/payload.bin"; format_id = "file"; engine = "http" } | ConvertTo-Json)
    $status = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        $status = Invoke-RestMethod "http://127.0.0.1:47931/downloads/$($job.id)" -Headers $h
        if ($status.status -in @("Completed", "Failed")) { break }
    }
    $same = $false
    if ($status.status -eq "Completed") {
        $same = (Get-FileHash $status.result_path).Hash -eq (Get-FileHash "$srvDir\payload.bin").Hash
    }
    Check "installed app downloads a file byte-exact" $same "$($status.status) $($status.result_path)"
    Stop-Process -Id $srv.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $app.Id -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Check "history database created in the data folder" (Test-Path "$Data\download_history.sqlite3")

    # ----------------------------------------------- re-install: program only (upgrade path)
    $code = Install "app" "startmenuicon" "$Work\install2.log"
    Check "re-install over the existing copy exits 0" ($code -eq 0) "exit $code"
    Check "unticked component's files are removed" (-not (Test-Path "$Dir\extensions"))
    Check "unticked desktop shortcut is removed" (-not (Test-Path $Desktop))
    $entries = @(Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" | Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -eq $Name })
    Check "still exactly one uninstall entry" ($entries.Count -eq 1) "$($entries.Count)"
    Check "user data untouched by re-install" ((Test-Path "$Data\settings.json") -and (Test-Path "$Data\download_history.sqlite3"))

    # -------------------------------------------------------------------- uninstall
    $p = Start-Process -FilePath "$Dir\unins000.exe" -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -PassThru -Wait
    Start-Sleep -Seconds 3  # the uninstaller finishes deleting itself after exiting
    Check "uninstall exits 0" ($p.ExitCode -eq 0) "exit $($p.ExitCode)"
    Check "program files removed" (-not (Test-Path "$Dir\MasterDownloadManager.exe")) "$(if (Test-Path $Dir) { (Get-ChildItem $Dir -Recurse | Measure-Object).Count } else { 0 }) leftover items"
    Check "Start menu shortcut removed" (-not (Test-Path $StartMenu))
    Check "uninstall entry removed" (-not (Test-Path $UninstallKey))
    Check "user data kept after uninstall" ((Test-Path "$Data\settings.json") -and (Test-Path "$Data\download_history.sqlite3"))
    Check "downloaded files kept after uninstall" (Test-Path $status.result_path)
}
finally {
    foreach ($lnk in $StartMenu, $Desktop) { if (Test-Path $lnk) { Remove-Item $lnk -Force } }
    if (Test-Path "$Dir\unins000.exe") {
        Start-Process -FilePath "$Dir\unins000.exe" -ArgumentList "/VERYSILENT" -Wait -ErrorAction SilentlyContinue
    }
}
$failed = @($results | Where-Object Result -eq "FAIL").Count
Write-Host ("`n{0}/{1} installer checks passed. Logs: {2}" -f ($results.Count - $failed), $results.Count, $Work)
exit $failed
