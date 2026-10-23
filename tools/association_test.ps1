# Association lifecycle test for the Master Download Manager installer.
# Installs into a temp folder (per-user), toggles the optional .torrent / magnet:
# associations and checks that another app's existing registration (e.g. Ant
# Download Manager) is preserved, restored on "disable", and restored on uninstall.
#   powershell -File tools\association_test.ps1 -Setup <installer.exe> -Torrent <file.torrent>
param([Parameter(Mandatory)] [string]$Setup, [Parameter(Mandatory)] [string]$Torrent)

$ErrorActionPreference = "Stop"
$dir = Join-Path $env:TEMP "mdm-assoc-install"
$env:MDM_DATA_DIR = Join-Path $env:TEMP "mdm-assoc-data"
$results = @()
function Check($name, $ok, $detail = "") {
  $script:results += [pscustomobject]@{ Name = $name; Ok = [bool]$ok }
  "{0}  {1}  {2}" -f ($(if ($ok) { "PASS" } else { "FAIL" })), $name, $detail
}
function Snap { [pscustomobject]@{
  magnet_cmd = (Get-ItemProperty "HKCU:\Software\Classes\magnet\shell\open\command" -EA SilentlyContinue)."(default)"
  magnet_icon = (Get-ItemProperty "HKCU:\Software\Classes\magnet\DefaultIcon" -EA SilentlyContinue)."(default)"
  magnet_name = (Get-ItemProperty "HKCU:\Software\Classes\magnet" -EA SilentlyContinue)."(default)"
  torrent_default = (Get-ItemProperty "HKCU:\Software\Classes\.torrent" -EA SilentlyContinue)."(default)"
  torrent_userchoice = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.torrent\UserChoice" -EA SilentlyContinue).Progid
  mdm_openwith = [bool](Get-ItemProperty "HKCU:\Software\Classes\.torrent\OpenWithProgids" -Name "MasterDownloadManager.torrent" -EA SilentlyContinue)
  mdm_registered = [bool](Get-ItemProperty "HKCU:\Software\RegisteredApplications" -Name "Master Download Manager" -EA SilentlyContinue)
  mdm_keys = (Test-Path "HKCU:\Software\Classes\MasterDownloadManager.torrent") -or (Test-Path "HKCU:\Software\Classes\MasterDownloadManager.magnet") -or (Test-Path "HKCU:\Software\MasterDownloadManager")
} }
function Install($tasks) {
  $p = Start-Process $Setup -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART", "/TASKS=""$tasks""", "/DIR=""$dir""") -Wait -PassThru
  return $p.ExitCode
}
function Windows() {
  Add-Type -ErrorAction SilentlyContinue @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class Win { public delegate bool CB(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(CB cb, IntPtr l);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 public static List<string> Titles() { var r = new List<string>(); EnumWindows((h, l) => { if (IsWindowVisible(h)) { var s = new StringBuilder(256); GetWindowText(h, s, 256); if (s.Length > 0) r.Add(s.ToString()); } return true; }, IntPtr.Zero); return r; } }
"@
  return [Win]::Titles() | Where-Object { $_ -like "*Master Download Manager*" }
}
function StopApp { Get-Process MasterDownloadManager -EA SilentlyContinue | Stop-Process -Force; Start-Sleep 2 }

$before = Snap
"Before: " + ($before | Out-String)

# 1) default install: associations stay off
Check "default (silent) install succeeds" ((Install "desktopicon") -eq 0)
$s = Snap
Check "default install leaves the existing magnet handler untouched" ($s.magnet_cmd -eq $before.magnet_cmd) $s.magnet_cmd
Check "default install leaves the .torrent default untouched" ($s.torrent_default -eq $before.torrent_default -and $s.torrent_userchoice -eq $before.torrent_userchoice)

# 2) enable both options
Check "install with both association options" ((Install "desktopicon,assoc_torrent,assoc_magnet") -eq 0)
$s = Snap
$exe = Join-Path $dir "MasterDownloadManager.exe"
Check "magnet: now opens Master Download Manager" ($s.magnet_cmd -like "*MasterDownloadManager.exe*") $s.magnet_cmd
Check "previous magnet handler was backed up" ((Get-ItemProperty "HKCU:\Software\MasterDownloadManager\AssociationBackup" -EA SilentlyContinue).magnet_command -eq $before.magnet_cmd)
Check ".torrent: MDM offered in 'Open with' and Default apps" ($s.mdm_openwith -and $s.mdm_registered)
Check ".torrent: the user's chosen default app is not overridden" ($s.torrent_userchoice -eq $before.torrent_userchoice -and $s.torrent_default -eq $before.torrent_default) "$($s.torrent_userchoice)"

StopApp
Start-Process "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=association-test"
Start-Sleep 9
$w = Windows
Check "clicking a magnet: link launches MDM with the link (Add dialog opens)" (($w | Where-Object { $_ -like "Add download*" }).Count -ge 1) ($w -join " | ")
StopApp
$cmd = (Get-ItemProperty "HKCU:\Software\Classes\MasterDownloadManager.torrent\shell\open\command")."(default)".Replace("%1", $Torrent)
Start-Process -FilePath $exe -ArgumentList ('"' + $Torrent + '"')
Start-Sleep 9
$w = Windows
Check "'Open with > Master Download Manager' on a .torrent opens it in MDM" (($w | Where-Object { $_ -like "Add download*" }).Count -ge 1) $cmd
StopApp

# 3) disable by re-running setup with the options unticked
Check "re-run setup with the options unticked" ((Install "desktopicon") -eq 0)
$s = Snap
Check "disabling restores the previous magnet handler exactly" ($s.magnet_cmd -eq $before.magnet_cmd -and $s.magnet_icon -eq $before.magnet_icon -and $s.magnet_name -eq $before.magnet_name) $s.magnet_cmd
Check "disabling removes MDM from .torrent 'Open with'" (-not $s.mdm_openwith)

# 4) enable again, then uninstall
Install "desktopicon,assoc_torrent,assoc_magnet" | Out-Null
StopApp
$p = Start-Process (Join-Path $dir "unins000.exe") -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru
Start-Sleep 3
$s = Snap
Check "uninstall succeeds" ($p.ExitCode -eq 0 -and -not (Test-Path $exe))
Check "uninstall restores the previous magnet handler exactly" ($s.magnet_cmd -eq $before.magnet_cmd -and $s.magnet_icon -eq $before.magnet_icon -and $s.magnet_name -eq $before.magnet_name) $s.magnet_cmd
Check "uninstall leaves the .torrent default as it was" ($s.torrent_default -eq $before.torrent_default -and $s.torrent_userchoice -eq $before.torrent_userchoice)
Check "uninstall removes every MDM association key" (-not $s.mdm_openwith -and -not $s.mdm_registered -and -not $s.mdm_keys)

$failed = ($results | Where-Object { -not $_.Ok }).Count
"`n{0}/{1} association checks passed" -f ($results.Count - $failed), $results.Count
