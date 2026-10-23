; Inno Setup script for Master Download Manager.
; Compiled by `python build.py installer` (uses the portable Inno Setup staged
; by tools/fetch_tools.py). Installs per-user by default (no admin needed).
;
; Pages: Welcome -> License Agreement (information, see docs/LEGAL_REVIEW_REQUIRED.md)
;        -> Choose Components -> Installation Location (new installs) -> Installing
;        (with Show details) -> Completing Setup (Run / Open GitHub).
; Unattended upgrade used by the in-app updater (ytd/update_install.py):
;   Setup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /MDMUPDATE=1 /WAITPID=<pid> /LOG=<file>
; waits for MDM to exit, upgrades in place and starts MDM again.

#ifndef AppVersion
  #error AppVersion must be passed by build.py (/DAppVersion=...) - ytd/__init__.py is the only version source
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\MasterDownloadManager"
#endif

#ifndef AppName
  #define AppName "Master Download Manager"
#endif
; Overridable only for the isolated install test (tools/installer_test.ps1); releases use the defaults.
#ifndef AppIdGuid
  #define AppIdGuid "{{6F2E9C1B-6A1D-4F6B-9B1E-6C9A2D7B3E41}"
#endif
#ifndef AppMutexName
  #define AppMutexName "Local\MasterDownloadManager.SingleInstance"
#endif
#ifndef OutputName
  #define OutputName "Master Download Manager Setup " + AppVersion
#endif
#ifndef DataFolder
  #define DataFolder "MasterDownloadManager"
#endif
#define AppExe "MasterDownloadManager.exe"
#define AppURL "https://github.com/enoluca22-dota2/master-download-manager"

