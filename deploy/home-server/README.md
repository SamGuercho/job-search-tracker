# Home server (Windows, always on)

Run the Job Search Tracker on a computer that stays on at home, and use it from your phone, your laptop and
any device signed into your Tailscale account — over **https**, from anywhere, without opening your router.

```
Phone ───┐                                   Windows PC (always on)
Laptop ──┼── Tailscale private network ──→   Docker: ghcr.io/samguercho/job-search-tracker
         │   https://<pc>.<tailnet>.ts.net   C:\JobSearch\data       (your CV, applications, chats)
         │                                   C:\JobSearch\companies  (generated CVs, job descriptions)
GitHub ── CI publishes a new image ──→ the PC installs it within 15 minutes (scheduled task)
```

Nothing is built on the PC: it runs the image that CI tests and publishes on every push to `main`.
Allow **30 minutes**, mostly downloads. Run the commands in **PowerShell** (Start menu → "PowerShell").
If a `winget install` answers "No package found", refresh its catalogue first: `winget source update`.

---

## 1. Prepare the PC (5 min)

- **Windows 10 22H2 or Windows 11.** Check: `winver`.
- **Never sleep while plugged in** (the screen can still turn off):
  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  ```
- **Optional, recommended:** in the BIOS, set *Restore on AC power loss* to *Power On*, so the PC restarts after
  a power cut; and enable automatic sign-in for your Windows user (Docker Desktop starts when you're signed in).

## 2. Install Docker Desktop (10 min)

```powershell
winget install -e --id Docker.DockerDesktop
```
Restart when asked, open **Docker Desktop**, accept the terms, keep the recommended **WSL 2** setting. Then in
Docker Desktop → **Settings → General**, tick **Start Docker Desktop when you sign in**.

If Docker says **WSL needs the "Virtual Machine Platform"**, enable it from an **administrator** PowerShell
(Start → type PowerShell → right-click → *Run as administrator*; a normal window answers "requires elevation"):
```powershell
wsl --install --no-distribution
```
(older Windows 10: `dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart` and the same
with `/featurename:Microsoft-Windows-Subsystem-Linux`), **restart the PC**, then run `wsl --update` and reopen Docker Desktop.

If it complains about virtualisation, enable *Intel VT-x* / *AMD-V (SVM)* in the BIOS (check first: Task Manager →
Performance → CPU → "Virtualization: Enabled"). Easiest way into the BIOS: Settings → System → Recovery →
Advanced startup → *Restart now* → Troubleshoot → Advanced options → *UEFI Firmware Settings*.

Any later command answering "requires elevation" (e.g. `powercfg`, the scheduled tasks): run it in an
administrator PowerShell.

✅ Check: `docker run --rm hello-world` prints "Hello from Docker!".

## 3. Install Tailscale (3 min)

Download and run the installer from **https://tailscale.com/download/windows**
(or with winget: `winget search tailscale`, then `winget install -e --id <Id shown>`; package ids change over time).
Open Tailscale from the system tray (bottom right, maybe under **^**) and **sign in with the same account as on
your phone and Mac**. Reopen PowerShell afterwards so it finds the `tailscale` command.

✅ Check: `tailscale status` lists this PC (and your other devices).

On your Tailscale account, **HTTPS certificates must be enabled** (once): https://login.tailscale.com/admin/dns →
*HTTPS Certificates* → *Enable HTTPS*.

## 4. Get the deployment files (2 min)

Only this folder is needed. Either:
```powershell
winget install -e --id Git.Git        # then open a new PowerShell
git clone https://github.com/SamGuercho/job-search-tracker.git C:\JobSearch\app
cd C:\JobSearch\app\deploy\home-server
```
or download the repository as a ZIP from GitHub (*Code → Download ZIP*), extract it to `C:\JobSearch\app`, and
`cd C:\JobSearch\app\deploy\home-server`.

## 5. Settings (3 min)

```powershell
New-Item -ItemType Directory -Force C:\JobSearch\data, C:\JobSearch\companies | Out-Null
Copy-Item .env.example .env
notepad .env
```
Set at least **`JOBTRACKER_PASSWORD`** (required: you'll type it once per device) and, for the AI features,
**`OPENAI_API_KEY`**. Keep `HOST_DATA_DIR=C:/JobSearch/data` and `HOST_OUTPUT_DIR=C:/JobSearch/companies` unless
you want other folders. Save and close.

## 6. Bring your data (5 min)

Copy your existing data files — `master_cv.json`, `companies.json`, `chats.json` (and the `backups` folder if
you want it) — into `C:\JobSearch\data`. Ways to get them from a Mac:
- **Taildrop** (built into Tailscale), on the Mac, **from the folder that holds them** (find the PC's name with
  `tailscale status`):
  ```bash
  cd "<your data folder>"   # e.g. ~/Library/Mobile Documents/com~apple~CloudDocs/Documents/Job Search/data
  tailscale file cp master_cv.json companies.json chats.json <pc-name>:
  ```
  On the PC they arrive in *Downloads*; move them into place:
  ```powershell
  Move-Item "$env:USERPROFILE\Downloads\master_cv.json","$env:USERPROFILE\Downloads\companies.json","$env:USERPROFILE\Downloads\chats.json" C:\JobSearch\data -Force
  ```
- or **icloud.com → iCloud Drive → Documents/Job Search/data**, download the files on the PC.

New user? Skip this step: the app starts with example data you replace from the *Master CV* page.

## 7. Start the app (3 min, first download)

```powershell
docker compose up -d
```
✅ Check: `curl.exe http://127.0.0.1:8000/health` returns `"status":"ok"` and `"pdf_engine":"libreoffice"`.
Open http://127.0.0.1:8000 on the PC, sign in with your password: your CV and applications are there.

