# Home server (Windows, always on)

Run Ezri (the job search tracker) on a computer that stays on at home, and use it from your phone, your laptop and
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
Windows blocks `.ps1` scripts by default ("running scripts is disabled"): run them as
`powershell -ExecutionPolicy Bypass -File .\script.ps1`, as below; this changes no setting.
If a `winget install` answers "No package found", refresh its catalogue first: `winget source update`.

---

## 1. Prepare the PC (5 min)

- **Windows 10 22H2 or Windows 11.** Check: `winver`.
- **Stay on 24/7** — after step 4 (once you have this folder), in an **administrator** PowerShell:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\harden-windows.ps1
  ```
  It turns off sleep and hibernation (the screen can still turn off), stops Windows Update from restarting the PC
  while you're signed in, and lists the last sleeps/restarts and their cause.
- **Automatic sign-in** (needed: Docker Desktop only starts once you're signed in, so after any reboot the app
  stays down until someone signs in). Use Microsoft's tool, which stores the password encrypted:
  `winget install -e --id Microsoft.Sysinternals.Autologon`, run `Autologon`, enter your Windows password, *Enable*.
- **Optional, recommended:** in the BIOS, set *Restore on AC power loss* to *Power On*, so the PC restarts after
  a power cut.

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
For the AI features, set **`OPENAI_API_KEY`**. No password: Tailscale decides who gets in (step 8). Keep `HOST_DATA_DIR=C:/JobSearch/data` and `HOST_OUTPUT_DIR=C:/JobSearch/companies` unless
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
The app itself opens only through Tailscale (step 8): `http://127.0.0.1:8000` answers "No access".

The container restarts by itself after a crash or a reboot (`restart: unless-stopped`).

## 8. Publish it to your devices over https (1 min)

```powershell
tailscale serve --bg 8000
tailscale serve status
```
The address shown (`https://<pc-name>.<tailnet>.ts.net`) works on **all your Tailscale devices** and nowhere else
(not on the internet). `tailscale serve` is remembered across reboots. Stop it with `tailscale serve --https=443 off`.

