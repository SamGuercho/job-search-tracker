"""Filesystem locations and settings. Override with environment variables or a .env file."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env support (KEY=value lines) so secrets and paths stay out of the code."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if value[:1] in "\"'":
                value = value.strip("\"'")
            else:
                value = value.split(" #", 1)[0].strip()  # inline comment: KEY=value  # note
            os.environ.setdefault(key.strip(), value)


if not os.environ.get("JOBTRACKER_NO_DOTENV"):  # tests set it: .env must never leak into them
    _load_dotenv(ROOT / ".env")  # before reading any setting below

DATA_DIR = Path(os.environ.get("JOBTRACKER_DATA_DIR", ROOT / "data")).expanduser()
SEED_DIR = ROOT / "data" / "seed"
OUTPUT_DIR = Path(os.environ.get("JOBTRACKER_OUTPUT_DIR", ROOT / "output")).expanduser()
WEB_DIR = ROOT / "web"

# Several users behind `tailscale serve` (see jobtracker/users.py). Turned on by deploy/home-server/compose.yaml.
TAILSCALE_USERS = os.environ.get("JOBTRACKER_TAILSCALE_USERS", "").lower() in ("1", "true", "yes")
OWNER = os.environ.get("JOBTRACKER_OWNER", "").strip().lower()  # rarely needed: see users.owner()
USERS_DIR = Path(os.environ.get("JOBTRACKER_USERS_DIR", DATA_DIR / "users")).expanduser()

# Chat telemetry (jobtracker/telemetry): one SQLite file for everyone's events, read by the monitor app.
# Deliberately NOT in JOBTRACKER_DATA_DIR, which is often a synced folder (iCloud, OneDrive): the database grows
# fast and syncing a live SQLite file can corrupt it. Default: the app's own data/ folder (local, never in git).
# In Docker it lives on a named volume (SQLite locking is unreliable on Windows bind mounts). 0 turns it off.
TELEMETRY = os.environ.get("JOBTRACKER_TELEMETRY", "1").lower() not in ("0", "false", "no")
TELEMETRY_DB = Path(os.environ.get("JOBTRACKER_TELEMETRY_DB", ROOT / "data" / "telemetry.db")).expanduser()
# Optional: a folder where a consistent copy of the database is written once a day (for backups). Off by default.
_backup = os.environ.get("JOBTRACKER_TELEMETRY_BACKUP_DIR", "").strip()
TELEMETRY_BACKUP_DIR = Path(_backup).expanduser() if _backup else None
# Compressed copies of the telemetry: one every N days, only the last K kept (see telemetry/backup.py)
TELEMETRY_BACKUP_EVERY_DAYS = float(os.environ.get("JOBTRACKER_TELEMETRY_BACKUP_EVERY_DAYS", "7"))
TELEMETRY_BACKUP_KEEP = int(os.environ.get("JOBTRACKER_TELEMETRY_BACKUP_KEEP", "3"))
# Detectors (telemetry/detect.py) suggest a flag for turns above these (0 = never)
FLAG_COST_USD = float(os.environ.get("JOBTRACKER_FLAG_COST_USD", "0.10"))
FLAG_SLOW_S = float(os.environ.get("JOBTRACKER_FLAG_SLOW_S", "60"))
MONITOR_PORT = int(os.environ.get("MONITOR_PORT", "8100"))
# Where the fixing process (`python -m jobtracker eval`) reaches the monitor: the server's, over Tailscale
MONITOR_URL = os.environ.get("JOBTRACKER_MONITOR_URL", f"http://127.0.0.1:{MONITOR_PORT}")

# Generated CV file name, without extension. {name} = your name with underscores.
# CV file names: {first_name} {last_name} {position} {company} ({name} = the full name); empty parts are left out
CV_FILENAME = os.environ.get("JOBTRACKER_CV_FILENAME", "{first_name}-{last_name}-{position}-{company}")

# Web server
HOST = os.environ.get("JOBTRACKER_HOST", "127.0.0.1")          # 0.0.0.0 in a container
PORT = int(os.environ.get("PORT", "8000"))                       # PORT is the cloud convention
PASSWORD = os.environ.get("JOBTRACKER_PASSWORD", "")            # set it to require a login
SESSION_DAYS = int(os.environ.get("JOBTRACKER_SESSION_DAYS", "30"))
LOG_FORMAT = os.environ.get("LOG_FORMAT", "text")               # "json" for cloud log tools
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_CHAT_MODEL = os.environ.get("OPENAI_CHAT_MODEL", OPENAI_MODEL)

# Fallback when OpenAI fails on quota, billing or access (needs ANTHROPIC_API_KEY).
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
ANTHROPIC_EFFORT = os.environ.get("ANTHROPIC_EFFORT", "medium")   # low | medium | high | xhigh | max

# Interview prep written by Codex (signed in with a ChatGPT plan: no API billing), for premium users only.
CODEX_BIN = os.environ.get("JOBTRACKER_CODEX", "codex")          # the codex command (in the Docker image: codex)
PREP_MODEL = os.environ.get("JOBTRACKER_PREP_MODEL", "gpt-6.2-astra")
PREP_EFFORT = os.environ.get("JOBTRACKER_PREP_EFFORT", "high")    # low | medium | high | xhigh | max | ultra
PREP_TIMEOUT_MIN = int(os.environ.get("JOBTRACKER_PREP_TIMEOUT_MIN", "40"))
# Scheduling a technical interview starts its prep by itself (premium accounts, once per application). 0 = off.
AUTO_PREP = os.environ.get("JOBTRACKER_AUTO_PREP", "1") != "0"
# Tailscale logins allowed to run it (comma-separated). Single-user mode (no Tailscale users): always allowed.
# Interview recordings: transcription (OpenAI, billed per minute). The diarize model labels speakers; OpenAI shuts
# it down on 2027-02-26, after which the app falls back to TRANSCRIBE_FALLBACK_MODEL (text only).
TRANSCRIBE_MODEL = os.environ.get("JOBTRACKER_TRANSCRIBE_MODEL", "gpt-4o-transcribe-diarize")
TRANSCRIBE_FALLBACK_MODEL = os.environ.get("JOBTRACKER_TRANSCRIBE_FALLBACK_MODEL", "gpt-transcribe")
# Notifications: the contact the push services (Google, Apple…) see in the server's signed requests
PUSH_CONTACT = os.environ.get("JOBTRACKER_PUSH_CONTACT", "mailto:noreply@example.com")
FFMPEG_BIN = os.environ.get("JOBTRACKER_FFMPEG", "ffmpeg")      # reads and shrinks the audio
FFPROBE_BIN = os.environ.get("JOBTRACKER_FFPROBE", "ffprobe")
MAX_RECORDING_MB = int(os.environ.get("JOBTRACKER_MAX_RECORDING_MB", "500"))
PREMIUM = {u.strip().lower() for u in os.environ.get("JOBTRACKER_PREMIUM", "").split(",") if u.strip()}
