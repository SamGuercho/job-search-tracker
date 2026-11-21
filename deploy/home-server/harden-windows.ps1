# Keeps this PC serving the app 24/7: no sleep, no hibernation, no surprise Windows Update restarts,
# and shows what happened the last times it slept or restarted.
# Run once, in an ADMINISTRATOR PowerShell, from this folder:
#   powershell -ExecutionPolicy Bypass -File .\harden-windows.ps1
$ErrorActionPreference = "Stop"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "Run this in an administrator PowerShell (right-click PowerShell -> Run as administrator)." -ForegroundColor Red; exit 1 }

Write-Host "`n== Power: never sleep or hibernate ==" -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /hibernate off                                                     # also disables Fast Startup
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HYBRIDSLEEP 0 2>$null
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 2>$null    # laptop: closing the lid does nothing
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0 2>$null
powercfg /setactive SCHEME_CURRENT
Write-Host "Done. (The screen may still turn off; that's fine.)"

Write-Host "`n== Windows Update: no automatic restart while you're signed in ==" -ForegroundColor Cyan
$au = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU"
New-Item -Path $au -Force | Out-Null
Set-ItemProperty -Path $au -Name NoAutoRebootWithLoggedOnUsers -Type DWord -Value 1
$ux = "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings"
New-Item -Path $ux -Force | Out-Null
Set-ItemProperty -Path $ux -Name ActiveHoursStart -Type DWord -Value 6     # 06:00-24:00, the longest Windows allows
Set-ItemProperty -Path $ux -Name ActiveHoursEnd -Type DWord -Value 0
Write-Host "Done. Updates still install; restart the PC yourself when convenient (Settings -> Windows Update)."

Write-Host "`n== After a restart: does everything come back without you? ==" -ForegroundColor Cyan
$winlogon = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
if ($winlogon.AutoAdminLogon -eq "1") {
    Write-Host "Automatic sign-in: ON (Docker Desktop starts after a reboot)." -ForegroundColor Green
} else {
    Write-Host "Automatic sign-in: OFF -> after any reboot Docker Desktop waits for you to sign in, so the app stays down." -ForegroundColor Yellow
    Write-Host "Turn it on with Microsoft's Autologon tool (stores the password encrypted):"
    Write-Host "    winget install -e --id Microsoft.Sysinternals.Autologon"
    Write-Host "    then run 'Autologon', enter your Windows password, click Enable."
    Write-Host "  (Windows Hello/PIN-only accounts: Settings -> Accounts -> Sign-in options -> turn OFF"
    Write-Host "   'For improved security, only allow Windows Hello sign-in' first.)"
}
$tailscale = Get-Service Tailscale -ErrorAction SilentlyContinue
if ($tailscale) {
    Set-Service Tailscale -StartupType Automatic
    Write-Host "Tailscale service: $($tailscale.Status), starts at boot." -ForegroundColor Green
}

Write-Host "`n== What happened recently (sleep / restart / power loss) ==" -ForegroundColor Cyan
# 41 = crashed or lost power, 6008 = unexpected shutdown, 1074 = restart requested (and by what), 42 = went to sleep, 1 = woke up
Get-WinEvent -FilterHashtable @{ LogName = "System"; Id = 1, 41, 42, 1074, 6008 } -MaxEvents 12 -ErrorAction SilentlyContinue |
    Where-Object { $_.Id -ne 1 -or $_.ProviderName -eq "Microsoft-Windows-Power-Troubleshooter" } |
    ForEach-Object {
        $what = switch ($_.Id) { 41 {"CRASH / POWER LOST"} 6008 {"UNEXPECTED SHUTDOWN"} 1074 {"RESTART"} 42 {"SLEEP"} 1 {"WOKE UP"} }
        $msg = ($_.Message -split "`n")[0].Trim()
        "{0:yyyy-MM-dd HH:mm}  {1,-20} {2}" -f $_.TimeCreated, $what, $msg.Substring(0, [Math]::Min(110, $msg.Length))
    }
Write-Host "`nAll set."
