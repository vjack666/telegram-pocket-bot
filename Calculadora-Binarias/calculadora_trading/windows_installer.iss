[Setup]
AppId={{2E13CFD1-7B0B-47E5-BABB-6BF2F9E6C33C}
AppName=Calculadora Trading
AppVersion=1.0.0
AppPublisher=v_jac
DefaultDirName={autopf}\Calculadora Trading
DefaultGroupName=Calculadora Trading
DisableProgramGroupPage=yes
OutputDir=build\windows\installer
OutputBaseFilename=CalculadoraTradingSetup-1.0.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\calculadora_trading.exe

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "build\windows\x64\runner\Release\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Calculadora Trading"; Filename: "{app}\calculadora_trading.exe"
Name: "{autodesktop}\Calculadora Trading"; Filename: "{app}\calculadora_trading.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\calculadora_trading.exe"; Description: "Abrir Calculadora Trading"; Flags: nowait postinstall skipifsilent