✅ Check: on your phone (Tailscale app on), open the address: your CV and applications are there, no password.
(You see example data instead? Your Tailscale login isn't the email in your master CV: see step 11.)
**Install it as an app**: Chrome menu ⋮ → *Install app* (Android) · Safari → Share → *Add to Home Screen* (iPhone).

## 9. Automatic updates (1 min)

```powershell
powershell -ExecutionPolicy Bypass -File .\install-update-task.ps1
```
Every 15 minutes (and at sign-in), `update.ps1` pulls the newest image published by CI and restarts the app if it
changed; if Docker Desktop isn't running, it starts it. Push to `main` → CI tests and publishes → the server updates itself. History: `update.log`.
**Safety net:** after each update the app must answer its health check (which also checks it can read your data).
If it doesn't, the previous version is put back within two minutes, the failed one is skipped until CI publishes a
new one (`bad-image.txt`), and its logs are kept in `failed-update.log`.

**Which version runs, and going back to one:** each update writes a line in `update.log` naming the versions, e.g.
`Updated: dda754a (Robustness: …) -> 88a95d4 (Assistant: compact long conversations)` (and `Rolled back: …` when
the safety net acts). The running version is also shown in the app (🔔 panel, or *More* on phones) and on
`/health`. To run a given version, put its commit in `.env`, e.g. `IMAGE_TAG=sha-dda754a`, then `docker compose up -d`;
back to following updates: `IMAGE_TAG=latest`.
The task updates the app only (the image), not the files in this folder. To update **everything at once, right
now** (the files with `git pull`, then the app once CI has published that exact version, a few minutes after a
push): `powershell -ExecutionPolicy Bypass -File .\update-now.ps1`. To stay on a version, set `IMAGE_TAG` in `.env` to a commit tag
(e.g. `sha-ade50a6`, see the repository's *Packages*).

## 10. Backups (recommended)

Your data lives only on this PC. Once, from this folder, set up a daily backup to a synced folder (3am, or at the
next start if the PC was off), and run a first one now:
```powershell
powershell -ExecutionPolicy Bypass -File .\backup.ps1 -Install                                     # → OneDrive\JobSearch-backups
powershell -ExecutionPolicy Bypass -File .\backup.ps1 -Install -Destination "D:\Backups\JobSearch"  # or another folder
```
What it keeps: your **data** (master CV, applications, chats, practice, friends' spaces) as a dated zip, the 30
most recent; and the **companies** folder (generated CVs, job descriptions, logos, **interview recordings**),
copied file by file: only what changed is copied, and nothing is ever deleted from the backup. History: `backup.log`.
The Today page tells you when the last backup failed or is more than 3 days old. The Codex sign-in is never backed up.

To restore: stop the app (`docker compose down`), unzip the newest `jobsearch-data-*.zip` into `C:\JobSearch\data`,
copy `companies` back to `C:\JobSearch\companies`, then `docker compose up -d`.

## 11. Share with friends

Friends use **your** server with **their own** space: their own master CV, applications, chats and CVs.
There's nothing to configure in the app: **Tailscale decides who gets in** (your devices, and the people
you share this PC with) and tells the app who is visiting. Each person gets their own space on first visit.

**For each friend:**
1. **They** install Tailscale (App Store / Play Store / tailscale.com/download) and sign in with any account.
2. **You** share the PC: https://login.tailscale.com/admin/machines → this PC → ⋯ → **Share…** → invite by
   email or send the link. **They** open it and accept. Your other devices stay invisible to them.
3. **They** open `https://<pc-name>.<tailnet>.ts.net` → *Install app* (Android, Chrome) or *Add to Home Screen*
   (iPhone, Safari). They start from example data and import their CV from the *Master CV* page.

To remove someone: revoke their share on the same Machines page. Their folder stays until you delete it.

**Limit friends to the app** (recommended, once): https://login.tailscale.com/admin/acls → if the policy has a
rule that lets everyone reach everything (`"src": ["*"]`), replace it with these two, then *Save*:
```json
{"action": "accept", "src": ["autogroup:member"], "dst": ["*:*"]},
{"action": "accept", "src": ["autogroup:shared"], "dst": ["*:443"]}
```
Your own devices keep full access; people you shared with reach only the https app on this PC.

**Good to know**
- **You** are recognised by the email in your master CV: your Tailscale login must be that email (it is if you
  sign in to Tailscale with it). The app remembers you in `data\owner.txt` on your first visit, so changing the
  CV email later changes nothing. Your Tailscale login is a different email? Add `JOBTRACKER_OWNER=<that login>`
  to `.env`, then `docker compose up -d`.
- **Open the app through its `https://….ts.net` address, also on this PC.** `http://127.0.0.1:8000` answers
  "No access" and the password is not used: with a password, anyone who knew it would be you.
- Friends' data: `C:\JobSearch\data\users\<their login>\` (included in `backup.ps1`).
- **AI assistants on your other devices** (Codex, Claude Code, Cursor…) can use the tracker on this PC through
  its MCP endpoint, `https://<this PC>.<tailnet>.ts.net/mcp`. On the Mac, for Codex:
  `codex mcp add job-search-tracker --url https://<this PC>.<tailnet>.ts.net/mcp`. They act as your
  Tailscale login (friends' assistants as theirs), so each sees only their own data.
- AI features use **your** OpenAI key for everyone (cents per action).
- You hold their CVs: same trust as handing you a copy.
- How it's safe: `tailscale serve` adds the visitor's identity (`Tailscale-User-Login`) to each request and removes
  any copy a visitor tries to send; the app only listens on `127.0.0.1`, so requests can't bypass Tailscale.
  This is turned on in `compose.yaml` (`JOBTRACKER_TAILSCALE_USERS`), which publishes the app on 127.0.0.1 only.

## 12. The monitor (assistant telemetry)

The `monitor` service (in `compose.yaml`, started and updated with the app) shows how the assistant behaves:
a dashboard (latency, tokens, cost, tools, errors per person) and the trace of every turn. **Only you** (the owner)
can open it; it shows everyone's conversations, so tell the people who use your server. Publish it once:
```powershell
tailscale serve --bg --https=8443 8100
```
Then open `https://<pc-name>.<tailnet>.ts.net:8443`. It installs like the app (*Install app* in Chrome,
*Add to Home Screen* in Safari) as **Ezri Monitor**, with its own icon (a chart badge). The data stays on this PC, in the Docker volume
`jobsearch_telemetry` (SQLite works reliably there, not in a Windows folder). Once a week the app writes a
compressed copy (`telemetry-<date>.db.xz`) to `data\telemetry-backups` and keeps the last 3; `backup.ps1` mirrors
that folder to `<backups>\telemetry` in OneDrive, so the cloud holds the same 3 files, never more. The live
database itself is never in the backups. The new service arrives with the deployment files: run `update-now.ps1` once.

To restore a copy (replaces the current telemetry), in this folder:
```powershell
docker compose stop app monitor
docker run --rm -v jobsearch_telemetry:/t -v "${PWD}\data\telemetry-backups:/b" alpine sh -c "rm -f /t/telemetry.db*; xzcat /b/telemetry-<date>.db.xz > /t/telemetry.db"
docker compose start app monitor
```

---

## Phone features

Nothing to set up: **📅 Add to calendar** on interview steps, **Share → Ezri** from LinkedIn (Android, once
the app is installed), **🎙 Record** on the application page, and **🔔 notifications** (each person turns them on
on their own devices; reminders follow each device's time zone). The server creates its notification key on first
use, in `HOST_DATA_DIR` (`vapid_private.pem`): keep it with your data. Turn the reminder checks off with
`JOBTRACKER_REMINDERS=0` in `.env`.

## Interview prep (premium accounts)

On an application's page, **✨ Prepare interview** researches the company and writes a prep pack: what the company
does, the likely interview process, topics to revise, your strengths and gaps for the role, 5 exercises with
reference solutions and 10-point grading rubrics, and questions to ask. It takes a few minutes, runs in the
background, and is also saved as `Interview_Prep.md` in the company folder. **Practice with the assistant** then
has the (cheap) assistant give you an exercise and grade your answer with its rubric.

It runs **Codex on this PC, signed in with your ChatGPT account**: no API billing, it uses your plan's limits
(model `JOBTRACKER_PREP_MODEL`, default `gpt-6.2-astra`, reasoning `JOBTRACKER_PREP_EFFORT`, default `high`).
So only the people you list get the button.

1. In `.env`, list who may use it (Tailscale logins, comma-separated):
   ```
   JOBTRACKER_PREMIUM=you@gmail.com,partner@gmail.com
   ```
2. Update: `powershell -ExecutionPolicy Bypass -File .\update-now.ps1`
3. Sign Codex in to your ChatGPT account, once:
   ```powershell
   docker compose exec app codex login --device-auth
   ```
   It prints a link and a code: open the link on any device, sign in to ChatGPT, enter the code.
4. ✅ Check: `docker compose exec app codex login status` says it is logged in with ChatGPT.

The sign-in is kept in `C:\JobSearch\app\deploy\home-server\codex` (or `HOST_CODEX_DIR`): it is a login token
for your ChatGPT account, so treat that folder like a password (it is git-ignored and not in `backup.ps1`).
Sign out with `docker compose exec app codex logout`. Anyone can still get a prep without the button: an AI
assistant connected through MCP can write it with the `prep_brief` and `prep_save` tools, on their own plan.

## Email (news from your Gmail or Outlook)

Each person can connect their Gmail, their personal Outlook (outlook.com, hotmail, live) or both, read-only, on
**Profile → Email**. Every 10 minutes the server looks at the
new emails, keeps only those that name one of that person's applications (the company's domain, its name or the
position), and has the cheap model say what happened: a rejection, an interview invite (with its date), a next
step, an offer. Each becomes an update on **Today** with **Apply** / **Dismiss**, and a notification. Nothing
changes by itself, and the other emails are never sent anywhere.

Set up Gmail, Outlook or both: each needs the app registered once (about 15 minutes each, done by you, the owner).
Only the ones you set up get a **Connect** button.

**Gmail** (Google):

1. Open https://console.cloud.google.com, create a project (e.g. "Ezri").
2. **APIs & Services → Library**: search **Gmail API**, **Enable**.
3. **Google Auth Platform** (OAuth consent screen): **Get started**, app name "Ezri", your email, audience
   **External**. Under **Data access**, **Add or remove scopes**: tick `.../auth/gmail.readonly`.
4. **Audience**: click **Publish app** (In production). In "Testing", Google cuts the access after 7 days and
   everyone would have to reconnect every week. The app stays unverified: people see "Google hasn't verified this
   app" when connecting → **Advanced → Go to Ezri**. That's expected for a private app (100 people at most).
5. **Clients → Create client**: type **Web application**, **Authorized redirect URIs**:
   `https://<pc-name>.<tailnet>.ts.net/mail/callback` (your app's address, as in step 8). **Create**.
6. In `.env`, paste the client's id and secret:
   ```
   GOOGLE_CLIENT_ID=1234-abc.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```
7. Update: `powershell -ExecutionPolicy Bypass -File .\update-now.ps1`, then **Profile → Email → Connect Gmail**.

Each person's access is a Google or Microsoft token in their data folder (`mail.json`): it can read their email, so the data
folder (and its backups) deserves the same care as a password. **Disconnect** on the Profile page deletes it (and
asks Google to revoke it); anyone can also remove "Ezri" from their Google account's third-party access.
**Outlook** (Microsoft, personal accounts only):

1. Open https://portal.azure.com with a Microsoft account (a free Azure account is enough: nothing is billed).
2. **Microsoft Entra ID → App registrations → New registration**: name "Ezri", supported account types
   **Personal Microsoft accounts only**, redirect URI: platform **Web**,
   `https://<pc-name>.<tailnet>.ts.net/mail/callback` (the same address as Gmail's). **Register**.
3. **Certificates & secrets → New client secret**: pick the longest expiry (24 months) and copy the **Value**
   right away (it's shown once). Note the date: when it expires, make a new one and update `.env`.
4. **API permissions → Add a permission → Microsoft Graph → Delegated**: tick `Mail.Read`, `User.Read`,
   `offline_access`. **Add permissions**. (No admin consent needed: each person allows it when connecting.)
5. In `.env`, paste the **Application (client) ID** from the app's Overview page, and the secret's value:
   ```
   MICROSOFT_CLIENT_ID=00000000-0000-0000-0000-000000000000
   MICROSOFT_CLIENT_SECRET=abc8Q~...
   ```
6. Update, then **Profile → Email → Connect Outlook**. People see that the app is unverified ("unverified" under
   its name): that's expected for a private app.

Outlook's access lapses after 90 days without a check (the background check keeps it alive). Microsoft has no way
for Ezri to revoke it: after **Disconnect**, people can remove "Ezri" at https://account.live.com/consent/Manage.

`JOBTRACKER_MAIL_EVERY_MIN` changes how often it checks; `JOBTRACKER_MAIL=0` turns the checks off.

---

## Everyday use

| You want to… | Do |
|---|---|
| Use the app | Open the https address (or the installed app) on any of your devices |
| **Update everything now** | `powershell -ExecutionPolicy Bypass -File .\update-now.ps1` (git pull, waits for CI's image of that commit, restarts the app, checks it answers) |
| See the logs | `docker compose logs -f app` |
| Restart | `docker compose restart app` |
| Stop / start | `docker compose down` / `docker compose up -d` |
| Change a setting | edit `.env`, then `docker compose up -d` |
| Check health | `curl.exe http://127.0.0.1:8000/health` |

**One copy of your data:** once the server holds your data, use the app through it from every device. Don't also
run `./run.sh` on a laptop against a copy of the same data, or the two copies will drift apart.

## Troubleshooting

- **The app went down / the PC slept or restarted** → run `harden-windows.ps1` again (administrator): it re-applies
  the settings and shows what happened (*SLEEP*, *RESTART* with the program that asked for it, *CRASH / POWER LOST*).
  Check automatic sign-in is ON. The update task also restarts Docker Desktop within 15 minutes if it was closed.
- **`docker compose` says the daemon isn't running** → open Docker Desktop and wait for "Engine running".
- **The phone can't open the address** → Tailscale switched on on the phone? `tailscale status` on the PC shows it
  online? `tailscale serve status` shows the address?
- **"tailscale serve" fails about certificates** → enable HTTPS at https://login.tailscale.com/admin/dns.
- **"No access: open the app through its Tailscale address"** → use `https://<pc-name>.<tailnet>.ts.net`, not
  `127.0.0.1` or the PC's IP; on the PC too.
- **You see example data instead of yours** → your Tailscale login isn't the email in your master CV: set
  `JOBTRACKER_OWNER=<your Tailscale login>` in `.env`, then `docker compose up -d` (step 11).
- **CVs have no PDF** → `curl.exe http://127.0.0.1:8000/health` must show `"pdf_engine":"libreoffice"`
  (it does with the published image).
- **`/health` says `"status":"error"`** (or the Today page says data files can't be read) → this version can't
  read some of your data files: `docker compose logs app | Select-String "Data check failed"` names them. After an
  update, the previous version is normally put back by itself (`update.log`, `failed-update.log`).
- **Something else** → `docker compose logs --tail 100 app`.
