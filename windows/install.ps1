$ErrorActionPreference = 'Stop'
$RepoRoot = 'https://raw.githubusercontent.com/banchikovartemy-netizen/camrec-test/main'
$RepoRaw = "$RepoRoot/windows"
$AppDir = Join-Path $env:LOCALAPPDATA 'Programs\CamRec'
$DataDir = Join-Path $env:LOCALAPPDATA 'CamRec'
$ScriptPath = Join-Path $AppDir 'camrec_windows.py'

Write-Host '=== CamRec Windows 10/11 ===' -ForegroundColor Cyan

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Ensure-WingetPackage($CommandName, $PackageId) {
    if (Get-Command $CommandName -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$CommandName не найден, а winget недоступен. Установи $CommandName и запусти установщик снова."
    }
    Write-Host "Устанавливаю $PackageId ..."
    winget install --id $PackageId -e --scope user --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
}

Ensure-WingetPackage 'python.exe' 'Python.Python.3.12'
Ensure-WingetPackage 'ffmpeg.exe' 'Gyan.FFmpeg'

$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    $Python = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Python) { throw 'Python не найден после установки.' }

$PythonW = Join-Path (Split-Path $Python) 'pythonw.exe'
if (-not (Test-Path $PythonW)) { $PythonW = $Python }

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Invoke-WebRequest "$RepoRaw/camrec_windows.py" -OutFile $ScriptPath -UseBasicParsing
Invoke-WebRequest "$RepoRaw/camrec_windows_actions.py" -OutFile (Join-Path $AppDir 'camrec_windows_actions.py') -UseBasicParsing
Invoke-WebRequest "$RepoRoot/camrec_preview.py" -OutFile (Join-Path $AppDir 'camrec_preview.py') -UseBasicParsing

$CmdPath = Join-Path $AppDir 'CamRec.cmd'
@"
@echo off
start "" "$PythonW" "$ScriptPath"
"@ | Set-Content -Encoding ASCII $CmdPath

$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$ShortcutPath = Join-Path $StartMenu 'CamRec.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PythonW
$Shortcut.Arguments = '"' + $ScriptPath + '"'
$Shortcut.WorkingDirectory = $AppDir
$Shortcut.Description = 'CamRec — запись с веб-камеры и IP/RTSP-камеры'
$Shortcut.Save()

Write-Host ''
Write-Host 'Готово.' -ForegroundColor Green
Write-Host 'CamRec добавлен в меню Пуск.'
Write-Host "Данные и записи: $DataDir"
Write-Host ''
$answer = Read-Host 'Запустить CamRec сейчас? [Y/n]'
if ($answer -notmatch '^[Nn]$') {
    Start-Process -FilePath $PythonW -ArgumentList ('"' + $ScriptPath + '"') -WorkingDirectory $AppDir
}
