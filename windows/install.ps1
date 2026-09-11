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

function Find-RealPython {
    $candidates = @()
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName
    try {
        $cmd = (Get-Command python.exe -ErrorAction Stop).Source
        if ($cmd -and $cmd -notmatch '\\WindowsApps\\python\.exe$') { $candidates += $cmd }
    } catch {}
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        try {
            $ok = & $candidate -c "import sys, tkinter; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ok) { return $candidate }
        } catch {}
    }
    return $null
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

$Python = Find-RealPython
if (-not $Python) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Настоящий Python с Tkinter не найден, а winget недоступен.'
    }
    Write-Host 'Устанавливаю Python 3.12 ...'
    winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    $Python = Find-RealPython
}
if (-not $Python) { throw 'Python с Tkinter не найден после установки.' }

Ensure-WingetPackage 'ffmpeg.exe' 'Gyan.FFmpeg'

$PythonW = Join-Path (Split-Path $Python) 'pythonw.exe'
if (-not (Test-Path $PythonW)) { $PythonW = $Python }

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir 'archive') | Out-Null

Invoke-WebRequest "$RepoRaw/camrec_windows.py" -OutFile $ScriptPath -UseBasicParsing
Invoke-WebRequest "$RepoRaw/camrec_windows_actions.py" -OutFile (Join-Path $AppDir 'camrec_windows_actions.py') -UseBasicParsing
Invoke-WebRequest "$RepoRoot/camrec_preview.py" -OutFile (Join-Path $AppDir 'camrec_preview.py') -UseBasicParsing

# Проверяем, что программа хотя бы импортируется до создания ярлыков.
& $Python -m py_compile $ScriptPath (Join-Path $AppDir 'camrec_windows_actions.py') (Join-Path $AppDir 'camrec_preview.py')
if ($LASTEXITCODE -ne 0) { throw 'Ошибка проверки файлов CamRec.' }

$CmdPath = Join-Path $AppDir 'CamRec.cmd'
@"
@echo off
start "" "$PythonW" "$ScriptPath"
"@ | Set-Content -Encoding ASCII $CmdPath

$DebugPath = Join-Path $AppDir 'CamRec Debug.cmd'
@"
@echo off
chcp 65001 >nul
"$Python" "$ScriptPath"
echo.
echo Если выше есть ошибка, скопируй её и пришли мне.
pause
"@ | Set-Content -Encoding UTF8 $DebugPath

$StartMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$ShortcutPath = Join-Path $StartMenu 'CamRec.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PythonW
$Shortcut.Arguments = '"' + $ScriptPath + '"'
$Shortcut.WorkingDirectory = $AppDir
$Shortcut.Description = 'CamRec — запись с веб-камеры и IP/RTSP-камеры'
$Shortcut.Save()

$DebugShortcutPath = Join-Path $StartMenu 'CamRec Debug.lnk'
$DebugShortcut = $Shell.CreateShortcut($DebugShortcutPath)
$DebugShortcut.TargetPath = $DebugPath
$DebugShortcut.WorkingDirectory = $AppDir
$DebugShortcut.Description = 'CamRec — запуск с выводом ошибок'
$DebugShortcut.Save()

Write-Host ''
Write-Host 'Готово.' -ForegroundColor Green
Write-Host 'CamRec добавлен в меню Пуск.'
Write-Host "Архив записей: $(Join-Path $DataDir 'archive')"
Write-Host "Сохранённые вручную: $(Join-Path ([Environment]::GetFolderPath('MyVideos')) 'CamRec\Saved')"
Write-Host "Диагностика: $DebugPath"
Write-Host ''
$answer = Read-Host 'Запустить CamRec сейчас? [Y/n]'
if ($answer -notmatch '^[Nn]$') {
    Start-Process -FilePath $PythonW -ArgumentList ('"' + $ScriptPath + '"') -WorkingDirectory $AppDir
}