The container restarts by itself after a crash or a reboot (`restart: unless-stopped`).

## 8. Publish it to your devices over https (1 min)

```powershell
tailscale serve --bg 8000
tailscale serve status
```
The address shown (`https://<pc-name>.<tailnet>.ts.net`) works on **all your Tailscale devices** and nowhere else
(not on the internet). `tailscale serve` is remembered across reboots. Stop it with `tailscale serve --https=443 off`.

✅ Check: on your phone (Tailscale app on), open the address, sign in.
**Install it as an app**: Chrome menu ⋮ → *Install app* (Android) · Safari → Share → *Add to Home Screen* (iPhone).

## 9. Automatic updates (1 min)

```powershell
powershell -ExecutionPolicy Bypass -File .\install-update-task.ps1
```
Every 15 minutes (and at sign-in), `update.ps1` pulls the newest image published by CI and restarts the app if it
changed. Push to `main` → CI tests and publishes → the server updates itself. History: `update.log`.
Update by hand any time with `.\update.ps1`. To stay on a version, set `IMAGE_TAG` in `.env` to a commit tag
(e.g. `sha-ade50a6`, see the repository's *Packages*).

## 10. Backups (recommended)

Your data lives only on this PC. Copy it regularly to a synced folder:
```powershell
.\backup.ps1                                   # → OneDrive\JobSearch-backups (default)
.\backup.ps1 -Destination "D:\Backups\JobSearch"
```
Automate it like the update task, e.g. daily:
```powershell
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\backup.ps1`""
Register-ScheduledTask -TaskName JobSearchTracker-Backup -Action $a -Trigger (New-ScheduledTaskTrigger -Daily -At 3am) -Force
```

## 11. Share with friends (optional)

Friends use **your** server with **their own** data: their own master CV, applications, chats and CVs. No
password and no account to create in the app: Tailscale tells the app who is visiting.

**Once**, in `.env`, set your own Tailscale login (the email shown at the top of https://login.tailscale.com):
```
JOBTRACKER_OWNER=you@example.com
```

**For each friend:**
1. **They** install Tailscale (App Store / Play Store / tailscale.com/download) and sign in with any account.
2. **You** share the PC: https://login.tailscale.com/admin/machines → this PC → ⋯ → **Share…** → invite by
   email or send the link. **They** open it and accept. Your other devices stay invisible to them.
3. **You** add their Tailscale login to `.env`, comma-separated, then `docker compose up -d`:
   ```
   JOBTRACKER_FRIENDS=friend@example.com,other.friend@example.com
   ```
   Not sure of their login? Let them open the address: the "Not invited" page shows it.
4. **They** open `https://<pc-name>.<tailnet>.ts.net` → *Install app* (Android, Chrome) or *Add to Home Screen*
   (iPhone, Safari). They start from example data and import their CV from the *Master CV* page.

**Limit friends to the app** (recommended, once): https://login.tailscale.com/admin/acls → if the policy has a
rule that lets everyone reach everything (`"src": ["*"]`), replace it with these two, then *Save*:
```json
{"action": "accept", "src": ["autogroup:member"], "dst": ["*:*"]},
{"action": "accept", "src": ["autogroup:shared"], "dst": ["*:443"]}
```
Your own devices keep full access; people you shared with reach only the https app on this PC.

**Good to know**
- Their data: `C:\JobSearch\data\users\<login>\` (included in `backup.ps1`). Removing a friend from
  `JOBTRACKER_FRIENDS` blocks them; their folder stays until you delete it.
- AI features use **your** OpenAI key for everyone (cents per action).
- You hold their CVs: same trust as handing you a copy.
- How it's safe: `tailscale serve` adds the visitor's identity (`Tailscale-User-Login`) to each request and removes
  any copy a visitor tries to send; the app only listens on `127.0.0.1`, so requests can't bypass Tailscale.
  Only set `JOBTRACKER_OWNER` with that setup.

---

## Everyday use

| You want to… | Do |
|---|---|
| Use the app | Open the https address (or the installed app) on any of your devices |
| See the logs | `docker compose logs -f app` |
| Restart | `docker compose restart app` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Change a setting | edit `.env`, then `docker compose up -d` |
| Check health | `curl.exe http://127.0.0.1:8000/health` |

**One copy of your data:** once the server holds your data, use the app through it from every device. Don't also
run `./run.sh` on a laptop against a copy of the same data, or the two copies will drift apart.

## Troubleshooting

- **`docker compose` says the daemon isn't running** → open Docker Desktop and wait for "Engine running".
- **The phone can't open the address** → Tailscale switched on on the phone? `tailscale status` on the PC shows it
  online? `tailscale serve status` shows the address?
- **"tailscale serve" fails about certificates** → enable HTTPS at https://login.tailscale.com/admin/dns.
- **Health OK but pages ask for a password you don't know** → it's `JOBTRACKER_PASSWORD` in `.env`.
- **CVs have no PDF** → `curl.exe http://127.0.0.1:8000/health` must show `"pdf_engine":"libreoffice"`
  (it does with the published image).
- **Something else** → `docker compose logs --tail 100 app`.
