; Inno Setup script for facbock.yo (FB → YouTube Uploader Pro).
; Compile on Windows after building the bundle (build_windows.bat):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=4.49.25 installer\facbock-yo.iss
; or let the GitHub release workflow do it (.github/workflows/release.yml).
; The version is passed in with /DAppVersion; the default below must stay
; in sync with version.py (tests/test_packaging.py enforces this).

#ifndef AppVersion
  #define AppVersion "4.49.29"
#endif
#define AppName "facbock.yo — FB to YouTube Uploader Pro"
#define AppPublisher "eldjazaireldjadida4-web"
#define AppURL "https://github.com/eldjazaireldjadida4-web/facbock.yo"

[Setup]
AppId={{766EA5C5-5C0E-4914-8A29-1EDF344953CB}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
; Per-user install on purpose: the app keeps config, tokens, receipts and
; logs beside the program (APP_DIRECTORY), so the install directory MUST
; be writable by the user. Never install into Program Files.
DefaultDirName={localappdata}\Programs\facbock.yo
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer-output
OutputBaseFilename=facbock-yo-{#AppVersion}-windows-x86_64-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
; The uninstaller only removes files it installed; config.ini, tokens,
; receipts and logs created by the app at runtime stay in place, so a
; reinstall keeps the user's settings (delete the folder by hand for a
; full wipe).

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller one-dir bundle (built by build_windows.bat).
; NEVER list a real config.ini, *_token.json or client_secrets.json here:
; user secrets must never ship inside the installer.
Source: "..\dist\facbock-yo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; First-run config from the shipped examples, only when the user has none
; yet (keeps settings across upgrades; never overwritten by reinstalls).
Source: "..\dist\facbock-yo\config.ini.example"; DestDir: "{app}"; DestName: "config.ini"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\dist\facbock-yo\rights_manifest.example.json"; DestDir: "{app}"; DestName: "rights_manifest.json"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\facbock-yo.exe"
Name: "{autoprograms}\facbock.yo Doctor (readiness check)"; Filename: "{app}\facbock-yo-doctor.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\facbock-yo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\facbock-yo.exe"; Description: "Launch facbock.yo"; Flags: postinstall nowait skipifsilent unchecked
