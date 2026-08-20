; Сборка установщика Velix для Inno Setup 6.
; Собирается командой:
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
; Перед этим нужен собранный Velix.exe (см. README, раздел «Сборка .exe»).

#define AppName "Velix"
#define AppVersion "1.2.0"
#define AppPublisher "Vexorter42"
#define AppExe "Velix.exe"

[Setup]
; Свой AppId, чтобы обновления ставились поверх, а не плодили копии
AppId={{7F3C1E64-58B2-4E0C-9E4B-2C8B5A1D9E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Ставим в профиль пользователя — тогда установщик не просит прав администратора
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=VelixSetup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
Source: "Velix.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить Velix"; Flags: nowait postinstall skipifsilent
