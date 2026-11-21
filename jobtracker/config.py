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

# Generated CV file name, without extension. {name} = your name with underscores.
CV_FILENAME = os.environ.get("JOBTRACKER_CV_FILENAME", "{name}_CV")

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
