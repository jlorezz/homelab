<#
.SYNOPSIS
  Sets up private meeting notes on a Windows device (desktop or laptop).

.DESCRIPTION
  Installs Meetily (pinned + hash-checked), Handy, Ollama and Python for the
  current user, keeps Ollama local-only, turns off Meetily audio saving, picks a
  summary model that fits the GPU, installs meeting-sink and schedules it every
  two minutes. Re-running is safe. Run -Firewall separately from an elevated
  PowerShell after Meetily and Handy have downloaded their speech models.

.EXAMPLE
  .\install-windows.ps1 -VaultPath 'G:\obsidian-notes\NAS' -Owner 'Your Name'
  .\install-windows.ps1 -EnableSummaries      # after the NAS exclusion is deployed
  .\install-windows.ps1 -Firewall             # elevated, after models are downloaded
#>
[CmdletBinding()]
param(
  [string]$VaultPath = 'G:\obsidian-notes\NAS',
  [string]$Owner = 'Me',
  [string]$Model = '',
  [switch]$EnableSummaries,
  [switch]$Firewall
)
$ErrorActionPreference = 'Stop'

$MeetilyVersion = '0.4.1'
$MeetilyUrl = "https://github.com/Zackriya-Solutions/meetily/releases/download/v$MeetilyVersion/meetily_${MeetilyVersion}_x64-setup.exe"
$MeetilySha256 = '6321068c98da8bf5392df3825aaa5be0e28349e54436f53d485c08c54b9c9315'
$SinkDir = Join-Path $env:LOCALAPPDATA 'meeting-sink'
$ConfigFile = Join-Path $SinkDir 'config.toml'
$MeetilyData = Join-Path $env:APPDATA 'com.meetily.ai'
$TaskName = 'meeting-sink'

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
# Windows PowerShell's -Encoding utf8 adds a BOM, which Meetily's JSON parser rejects
# (it would silently fall back to saving audio).
function Write-Utf8($path, $text) { [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding($false))) }

if ($Firewall) {
  $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run -Firewall from an elevated PowerShell.'
  }
  # Speech apps never need the network once models are downloaded.
  # Ollama keeps network access for `ollama pull`; OLLAMA_NO_CLOUD blocks cloud models.
  $apps = @{
    'Meetily' = @("$env:LOCALAPPDATA\meetily\meetily.exe", "$env:ProgramFiles\meetily\meetily.exe")
    'Handy'   = @("$env:LOCALAPPDATA\Handy\handy.exe", "$env:ProgramFiles\Handy\handy.exe")
  }
  foreach ($name in $apps.Keys) {
    $exe = $apps[$name] | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) {
      $exe = Get-ChildItem "$env:LOCALAPPDATA", "$env:ProgramFiles" -Filter "$($name.ToLower()).exe" -Recurse -ErrorAction SilentlyContinue -Depth 3 |
        Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $exe) { Write-Warning "$name executable not found; skipped"; continue }
    $rule = "Block $name outbound (meeting notes)"
    Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -Program $exe -Action Block -Profile Any | Out-Null
    Step "Blocked outbound network for $exe"
  }
  Write-Host 'To update Meetily or Handy later, disable these rules temporarily.'
  return
}

Step 'Installing Python, Ollama and Handy (user scope)'
foreach ($id in 'Python.Python.3.13', 'Ollama.Ollama', 'cjpais.Handy') {
  winget list --id $id --exact --accept-source-agreements | Out-Null
  if ($LASTEXITCODE -ne 0) {
    winget install --id $id --exact --silent --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
      # Some packages only offer a machine-wide installer.
      winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    }
  }
}

# Pick up PATH entries added by the installers above.
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')

Step 'Keeping Ollama local-only'
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD', '1', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')
$env:OLLAMA_NO_CLOUD = '1'; $env:OLLAMA_HOST = '127.0.0.1:11434'
Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | Stop-Process -Force
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if (-not (Test-Path $ollama)) { throw 'ollama.exe not found' }
Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
Start-Sleep -Seconds 3

