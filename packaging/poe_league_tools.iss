#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define AppName "PoE League Tools"
#define AppPublisher "Cyrus Hadavi"
#define AppExeName "PoE League Tools.exe"

[Setup]
AppId=PoELeagueTools.CyrusHadavi
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/cyrushadavi1/poe-league-tools
AppSupportURL=https://github.com/cyrushadavi1/poe-league-tools/issues
AppUpdatesURL=https://github.com/cyrushadavi1/poe-league-tools/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=PoE-League-Tools-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion=0.0.0.0
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Path of Exile party leveling overlay

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
  GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\PoE League Tools\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
  WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName} - Setup or Change Character"; \
  Filename: "{app}\{#AppExeName}"; Parameters: "--setup"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
  WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent
