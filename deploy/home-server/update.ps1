# Pulls the latest image published by CI and restarts the app if it changed.
# Run by hand, or every 15 minutes by the scheduled task (install-update-task.ps1).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "update.log"
function Log($message) { "$(Get-Date -Format s)  $message" | Tee-Object -FilePath $log -Append }

# Runs a program, hiding its output; returns its output text, or $null if it failed. Windows PowerShell turns
# any text a program writes on its error output into a fatal error under "Stop" when output is redirected;
# that text is expected here (e.g. Docker not started yet), so it is ignored in this function only.
function Invoke-Quiet([scriptblock]$command) {
    $ErrorActionPreference = "Continue"
    try { $out = & $command 2> $null } catch { return $null }
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($out | Out-String)
}

try {
    if ($null -eq (Invoke-Quiet { docker info })) {
        # Watchdog: Docker Desktop was closed or hasn't started (e.g. after a reboot): start it, the app follows
        $desktop = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
        if ((Test-Path $desktop) -and -not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
            Start-Process $desktop
            Log "Docker was not running; started Docker Desktop."
        } else { Log "Docker is not running yet; skipping." }
        exit 0
    }
    $before = Invoke-Quiet { docker compose images app --format json }
    docker compose pull app --quiet
    docker compose up -d --remove-orphans
    $after = Invoke-Quiet { docker compose images app --format json }
    if ($before -ne $after) { Log "Updated to the newest image." ; docker image prune -f *> $null }
    # keep the log short
    if ((Get-Item $log -ErrorAction SilentlyContinue).Length -gt 1MB) { Get-Content $log -Tail 500 | Set-Content $log }
} catch {
    Log "Update failed: $_"
    exit 1
}
