# Copies your data (master CV, applications, chats) to a dated zip. Point -Destination at a synced folder
# (OneDrive, iCloud for Windows, Google Drive) for off-site backups. Keeps the 30 most recent zips.
param([string]$Destination = "$env:USERPROFILE\OneDrive\JobSearch-backups")
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$dataDir = (Get-Content .env | Where-Object { $_ -match '^HOST_DATA_DIR=' }) -replace '^HOST_DATA_DIR=', ''
if (-not $dataDir) { $dataDir = ".\data" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$zip = Join-Path $Destination ("jobsearch-data-{0}.zip" -f (Get-Date -Format "yyyy-MM-dd_HHmm"))
Compress-Archive -Path (Join-Path $dataDir "*") -DestinationPath $zip -Force
Get-ChildItem $Destination -Filter "jobsearch-data-*.zip" | Sort-Object Name -Descending | Select-Object -Skip 30 | Remove-Item
Write-Host "Backup written: $zip"
