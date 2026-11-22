# Pulls the latest image published by CI and restarts the app if it changed.
# Run by hand, or every 15 minutes by the scheduled task (install-update-task.ps1).
#
# Safety net: after switching to a new version, the app must answer /health with "ok" (it also checks it can read
# your data). If it doesn't, the previous version is put back, and the failed one is remembered in bad-image.txt
# and skipped until CI publishes another. If the previous version fails too, the problem isn't the new version
# (the data, the PC…): the new one is kept and update.log says so.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "update.log"
$badFile = Join-Path $PSScriptRoot "bad-image.txt"
$image = "ghcr.io/samguercho/job-search-tracker"
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

function Setting($name, $default) {
    $line = Select-String -Path .env -Pattern "^\s*$name\s*=\s*(\S+)" -ErrorAction SilentlyContinue
    if ($line) { return $line.Matches[0].Groups[1].Value } else { return $default }
}
$tag = Setting "IMAGE_TAG" "latest"
$port = Setting "PORT" "8000"

function ImageId($ref) {
    $id = Invoke-Quiet { docker image inspect --format "{{.Id}}" $ref }
    if ($id) { return $id.Trim() } else { return "" }
}
function RunningImage {
    $container = Invoke-Quiet { docker compose ps -q app }
    if (-not $container) { return "" }
    $c = ($container.Trim() -split "\s+")[0]
    $id = Invoke-Quiet { docker inspect --format "{{.Image}}" $c }
    if ($id) { return $id.Trim() } else { return "" }
}
# Waits for the app (up to ~2 minutes): true only if it answers with status "ok"
function Healthy {
    foreach ($i in 1..40) {
        try { return ((Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 5).status -eq "ok") }
        catch { Start-Sleep -Seconds 3 }
    }
    return $false
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
    $current = RunningImage
    docker compose pull app --quiet
    $latest = ImageId "${image}:${tag}"
    if (-not $latest) { Log "No app image ${image}:${tag} found after the pull (offline? IMAGE_TAG in .env?)."; exit 1 }
    $bad = if (Test-Path $badFile) { (Get-Content $badFile -Raw).Trim() } else { "" }

    if ($latest -and $latest -eq $bad) {
        # This version already failed its check: stay on the previous one until CI publishes a new version
        if (-not $current -and (ImageId "${image}:previous")) { $env:IMAGE_TAG = "previous"; docker compose up -d --remove-orphans }
        Write-Host "The newest version failed its health check earlier (see update.log): staying on the previous one."
        exit 0
    }
    if ($latest -eq $current) {
        docker compose up -d --remove-orphans   # nothing new; starts the app if it was stopped
        exit 0
    }

    if ($current) { docker tag $current "${image}:previous" }   # kept, to go back to
    docker compose up -d --remove-orphans
    if (Healthy) {
        Log "Updated to the newest image."
        Remove-Item $badFile -ErrorAction SilentlyContinue
        docker image prune -f *> $null
    } elseif (-not $current) {
        Log "The app does not pass its health check, and there is no previous version to go back to. See: docker compose logs --tail 50"
        exit 1
    } else {
        Log "The new version failed its health check: going back to the previous one (its logs: failed-update.log)."
        Invoke-Quiet { docker compose logs app --tail 300 } | Set-Content -Path (Join-Path $PSScriptRoot "failed-update.log")
        $env:IMAGE_TAG = "previous"
        docker compose up -d --remove-orphans
        if (Healthy) {
            Set-Content -Path $badFile -Value $latest
            Log "Back on the previous version. The failed one is skipped until CI publishes a new version."
        } else {
            # The previous version fails too: the cause is elsewhere (data, disk, PC). Keep the new version.
            Remove-Item Env:IMAGE_TAG
            docker compose up -d --remove-orphans
            Log "The previous version fails too, so the new version was kept. See: docker compose logs --tail 50 and curl.exe http://127.0.0.1:$port/health"
        }
        exit 1
    }
    # keep the log short
    if ((Get-Item $log -ErrorAction SilentlyContinue).Length -gt 1MB) { Get-Content $log -Tail 500 | Set-Content $log }
} catch {
    Log "Update failed: $_"
    exit 1
}
