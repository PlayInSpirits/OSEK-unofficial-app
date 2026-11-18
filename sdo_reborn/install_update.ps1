$ErrorActionPreference = 'Stop'
$exe = [IO.Path]::GetFullPath($env:OSEK_UPDATE_EXE)
$install = [IO.Path]::GetDirectoryName($exe)
$stage = [IO.Path]::GetFullPath($env:OSEK_UPDATE_STAGE)
if ([IO.Path]::GetDirectoryName($stage) -ne $install -or
    ![IO.Path]::GetFileName($stage).StartsWith('.osek-update-') -or
    ![IO.Directory]::Exists($stage)) { exit 1 }

$source = Join-Path $stage 'ОСЭК-Remastered'
$newExe = Join-Path $source 'ОСЭК-Remastered.exe'
$newInternal = Join-Path $source '_internal'
$internal = Join-Path $install '_internal'
$backupExe = Join-Path $stage 'previous.exe'
$backupInternal = Join-Path $stage 'previous-internal'
$ready = Join-Path $stage 'ready'
$oldPid = [int]$env:OSEK_UPDATE_PID
$launched = $null
$parentExited = $false
$cleanup = $false
try {
    $parent = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
    if ($parent -and !$parent.WaitForExit(45000)) { throw 'The application did not exit.' }
    $parentExited = $true
    $other = Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($exe)) -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $exe }
    if ($other) { throw 'Another instance is running from this directory.' }

    [IO.File]::Move($exe, $backupExe)
    [IO.Directory]::Move($internal, $backupInternal)
    [IO.Directory]::Move($newInternal, $internal)
    [IO.File]::Move($newExe, $exe)

    $env:OSEK_UPDATE_READY = $ready
    $launched = Start-Process -FilePath $exe -WorkingDirectory $install -WindowStyle Hidden -PassThru
    Remove-Item Env:OSEK_UPDATE_READY
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while (![IO.File]::Exists($ready)) {
        if ($launched.HasExited -or [DateTime]::UtcNow -ge $deadline) { throw 'The updated interface did not start.' }
        Start-Sleep -Milliseconds 300
    }
    $cleanup = $true
} catch {
    $failure = $_.Exception.Message
    try {
        if ($launched -and !$launched.HasExited) {
            $launched.Kill()
            if (!$launched.WaitForExit(10000)) { throw 'The updated process could not be stopped.' }
        }
        if ([IO.File]::Exists($backupExe)) {
            if ([IO.File]::Exists($exe)) { [IO.File]::Move($exe, $newExe) }
            if ([IO.Directory]::Exists($backupInternal)) {
                if ([IO.Directory]::Exists($internal)) { [IO.Directory]::Move($internal, $newInternal) }
                [IO.Directory]::Move($backupInternal, $internal)
            }
            [IO.File]::Move($backupExe, $exe)
        }
        [IO.File]::WriteAllText($env:OSEK_UPDATE_ERROR, 'Обновление не завершилось. Прежняя версия сохранена.')
        if ($parentExited -and !(Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($exe)) -ErrorAction SilentlyContinue |
                       Where-Object { $_.Path -eq $exe })) {
            Start-Process -FilePath $exe -WorkingDirectory $install -WindowStyle Hidden
        }
        $cleanup = $true
    } catch {
        [IO.File]::WriteAllText($env:OSEK_UPDATE_ERROR, "Не удалось восстановить приложение. Резервная копия находится в $stage")
    }
    [IO.File]::AppendAllText($env:OSEK_UPDATE_ERROR + '.log', $failure + [Environment]::NewLine)
} finally {
    if ($cleanup -and [IO.Directory]::GetParent($stage).FullName -eq $install) {
        try { [IO.Directory]::Delete($stage, $true) } catch {}
    }
}

