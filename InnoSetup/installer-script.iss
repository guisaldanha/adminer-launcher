; ============================================================
; Adminer Launcher - Installer
; ============================================================

#define MyAppName "Adminer Launcher"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "GuiSaldanha.com"
#define MyAppURL "https://www.guisaldanha.com/"
#define MyAppExeName "adminer-launcher.exe"

; Runtime Versions
#define PHPVersion "8.5.3"
#define AdminerVersion "5.4.2"

[Setup]
AppId={{0E347277-D781-4FC0-A738-5B163D82C438}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=adminer-launcher-setup-{#MyAppVersion}
SetupIconFile=..\docs\img\Icone-Adminer-Launcher-Installer.ico
SolidCompression=yes
WizardStyle=modern dynamic
LicenseFile=..\InnoSetup\licenses.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\adminer-launcher\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\adminer-launcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"


[Code]

const
  PHP_URL = 'https://downloads.php.net/~windows/releases/archives/php-{#PHPVersion}-Win32-vs17-x64.zip';
  ADMINER_URL = 'https://github.com/vrana/adminer/releases/download/v{#AdminerVersion}/adminer-{#AdminerVersion}.php';


function GenerateInstallKeyString(): String;
var
  chars, S: String;
  i: Integer;
begin
  chars := '0123456789ABCDEF';
  S := '';
  for i := 1 to 32 do
    S := S + chars[Random(16) + 1];

  Result :=
    Copy(S, 1, 8) + '-' +
    Copy(S, 9, 4) + '-' +
    Copy(S, 13, 4) + '-' +
    Copy(S, 17, 4) + '-' +
    Copy(S, 21, 12);
end;


procedure GenerateInstallKey();
var
  key, path: String;
begin
  path := ExpandConstant('{app}\install.key');

  if not FileExists(path) then
  begin
    key := GenerateInstallKeyString();
    SaveStringToFile(path, key, False);
  end;
end;


function DownloadFile(url, dest: String): Boolean;
var
  ResultCode: Integer;
  cmd: String;
begin
  cmd :=
    '-NoProfile -ExecutionPolicy Bypass -Command ' +
    '"Invoke-WebRequest -Uri ''' + url + ''' -OutFile ''' + dest + '''"';

  Result :=
    Exec('powershell.exe', cmd, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)
      and (ResultCode = 0);
end;


function ExtractZip(zipPath, destPath: String): Boolean;
var
  ResultCode: Integer;
  cmd: String;
begin
  cmd :=
    '-NoProfile -ExecutionPolicy Bypass -Command ' +
    '"Expand-Archive -LiteralPath ''' + zipPath +
    ''' -DestinationPath ''' + destPath + ''' -Force"';

  Result :=
    Exec('powershell.exe', cmd, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode)
      and (ResultCode = 0);
end;


procedure SetupPHP();
var
  zipPath, phpDir: String;
  iniProd, iniFile: String;
  contentAnsi: AnsiString;
  content: String;
begin
  phpDir := ExpandConstant('{app}\php');
  zipPath := ExpandConstant('{tmp}\php.zip');

  ForceDirectories(phpDir);

  WizardForm.StatusLabel.Caption := 'Downloading PHP {#PHPVersion}...';
  WizardForm.Update;

  if not DownloadFile(PHP_URL, zipPath) then
  begin
    MsgBox('Failed to download PHP.', mbError, MB_OK);
    Abort;
  end;

  WizardForm.StatusLabel.Caption := 'Extracting PHP {#PHPVersion}...';
  WizardForm.Update;

  if not ExtractZip(zipPath, phpDir) then
  begin
    MsgBox('Failed to extract PHP.', mbError, MB_OK);
    Abort;
  end;

  iniProd := phpDir + '\php.ini-production';
  iniFile := phpDir + '\php.ini';

  if FileExists(iniProd) then
  begin

    WizardForm.StatusLabel.Caption := 'Configuring PHP...';
    WizardForm.Update;

    CopyFile(iniProd, iniFile, False);

    LoadStringFromFile(iniFile, contentAnsi);
    content := String(contentAnsi);

    // Use larger filesize limits
    StringChangeEx(content, 'upload_max_filesize = 2M', 'upload_max_filesize = 100M', True);
    StringChangeEx(content, 'memory_limit = 128M', 'memory_limit = 512M', True);
    StringChangeEx(content, 'post_max_size = 8M', 'post_max_size = 100M', True);

    // Uncomment necessary extensions
    StringChangeEx(content, ';extension=mysqli', 'extension=mysqli', True);
    StringChangeEx(content, ';extension=openssl', 'extension=openssl', True);
    StringChangeEx(content, ';extension=pdo_pgsql', 'extension=pdo_pgsql', True);
    StringChangeEx(content, ';extension=pdo_sqlite', 'extension=pdo_sqlite', True);
    StringChangeEx(content, ';extension=pgsql', 'extension=pgsql', True);
    StringChangeEx(content, ';extension=sqlite3', 'extension=sqlite3', True);
    StringChangeEx(content, ';extension_dir = "ext"', 'extension_dir = "ext"', True);

    SaveStringToFile(iniFile, content, False);

  end;
end;


procedure SetupAdminer();
var
  base, plugins: String;
begin
  base := ExpandConstant('{app}');
  plugins := base + '\adminer-plugins';

  ForceDirectories(plugins);

  WizardForm.StatusLabel.Caption := 'Downloading Adminer {#AdminerVersion}...';
  WizardForm.Update;

  if not DownloadFile(ADMINER_URL,
    base + '\adminer-{#AdminerVersion}.php') then
  begin
    MsgBox('Failed to download Adminer.', mbError, MB_OK);
    Abort;
  end;

  WizardForm.StatusLabel.Caption := 'Downloading Adminer style...';
  WizardForm.Update;

  DownloadFile('https://raw.githubusercontent.com/guisaldanha/adminer-obsidian-amber/main/adminer.css', base + '\adminer.css');

  WizardForm.StatusLabel.Caption := 'Downloading Adminer plugins...';
  WizardForm.Update;

  DownloadFile('https://raw.githubusercontent.com/dg/adminer/refs/heads/master/adminer-plugins/login-without-credentials.php', plugins + '\login-without-credentials.php');
  DownloadFile('https://raw.githubusercontent.com/guisaldanha/sql-ollama/refs/heads/main/sql-ollama.php', plugins + '\sql-ollama.php');
  DownloadFile('https://www.adminer.org/download/v{#AdminerVersion}/plugins/row-numbers.php', plugins + '\row-numbers.php');
  DownloadFile('https://www.adminer.org/download/v{#AdminerVersion}/plugins/edit-foreign.php', plugins + '\edit-foreign.php');

  WizardForm.StatusLabel.Caption := 'Installation complete!';
  WizardForm.Update;
end;


procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    GenerateInstallKey();
    SetupPHP();
    SetupAdminer();
  end;
end;