#define AppName "MediaDownloader"
#define AppVersion "1.1.5"
#define AppPublisher "MediaDownloader"

[Setup]
AppId={{8C58A13B-2B48-48C8-9E82-CF498A0D04E3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=installer_output
OutputBaseFilename=MediaDownloader-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\MediaDownloader\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\MediaDownloader.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\MediaDownloader.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Run]
Filename: "{app}\MediaDownloader.exe"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent
