# One command to bring the home server fully up to date, right now:
#   1. git pull: the newest deployment files (compose.yaml, these scripts, the guide)
#   2. waits until CI has published the app image for that exact commit (a few minutes after a push)
#   3. pulls it and restarts the app (update.ps1), then checks the app answers
# Run from this folder:   powershell -ExecutionPolicy Bypass -File .\update-now.ps1
# (The scheduled task keeps doing step 3 alone every 15 minutes; this is for when you don't want to wait.)
param([int]$WaitMinutes = 20)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$image = "ghcr.io/samguercho/job-search-tracker"

function Fail($message) { Write-Host $message -ForegroundColor Red; exit 1 }

# Runs a program silently and says whether it succeeded. Windows PowerShell turns any text a program writes on
# its error output into a fatal error when output is redirected and ErrorActionPreference is "Stop"; here that
# text is expected (e.g. "manifest unknown" while CI is still building), so it is ignored in this function only.
function Test-Quiet([scriptblock]$command) {
    $ErrorActionPreference = "Continue"
    try { & $command *> $null } catch { return $false }
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Fail "git is not installed: winget install -e --id Git.Git, then open a new PowerShell (see the guide, step 4)."
}
if (-not (Test-Quiet { docker info })) { Fail "Docker is not running: start Docker Desktop, wait until it is ready, then run this again." }

Write-Host "1/3  Getting the latest code (git pull)..."
git -C $repo pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Fail "git pull failed. Local changes in $repo ? See: git -C $repo status   (to drop them: git -C $repo checkout -- .)"
}
$sha = (git -C $repo rev-parse HEAD).Trim().Substring(0, 7)
Write-Host "     Code is at commit $sha."

$pinned = Select-String -Path .env -Pattern '^\s*IMAGE_TAG\s*=\s*(\S+)' -ErrorAction SilentlyContinue
if ($pinned -and $pinned.Matches[0].Groups[1].Value -ne "latest") {
    Write-Host "     Note: IMAGE_TAG in .env pins the app to $($pinned.Matches[0].Groups[1].Value); remove it to follow updates." -ForegroundColor Yellow
}

Write-Host "2/3  Waiting for the app image of commit $sha (CI publishes it a few minutes after a push)..."
$deadline = (Get-Date).AddMinutes($WaitMinutes)
while ($true) {
    if (Test-Quiet { docker manifest inspect "${image}:sha-$sha" }) { Write-Host "     Image ready."; break }
    if ((Get-Date) -gt $deadline) {
        Write-Host "     No image for $sha after $WaitMinutes minutes: did CI fail? See GitHub > Actions. Using the newest published image." -ForegroundColor Yellow
        break
    }
    Start-Sleep -Seconds 30
}

Write-Host "3/3  Installing it and restarting the app..."
& (Join-Path $PSScriptRoot "update.ps1")
if ($LASTEXITCODE -ne 0) { Fail "The update failed: see update.log in this folder." }

$port = 8000
$portLine = Select-String -Path .env -Pattern '^\s*PORT\s*=\s*(\d+)' -ErrorAction SilentlyContinue
if ($portLine) { $port = [int]$portLine.Matches[0].Groups[1].Value }
$health = $null
foreach ($i in 1..20) {
    try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 5; break }
    catch { Start-Sleep -Seconds 3 }
}
if ((Test-Path (Join-Path $PSScriptRoot "bad-image.txt")) -and $health.status -eq "ok") {
    Write-Host "The newest version failed its health check, so the app runs the previous one. See update.log and failed-update.log." -ForegroundColor Yellow
} elseif ($health.status -eq "ok") { Write-Host "Done: the app is up to date and running." -ForegroundColor Green }
elseif ($health.status -eq "error") { Fail "The app runs but can't read some data files: docker compose logs --tail 50" }
else { Fail "The app does not answer yet: docker compose logs --tail 50" }
