; Instalator Inno Setup 6 — budowany przez build.py (wersja i nazwa pliku z /D...).
; Instalacja per użytkownik (bez uprawnień administratora), więc aktualizacja z programu
; nie pyta o UAC. Dane (połączenia, hasła) leżą w %APPDATA%\SSH-RDP-Manager, nie tutaj —
; odinstalowanie ich nie zabiera.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef OutputName
  #define OutputName "SSH-RDP-Manager-setup"
#endif

[Setup]
; Stały identyfikator — po nim instalator rozpoznaje starszą wersję do nadpisania. Nie zmieniać.
AppId={{6B0E2B1A-7C4D-4E8F-9A35-2D1C8F4B7E60}
AppName=SSH-RDP Manager
AppVersion={#AppVersion}
AppPublisher=Bochnovic
AppPublisherURL=https://github.com/DawidBochno/ssh-rdp-manager
DefaultDirName={autopf}\SSH-RDP-Manager
DefaultGroupName=SSH-RDP Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\SSH-RDP-Manager.exe
; Aktualizacja z programu: zamknij działający program, potem uruchom go ponownie.
CloseApplications=yes

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\SSH-RDP-Manager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Stare moduły z poprzedniej wersji nie mogą zostać obok nowych.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\SSH-RDP Manager"; Filename: "{app}\SSH-RDP-Manager.exe"
Name: "{autodesktop}\SSH-RDP Manager"; Filename: "{app}\SSH-RDP-Manager.exe"; Tasks: desktopicon

[Run]
; Bez `skipifsilent`: po aktualizacji z programu (/SILENT) uruchamia go z powrotem.
Filename: "{app}\SSH-RDP-Manager.exe"; Description: "{cm:LaunchProgram,SSH-RDP Manager}"; Flags: nowait postinstall
