[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName=Pocket Option Bot
AppVersion=1.0.0
AppPublisher=PocketBot
DefaultDirName={autopf}\Pocket Option Bot
DefaultGroupName=Pocket Option Bot
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PocketOptionBot-Setup-1.0.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\PocketOptionBot.exe

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "..\dist\PocketOptionBot_v1.0\PocketOptionBot.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\PocketOptionBot_v1.0\.env";               DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "..\dist\PocketOptionBot_v1.0\LEEME.txt";          DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Pocket Option Bot"; Filename: "{app}\PocketOptionBot.exe"
Name: "{autodesktop}\Pocket Option Bot";  Filename: "{app}\PocketOptionBot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PocketOptionBot.exe"; Description: "Iniciar Pocket Option Bot"; Flags: nowait postinstall skipifsilent
