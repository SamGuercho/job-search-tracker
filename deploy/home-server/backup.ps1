# Backs up everything Ezri keeps on this PC, to a synced folder (OneDrive by default) for off-site copies:
#   - data (master CV, applications, chats, practice, friends' spaces): a dated zip; the 30 most recent are kept
#   - the assistant's telemetry: the app's weekly compressed copies (data\telemetry-backups, the last 3), mirrored
#     to <Destination>\telemetry, so OneDrive holds the same 3 files, never more
#   - the companies folder (generated CVs, job descriptions, logos, interview recordings): copied file by file
#     into <Destination>\companies. Only new or changed files are copied, and nothing is ever deleted there,
#     so a file deleted by mistake in the app is still in the backup.
# Writes data\last_backup.json, so the app shows when the last backup ran (Today page, /health). History: backup.log.
#
# Once, to back up every day at 3am (or at the next start if the PC was off then), and run a first backup now:
#   powershell -ExecutionPolicy Bypass -File .\backup.ps1 -Install
#   powershell -ExecutionPolicy Bypass -File .\backup.ps1 -Install -Destination "D:\Backups\JobSearch"
# By hand: the same without -Install.
param([string]$Destination = "$env:USERPROFILE\OneDrive\JobSearch-backups", [switch]$Install)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "backup.log"
function Log($message) { "$(Get-Date -Format s)  $message" | Tee-Object -FilePath $log -Append }

function Setting($name, $default) {
    $line = Select-String -Path .env -Pattern "^\s*$name\s*=\s*(\S.*?)\s*$" -ErrorAction SilentlyContinue
    if ($line) { return $line.Matches[0].Groups[1].Value } else { return $default }
}

if ($Install) {
    $script = Join-Path $PSScriptRoot "backup.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Destination `"$Destination`""
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName "JobSearchTracker-Backup" -Action $action -Trigger (New-ScheduledTaskTrigger -Daily -At 3am) `
        -Settings $settings -Description "Backs up Ezri's data and files every day" -Force | Out-Null
    Write-Host "Scheduled task 'JobSearchTracker-Backup' installed: every day at 3am, to $Destination."
    Write-Host "Running a first backup now..."
}

$dataDir = Setting "HOST_DATA_DIR" ".\data"
$outputDir = Setting "HOST_OUTPUT_DIR" ".\companies"
$marker = Join-Path $dataDir "last_backup.json"

function Write-Marker([bool]$ok, [string]$err) {
    try {
        @{ at = (Get-Date -Format s); ok = $ok; error = $err; destination = $Destination } |
            ConvertTo-Json | Set-Content -Path $marker -Encoding UTF8
    } catch { Log "Could not write $marker : $_" }
}

# robocopy: copies what changed, retries a file the app is writing; exit codes 0-7 mean success
function Copy-Folder($from, $to, [string[]]$skip = @(), [switch]$Mirror) {
    $how = if ($Mirror) { "/MIR" } else { "/E" }
    $exclude = if ($skip) { @("/XD") + $skip } else { @() }
    robocopy $from $to $how /R:3 /W:2 /XF *.tmp @exclude /NP /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying $from (code $LASTEXITCODE)" }
}

$stage = Join-Path $env:TEMP ("jobsearch-backup-" + [guid]::NewGuid().ToString("N"))
try {
    if (-not (Test-Path $dataDir)) { throw "Data folder not found: $dataDir (HOST_DATA_DIR in .env)" }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null

    # 1. data: copied first (a consistent snapshot, even while the app saves), then zipped
    $telemetry = Join-Path $dataDir "telemetry-backups"
    Copy-Folder $dataDir $stage -skip $telemetry   # telemetry has its own copies (step 3): not in every zip
    $zip = Join-Path $Destination ("jobsearch-data-{0}.zip" -f (Get-Date -Format "yyyy-MM-dd_HHmm"))
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
    Get-ChildItem $Destination -Filter "jobsearch-data-*.zip" | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item

    # 2. companies: generated CVs, job descriptions, recordings (big files: copied once, then only changes)
    if (Test-Path $outputDir) { Copy-Folder $outputDir (Join-Path $Destination "companies") }
    else { Log "No companies folder at $outputDir (HOST_OUTPUT_DIR in .env): skipped." }

    # 3. telemetry: mirrored, so the 3 copies kept by the app replace the older ones here too
    if (Test-Path $telemetry) { Copy-Folder $telemetry (Join-Path $Destination "telemetry") -Mirror }

    $size = "{0:N1} MB" -f ((Get-Item $zip).Length / 1MB)
    Write-Marker $true ""
    Log "Backup done: $zip ($size), the companies folder and the telemetry copies, in $Destination"
} catch {
    Write-Marker $false "$_"
    Log "Backup FAILED: $_"
    exit 1
} finally {
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}
if ((Get-Item $log -ErrorAction SilentlyContinue).Length -gt 1MB) { Get-Content $log -Tail 500 | Set-Content $log }
