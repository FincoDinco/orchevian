; Orchevian Windows installer (Inno Setup 6). Built by scripts/installers.py:
;   iscc /DAppVersion=... /DSourceDir=... /DOutputDir=... /DOutputName=... orchevian.iss
; Installs for the current user, so no administrator prompt is needed.

#define AppName "Orchevian"

[Setup]
AppId={{6A7C2F55-3E4B-4B8A-9C0E-1F2D3A4B5C6D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Seth Hardin
AppPublisherURL=https://github.com/FincoDinco/orchevian
AppSupportURL=https://github.com/FincoDinco/orchevian/issues
AppCopyright=Copyright (C) 2026 Seth Hardin
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
LicenseFile={#LicenseFile}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Orchevian.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Orchevian.exe"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\Orchevian.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Orchevian.exe"; Description: "Open Orchevian"; Flags: nowait postinstall skipifsilent
