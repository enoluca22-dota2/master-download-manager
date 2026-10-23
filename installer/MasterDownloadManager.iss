; Inno Setup script for Master Download Manager.
; Compiled by `python build.py installer` (uses the portable Inno Setup staged
; by tools/fetch_tools.py). Installs per-user by default (no admin needed).

#ifndef AppVersion
  #define AppVersion "4.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\MasterDownloadManager"
#endif

#define AppName "Master Download Manager"
#define AppExe "MasterDownloadManager.exe"
#define AppURL "https://github.com/enoluca22-dota2/master-download-manager"

[Setup]
AppId={{6F2E9C1B-6A1D-4F6B-9B1E-6C9A2D7B3E41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
; The product name, not a company: the build is unsigned and no publisher is claimed.
AppPublisher={#AppName}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppComments=Download manager for files, media sites and torrents
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
; Upgrades install over the existing copy (same AppId): no uninstall first, settings/history untouched,
; the previous install folder and task choices are reused.
UsePreviousAppDir=yes
UsePreviousTasks=yes
UsePreviousSetupType=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Master Download Manager Setup {#AppVersion}
SetupIconFile=..\mdm.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dynamic windows11
WizardImageFile=art\wizard-light-1x.bmp,art\wizard-light-2x.bmp
WizardImageFileDynamicDark=art\wizard-dark-1x.bmp,art\wizard-dark-2x.bmp
WizardSmallImageFile=art\wizard-small-light-1x.bmp,art\wizard-small-light-2x.bmp
WizardSmallImageFileDynamicDark=art\wizard-small-dark-1x.bmp,art\wizard-small-dark-2x.bmp
InfoBeforeFile=..\installer\before_install.txt
; Refuses to install/uninstall while the app is running (same mutex the app holds).
AppMutex=Local\MasterDownloadManager.SingleInstance
CloseApplications=yes
ChangesAssociations=yes
InfoAfterFile=..\installer\after_install.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Full installation (recommended)"
Name: "compact"; Description: "Program only (no browser extension files)"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "app"; Description: "Master Download Manager (with media engine, FFmpeg and torrent engine)"; Types: full compact custom; Flags: fixed
Name: "browser"; Description: "Browser extension files (Chrome, Edge, Firefox) — load them from Settings → Browser Integration"; Types: full

[Tasks]
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Shortcuts:"
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "assoc_torrent"; Description: "Offer {#AppName} for .&torrent files (adds it to ""Open with"" and Windows Default apps; your current default app stays)"; GroupDescription: "Optional file and link types:"; Flags: unchecked
Name: "assoc_magnet"; Description: "Open &magnet: links with {#AppName} (your current magnet app is restored on uninstall)"; GroupDescription: "Optional file and link types:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\extensions\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app
Source: "{#SourceDir}\extensions\*"; DestDir: "{app}\extensions"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: browser

[InstallDelete]
; extension files from an older version are replaced as a whole (or removed when the component is unticked)
Type: filesandordirs; Name: "{app}\extensions"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Comment: "Download videos, playlists, files and torrents"; Tasks: startmenuicon
Name: "{autoprograms}\{#AppName} Licenses"; Filename: "{app}\licenses"; Comment: "Third-party licenses"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Comment: "Download videos, playlists, files and torrents"; Tasks: desktopicon

[Registry]
; .torrent: offered, never forced. Windows protects the user's chosen default
; (UserChoice), so this only adds MDM to "Open with" and to Settings > Default apps.
Root: HKA; Subkey: "Software\Classes\.torrent\OpenWithProgids"; ValueType: string; ValueName: "MasterDownloadManager.torrent"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoc_torrent
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.torrent"; ValueType: string; ValueName: ""; ValueData: "BitTorrent file"; Flags: uninsdeletekey; Tasks: assoc_torrent
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.torrent\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc_torrent
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.torrent\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc_torrent
; Windows "Default apps" registration: lets the user pick MDM in Settings; changes nothing by itself.
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Tasks: assoc_torrent assoc_magnet
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Download videos, playlists, files and torrents"; Tasks: assoc_torrent assoc_magnet
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities\FileAssociations"; ValueType: string; ValueName: ".torrent"; ValueData: "MasterDownloadManager.torrent"; Tasks: assoc_torrent
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities\URLAssociations"; ValueType: string; ValueName: "magnet"; ValueData: "MasterDownloadManager.magnet"; Tasks: assoc_magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet"; ValueType: string; ValueName: ""; ValueData: "URL:Magnet link"; Flags: uninsdeletekey; Tasks: assoc_magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: assoc_magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc_magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc_magnet
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "Software\MasterDownloadManager\Capabilities"; Flags: uninsdeletevalue; Tasks: assoc_torrent assoc_magnet
; magnet: itself is handled in [Code] so the previous handler (e.g. another download
; manager) is backed up first and restored afterwards, never deleted.

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{#AppURL}"; Description: "Open the project page on GitHub"; Flags: postinstall shellexec unchecked skipifsilent

[UninstallDelete]
; Program files only. Your settings, history and downloads are kept
; (%LOCALAPPDATA%\MasterDownloadManager and your download folders).
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
const
  MagnetKey = 'Software\Classes\magnet';
  BackupKey = 'Software\MasterDownloadManager\AssociationBackup';

function OurExe: String;
begin
  Result := ExpandConstant('{app}\{#AppExe}');
end;

function MagnetIsOurs: Boolean;
var
  Cmd: String;
begin
  Result := RegQueryStringValue(HKCU, MagnetKey + '\shell\open\command', '', Cmd) and
            (Pos(Lowercase('{#AppExe}'), Lowercase(Cmd)) > 0);
end;

procedure BackupValue(const SubKey, Name, BackupName: String);
var
  V: String;
begin
  if RegQueryStringValue(HKCU, MagnetKey + SubKey, Name, V) then
    RegWriteStringValue(HKCU, BackupKey, BackupName, V);
end;

procedure RestoreValue(const SubKey, Name, BackupName: String);
var
  V: String;
begin
  if RegQueryStringValue(HKCU, BackupKey, BackupName, V) then
    RegWriteStringValue(HKCU, MagnetKey + SubKey, Name, V)
  else
    RegDeleteValue(HKCU, MagnetKey + SubKey, Name);
end;

procedure RegisterMagnet;
begin
  if not MagnetIsOurs then
  begin
    RegDeleteKeyIncludingSubkeys(HKCU, BackupKey);
    if RegKeyExists(HKCU, MagnetKey) then
    begin
      BackupValue('', '', 'magnet_default');
      BackupValue('\DefaultIcon', '', 'magnet_icon');
      BackupValue('\shell\open\command', '', 'magnet_command');
      RegWriteStringValue(HKCU, BackupKey, 'had_handler', '1');
    end
    else
      RegWriteStringValue(HKCU, BackupKey, 'had_handler', '0');
  end;
  RegWriteStringValue(HKCU, MagnetKey, '', 'URL:Magnet link');
  RegWriteStringValue(HKCU, MagnetKey, 'URL Protocol', '');
  RegWriteStringValue(HKCU, MagnetKey + '\DefaultIcon', '', OurExe + ',0');
  RegWriteStringValue(HKCU, MagnetKey + '\shell\open\command', '', '"' + OurExe + '" "%1"');
end;

procedure UnregisterMagnet;
var
  Had: String;
begin
  { Only undo our own registration; if another app took magnet: over since, leave it alone. }
  if MagnetIsOurs then
  begin
    if RegQueryStringValue(HKCU, BackupKey, 'had_handler', Had) and (Had = '1') then
    begin
      RestoreValue('', '', 'magnet_default');
      RestoreValue('\DefaultIcon', '', 'magnet_icon');
      RestoreValue('\shell\open\command', '', 'magnet_command');
    end
    else
      RegDeleteKeyIncludingSubkeys(HKCU, MagnetKey);
  end;
  RegDeleteKeyIncludingSubkeys(HKCU, BackupKey);
end;

procedure RemoveTorrentOffer;
begin
  RegDeleteValue(HKCU, 'Software\Classes\.torrent\OpenWithProgids', 'MasterDownloadManager.torrent');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\MasterDownloadManager.torrent');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Re-running setup with an option unticked turns that association off again. }
    if WizardIsTaskSelected('assoc_magnet') then
      RegisterMagnet
    else
      UnregisterMagnet;
    if not WizardIsTaskSelected('assoc_torrent') then
      RemoveTorrentOffer;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    UnregisterMagnet;
end;