Step 'Turning off Meetily audio saving before first start'
New-Item -ItemType Directory -Force -Path $MeetilyData | Out-Null
$prefsFile = Join-Path $MeetilyData 'recording_preferences.json'
$prefs = if (Test-Path $prefsFile) { Get-Content $prefsFile -Raw | ConvertFrom-Json } else { $null }
if (-not $prefs -or -not $prefs.preferences) {
  $prefs = [pscustomobject]@{ preferences = [pscustomobject]@{
    save_folder = (Join-Path ([Environment]::GetFolderPath('MyMusic')) 'meetily-recordings')
    auto_save = $false; file_format = 'mp4'
    preferred_mic_device = $null; preferred_system_device = $null } }
} else { $prefs.preferences.auto_save = $false }
Write-Utf8 $prefsFile ($prefs | ConvertTo-Json -Depth 5)

Step "Installing Meetily $MeetilyVersion (hash-checked)"
$installed = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
  Where-Object { $_.DisplayName -like 'meetily*' }
if (-not $installed) {
  $setup = Join-Path $env:TEMP "meetily_${MeetilyVersion}_x64-setup.exe"
  Invoke-WebRequest -Uri $MeetilyUrl -OutFile $setup -UseBasicParsing
  $hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
  if ($hash -ne $MeetilySha256) { Remove-Item $setup; throw "Meetily installer hash mismatch: $hash" }
  Start-Process -FilePath $setup -ArgumentList '/S' -Wait
  Remove-Item $setup
}

Step 'Choosing a summary model for this GPU'
if (-not $Model) {
  $vramMb = 0
  $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
  if ($smi) { $vramMb = [int]((& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | Select-Object -First 1).Trim()) }
  $Model = if ($vramMb -ge 12000) { 'qwen3:14b' } elseif ($vramMb -ge 7000) { 'qwen3:8b' } else { 'qwen3:4b' }
  Write-Host "    GPU memory ${vramMb} MB -> $Model"
}
& $ollama pull $Model
if ($LASTEXITCODE -ne 0) { throw "ollama pull $Model failed" }

Step 'Installing meeting-sink'
New-Item -ItemType Directory -Force -Path $SinkDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'meeting_sink.py') $SinkDir -Force
if (-not (Test-Path $ConfigFile)) {
  $config = Get-Content (Join-Path $PSScriptRoot 'config.example.toml') -Raw
  $config = $config -replace "(?m)^vault_path = .*$", "vault_path = '$VaultPath'"
  $config = $config -replace '(?m)^model = .*$', "model = `"$Model`""
  $config = $config -replace '(?m)^owner = .*$', "owner = `"$Owner`""
  Write-Utf8 $ConfigFile $config
}
if ($EnableSummaries) {
  Write-Utf8 $ConfigFile ((Get-Content $ConfigFile -Raw) -replace '(?m)^nas_exclusion_confirmed = .*$', 'nas_exclusion_confirmed = true')
  Step 'Summaries enabled'
}
# Only this user may read the sink's state, config and log.
icacls $SinkDir /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" | Out-Null

Step 'Scheduling meeting-sink every 2 minutes'
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = "$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe" }
$python = (& $py -3.13 -c 'import sys; print(sys.executable)').Trim()
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$SinkDir\meeting_sink.py`"" -WorkingDirectory $SinkDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
  -Description 'Private meeting notes: summarise Meetily meetings into Obsidian and erase transcripts' -Force | Out-Null

Write-Host ''
Write-Host 'Done. Finish in the apps (one time):' -ForegroundColor Green
Write-Host '  Meetily: Settings -> turn analytics OFF; transcription = local Parakeet (or Whisper large-v3-turbo for German);'
Write-Host '           leave summary provider unused (meeting-sink summarises); confirm audio saving is OFF.'
Write-Host '  Handy:   pick a local model and a hotkey; start with Windows.'
Write-Host '  Then:    run this script with -Firewall from an elevated PowerShell.'
if (-not $EnableSummaries) {
  Write-Host '  Notes stay OFF until the NAS Private/ exclusion is deployed; then re-run with -EnableSummaries.' -ForegroundColor Yellow
}
Write-Host "Log: $SinkDir\sink.log"
