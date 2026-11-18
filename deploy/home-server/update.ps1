# Pulls the latest image published by CI and restarts the app if it changed.
# Run by hand, or every 15 minutes by the scheduled task (install-update-task.ps1).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "update.log"
function Log($message) { "$(Get-Date -Format s)  $message" | Tee-Object -FilePath $log -Append }

try {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Log "Docker is not running yet; skipping."; exit 0 }
    $before = docker compose images app --format json 2>$null
    docker compose pull app --quiet
    docker compose up -d --remove-orphans
    $after = docker compose images app --format json 2>$null
    if ($before -ne $after) { Log "Updated to the newest image." ; docker image prune -f *> $null }
    # keep the log short
    if ((Get-Item $log -ErrorAction SilentlyContinue).Length -gt 1MB) { Get-Content $log -Tail 500 | Set-Content $log }
} catch {
    Log "Update failed: $_"
    exit 1
}