[Setup]
AppId={#AppIdGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
; The product name, not a company: the build is unsigned and no registered publisher is claimed.
AppPublisher={#AppName}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
AppComments=Download manager for files, media sites and torrents
AppCopyright=Copyright (c) 2026 the Master Download Manager author
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright=Copyright (c) 2026 the Master Download Manager author
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
; Welcome -> License -> Components -> Location -> Installing -> Completing: the last choice page has the Install button.
DisableReadyPage=yes
; The built-in folder page comes before the components page; MDM shows its own
; "Installation Location" page after the components instead ([Code] DirPage),
; for new installs only. Upgrades keep the existing folder (same AppId ->
; in-place, one Installed Apps entry).
DisableDirPage=yes
UsePreviousAppDir=yes
UsePreviousSetupType=yes
PrivilegesRequired=lowest
; Per-user only in the wizard (no "all users" dialog before Welcome): unattended in-app updates must never need
; elevation. Administrators can still deploy machine-wide with /ALLUSERS on the command line.
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#OutputName}
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
; "License Agreement" page: information, no forced acceptance (no product EULA has been approved yet).
InfoBeforeFile=..\installer\license_information.txt
; Refuses to install/uninstall while the app is running (same mutex the app holds).
AppMutex={#AppMutexName}
CloseApplications=yes
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Welcome to [name] Setup
WelcomeLabel2=This wizard will guide you through the installation of [name/ver].%n%n[name] downloads files (multi-connection, resumable), videos and music from media sites, and torrents - with queues, a scheduler and a browser extension.%n%nInstalling over an earlier version keeps your settings, download history and queues.
WizardInfoBefore=License Agreement
InfoBeforeLabel=Please read the following license information.
InfoBeforeClickLabel=When you have read it, click Next to continue.
SelectComponentsLabel2=Select the parts of [name] you want and clear the ones you don't. The space each part needs is shown on the right.
FinishedHeadingLabel=Completing [name] Setup
FinishedLabelNoIcons=[name] has been successfully installed.
FinishedLabel=[name] has been successfully installed.

[Types]
Name: "full"; Description: "Recommended installation"
Name: "compact"; Description: "Program only"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "app"; Description: "{#AppName}"; Types: full compact custom; Flags: fixed
Name: "startmenu"; Description: "Start Menu shortcut"; Types: full
Name: "desktop"; Description: "Desktop shortcut"; Types: full
Name: "autostart"; Description: "Start with Windows (in the notification area)"
Name: "browser"; Description: "Browser integration files"; Types: full
Name: "assoc"; Description: "File associations"
Name: "assoc\torrent"; Description: ".torrent files"
Name: "assoc\magnet"; Description: "magnet: links"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "\extensions"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: app; AfterInstall: FileInstalled
Source: "{#SourceDir}\extensions\*"; DestDir: "{app}\extensions"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: browser; AfterInstall: FileInstalled

[InstallDelete]
; extension files from an older version are replaced as a whole (or removed when the component is unticked)
Type: filesandordirs; Name: "{app}\extensions"
Type: filesandordirs; Name: "{app}\docs"
; re-running setup with a shortcut unticked removes that shortcut
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Components: not desktop
Type: files; Name: "{autoprograms}\{#AppName}.lnk"; Components: not startmenu
Type: files; Name: "{autoprograms}\{#AppName} Licenses.lnk"; Components: not startmenu
; shortcut name used by an early 3.0 build (duplicate "Licenses" entry)
Type: files; Name: "{autoprograms}\{#AppName} — Licenses.lnk"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Comment: "Download files, videos, music and torrents"; Components: startmenu
Name: "{autoprograms}\{#AppName} Licenses"; Filename: "{app}\licenses"; Comment: "Third-party licenses"; Components: startmenu
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Comment: "Download files, videos, music and torrents"; Components: desktop
; same shortcut the app's own "Start with Windows" setting manages (ytd/startup.py)
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--minimized"; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"; Components: autostart

[Registry]
; .torrent: offered, never forced. Windows protects the user's chosen default
; (UserChoice), so this only adds MDM to "Open with" and to Settings > Default apps.
Root: HKA; Subkey: "Software\Classes\.torrent\OpenWithProgids"; ValueType: string; ValueName: "MasterDownloadManager.torrent"; ValueData: ""; Flags: uninsdeletevalue; Components: assoc\torrent
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.torrent"; ValueType: string; ValueName: ""; ValueData: "BitTorrent file"; Flags: uninsdeletekey; Components: assoc\torrent
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.torrent\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Components: assoc\torrent
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.torrent\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Components: assoc\torrent
; Windows "Default apps" registration: lets the user pick MDM in Settings; changes nothing by itself.
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey; Components: assoc\torrent assoc\magnet
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Download files, videos, music and torrents"; Components: assoc\torrent assoc\magnet
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities\FileAssociations"; ValueType: string; ValueName: ".torrent"; ValueData: "MasterDownloadManager.torrent"; Components: assoc\torrent
Root: HKA; Subkey: "Software\MasterDownloadManager\Capabilities\URLAssociations"; ValueType: string; ValueName: "magnet"; ValueData: "MasterDownloadManager.magnet"; Components: assoc\magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet"; ValueType: string; ValueName: ""; ValueData: "URL:Magnet link"; Flags: uninsdeletekey; Components: assoc\magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Components: assoc\magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Components: assoc\magnet
Root: HKA; Subkey: "Software\Classes\MasterDownloadManager.magnet\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Components: assoc\magnet
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "{#AppName}"; ValueData: "Software\MasterDownloadManager\Capabilities"; Flags: uninsdeletevalue; Components: assoc\torrent assoc\magnet
; magnet: itself is handled in [Code] so the previous handler (e.g. another download
; manager) is backed up first and restored afterwards, never deleted.

[Run]
; in-app update: start the new version (runs in silent mode too, unlike the postinstall entries)
Filename: "{app}\{#AppExe}"; Parameters: "--post-update"; Flags: nowait; Check: IsMdmUpdate
Filename: "{app}\{#AppExe}"; Description: "Run {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{#AppURL}"; Description: "Open the GitHub project page"; Flags: postinstall shellexec unchecked skipifsilent

[UninstallDelete]
; Program files only. Settings, history and downloads are kept unless the user chooses otherwise.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: filesandordirs; Name: "{app}\docs"
; the "Start with Windows" shortcut belongs to MDM whether Setup or the app created it
Type: files; Name: "{userstartup}\{#AppName}.lnk"

[Code]
const
  MagnetKey = 'Software\Classes\magnet';
  BackupKey = 'Software\MasterDownloadManager\AssociationBackup';
  SYNCHRONIZE = $00100000;

var
  CompDesc: TNewStaticText;
  DetailsMemo: TNewMemo;
  DetailsButton: TNewButton;
  InstallOK: Boolean;
  AppDirForRelaunch: String;
  DirPage: TInputDirWizardPage;

function OpenProcess(dwDesiredAccess: Cardinal; bInheritHandle: Boolean; dwProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function IsMdmUpdate: Boolean;
begin
  Result := ExpandConstant('{param:MDMUPDATE|0}') = '1';
end;

{ ---------------------------------------------------------------- update mode }
function UninstallKey: String;
begin
  Result := '{#AppIdGuid}';
  if Copy(Result, 1, 2) = '{{' then
    Delete(Result, 1, 1);  { "{{" is the [Setup] escape for a literal "{" }
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + Result + '_is1';
end;

function PreviousInstallDir: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', Result) or
     RegQueryStringValue(HKLM, UninstallKey, 'InstallLocation', Result) then
    Result := RemoveBackslashUnlessRoot(Result);
end;

{ 4.0.x had the shortcuts as tasks (startmenuicon, desktopicon); 4.1 has them
  as components. Carry the user's earlier choice over instead of re-adding a
  shortcut they had turned off. }
procedure CarryOverShortcutChoices;
var
  Tasks, Comps: String;
begin
  if RegQueryStringValue(HKA, UninstallKey, 'Inno Setup: Selected Tasks', Tasks) and
     RegQueryStringValue(HKA, UninstallKey, 'Inno Setup: Selected Components', Comps) and
     (Pos('startmenu', Comps) = 0) and (Pos('desktop', Comps) = 0) then
  begin
    if Pos('startmenuicon', Tasks) = 0 then
      WizardSelectComponents('!startmenu');
    if Pos('desktopicon', Tasks) = 0 then
      WizardSelectComponents('!desktop');
  end;
end;

function InitializeSetup: Boolean;
var
  Pid: Integer;
  H: THandle;
begin
  Result := True;
  { known from the start, so the old version is restarted even if Setup fails
    before it begins copying files }
  if IsMdmUpdate then
    AppDirForRelaunch := PreviousInstallDir;
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid > 0 then
  begin
    { the in-app updater started us: wait (max 90 s) for that MDM process to exit }
    H := OpenProcess(SYNCHRONIZE, False, Pid);
    if H <> 0 then
    begin
      WaitForSingleObject(H, 90000);
      CloseHandle(H);
    end;
  end;
end;

procedure DeinitializeSetup;
var
  Code: Integer;
begin
  { In-app update that didn't complete: Setup has rolled its changes back, so
    start the previous version again; it reports the failure to the user. }
  if IsMdmUpdate and (not InstallOK) and (AppDirForRelaunch <> '') and
     FileExists(AppDirForRelaunch + '\{#AppExe}') then
    Exec(AppDirForRelaunch + '\{#AppExe}', '--post-update', AppDirForRelaunch, SW_SHOWNORMAL, ewNoWait, Code);
end;

{ ---------------------------------------------------------- components page }
procedure ComponentsClick(Sender: TObject);
var
  S: String;
begin
  case WizardForm.ComponentsList.ItemIndex of
    0: S := 'The program with everything it needs: its own Python runtime, the media engine (yt-dlp), FFmpeg, the JavaScript runtime and the torrent engine. Required.';
    1: S := 'Adds a Master Download Manager shortcut to the Start Menu.';
    2: S := 'Puts a Master Download Manager shortcut on your desktop.';
    3: S := 'Starts Master Download Manager minimized in the notification area when you sign in to Windows, so scheduled queues and browser downloads work right away. You can change this later in Settings.';
    4: S := 'Copies the Chrome/Edge and Firefox extension files into the program folder (extensions). Loading them into your browser is explained in Help -> Browser Integration Guide.';
    5: S := 'Lets Master Download Manager open .torrent files and magnet: links. Your current default apps are kept for .torrent files; a previous magnet: handler is restored on uninstall.';
    6: S := 'Adds Master Download Manager to "Open with" for .torrent files and to Windows Default apps.';
    7: S := 'Opens magnet: links in Master Download Manager (the previous handler is backed up and restored when you uninstall).';
  else
    S := '';
  end;
  CompDesc.Caption := S;
end;

{ ------------------------------------------------------------ installing page }
procedure Detail(const S: String);
begin
  if DetailsMemo <> nil then
    DetailsMemo.Lines.Add(S);
end;

procedure FileInstalled;
var
  F, AppDir: String;
begin
  F := ExpandConstant(CurrentFileName);
  AppDir := ExpandConstant('{app}\');
  if Pos(Lowercase(AppDir), Lowercase(F)) = 1 then
    F := Copy(F, Length(AppDir) + 1, Length(F));
  Detail('Extracted ' + F);
end;

procedure DetailsClick(Sender: TObject);
begin
  DetailsMemo.Visible := not DetailsMemo.Visible;
  if DetailsMemo.Visible then
    DetailsButton.Caption := 'Hide &details'
  else
    DetailsButton.Caption := 'Show &details';
end;

{ ------------------------------------------------- installation location page }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { upgrades keep the folder that is already in use }
  Result := (DirPage <> nil) and (PageID = DirPage.ID) and (WizardForm.PrevAppDir <> '');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { No "Ready" page: the last choice page starts the installation. That is the
    location page, or the components page on an upgrade (location skipped). }
  if ((DirPage <> nil) and (CurPageID = DirPage.ID)) or
     ((CurPageID = wpSelectComponents) and (WizardForm.PrevAppDir <> '')) then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (DirPage <> nil) and (CurPageID = DirPage.ID) then
  begin
    if Trim(DirPage.Values[0]) = '' then
    begin
      MsgBox('Please choose a folder.', mbError, MB_OK);
      Result := False;
    end
    else
      WizardForm.DirEdit.Text := DirPage.Values[0];
  end;
end;

procedure InitializeWizard;
var
  L: TNewCheckListBox;
  P: TNewNotebookPage;
begin
  DirPage := CreateInputDirPage(wpSelectComponents, 'Installation Location',
    'Where should {#AppName} be installed?',
    '{#AppName} will be installed into the following folder. To start the installation, click Install. ' +
    'To choose a different folder, click Browse.' + #13#10#13#10 +
    'The space needed is shown on the previous page. Your settings and downloads are stored ' +
    'separately and are not affected by this choice.', False, '');
  DirPage.Add('');
  DirPage.Values[0] := WizardForm.DirEdit.Text;

  { description panel under the component list }
  L := WizardForm.ComponentsList;
  L.Height := L.Height - ScaleY(64);
  CompDesc := TNewStaticText.Create(WizardForm);
  CompDesc.Parent := WizardForm.SelectComponentsPage;
  CompDesc.Left := L.Left;
  CompDesc.Top := L.Top + L.Height + ScaleY(8);
  CompDesc.Width := L.Width;
  CompDesc.Height := ScaleY(56);
  CompDesc.AutoSize := False;
  CompDesc.WordWrap := True;
  CompDesc.Caption := 'Click an item to see what it does.';
  L.OnClick := @ComponentsClick;
  L.OnClickCheck := @ComponentsClick;
  CarryOverShortcutChoices;

  { "Show details" log on the installing page }
  P := WizardForm.InstallingPage;
  DetailsButton := TNewButton.Create(WizardForm);
  DetailsButton.Parent := P;
  DetailsButton.Left := WizardForm.ProgressGauge.Left;
  DetailsButton.Top := WizardForm.ProgressGauge.Top + WizardForm.ProgressGauge.Height + ScaleY(10);
  DetailsButton.Width := ScaleX(110);
  DetailsButton.Height := ScaleY(24);
  DetailsButton.Caption := 'Show &details';
  DetailsButton.OnClick := @DetailsClick;
  DetailsMemo := TNewMemo.Create(WizardForm);
  DetailsMemo.Parent := P;
  DetailsMemo.Left := WizardForm.ProgressGauge.Left;
  DetailsMemo.Top := DetailsButton.Top + DetailsButton.Height + ScaleY(6);
  DetailsMemo.Width := WizardForm.ProgressGauge.Width;
  DetailsMemo.Height := P.ClientHeight - DetailsMemo.Top - ScaleY(4);
  DetailsMemo.ScrollBars := ssVertical;
  DetailsMemo.ReadOnly := True;
  DetailsMemo.Visible := False;
end;

{ ------------------------------------------------------------- associations }
function OurExe: String;
begin
  Result := ExpandConstant('{app}\{#AppExe}');
end;

function MagnetIsOurs: Boolean;
var
  Cmd: String;
begin
  { Ours only if it starts THIS install's exe: another copy (portable, test,
    other folder) must never take over or remove this one's registration. }
  Result := RegQueryStringValue(HKCU, MagnetKey + '\shell\open\command', '', Cmd) and
            (Pos(Lowercase(OurExe), Lowercase(Cmd)) > 0);
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
var
  Cmd: String;
begin
  { Same rule as magnet: only remove the offer if it points at this install. }
  if RegQueryStringValue(HKCU, 'Software\Classes\MasterDownloadManager.torrent\shell\open\command', '', Cmd) and
     (Pos(Lowercase(OurExe), Lowercase(Cmd)) = 0) then
    exit;
  RegDeleteValue(HKCU, 'Software\Classes\.torrent\OpenWithProgids', 'MasterDownloadManager.torrent');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\MasterDownloadManager.torrent');
end;

#ifdef UpdateFailTest
{ test builds only (tools/update_e2e_test.py): an update that fails before installing }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := 'Simulated installation failure (update rollback test build).';
end;
#endif

{ --------------------------------------------------------------- install steps }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    AppDirForRelaunch := ExpandConstant('{app}');
    Detail('Creating installation directories in ' + AppDirForRelaunch);
    Detail('Installing the program and its bundled runtime (Python, media and torrent engines, FFmpeg)...');
  end;
  if CurStep = ssPostInstall then
  begin
    Detail('Registering shortcuts and the uninstaller');
    Detail('Configuring components');
#ifndef InstallTest
    { Re-running setup with an option unticked turns that association off again. }
    if WizardIsComponentSelected('assoc\magnet') then
    begin
      Detail('Registering magnet: links');
      RegisterMagnet;
    end
    else
      UnregisterMagnet;
    if not WizardIsComponentSelected('assoc\torrent') then
      RemoveTorrentOffer
    else
      Detail('Offering Master Download Manager for .torrent files');
#endif
  end;
  if CurStep = ssDone then
  begin
    Detail('Finalizing installation - done.');
    InstallOK := True;
  end;
end;

{ Removes only the files and folders MDM itself creates in its data folder, so
  anything else kept there (even a download folder someone pointed inside it)
  survives; the folder itself goes only if it is then empty. }
procedure RemoveUserData(DataDir: String);
var
  Names: TArrayOfString;
  I: Integer;
  F: TFindRec;
begin
  Names := ['settings.json', 'queue.json', 'queues.json', 'scheduler.json', 'download_history.json',
            'download_history.sqlite3', 'download_history.sqlite3-wal', 'download_history.sqlite3-shm',
            'download_history.sqlite3-journal'];
  for I := 0 to GetArrayLength(Names) - 1 do
    DeleteFile(DataDir + '\' + Names[I]);
  if FindFirst(DataDir + '\*.corrupt-*', F) then
  try
    repeat
      DeleteFile(DataDir + '\' + F.Name);
    until not FindNext(F);
  finally
    FindClose(F);
  end;
  DelTree(DataDir + '\logs', True, True, True);
  DelTree(DataDir + '\cache', True, True, True);
  DelTree(DataDir + '\updates', True, True, True);
  RemoveDir(DataDir);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
#ifndef InstallTest
  if CurUninstallStep = usUninstall then
    UnregisterMagnet;
#endif
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    DataDir := ExpandConstant('{localappdata}\{#DataFolder}');
    if DirExists(DataDir) and (MsgBox('Do you also want to remove your Master Download Manager settings, download history, queues and logs?' + #13#10#13#10 +
        'Choose No to keep them (recommended if you may install it again). Your downloaded files are never deleted either way.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
      RemoveUserData(DataDir);
  end;
end;
