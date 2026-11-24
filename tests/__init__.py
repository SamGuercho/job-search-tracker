"""Test isolation. Python runs this file before any test module, whatever the import order in them,
so the app is always configured with throwaway folders before it is imported: tests can never read or
write real data, the real output folder (e.g. iCloud) or .env settings, and never call OpenAI."""
import os
import sys
import tempfile
from pathlib import Path

if "jobtracker" in sys.modules:  # configured too late: the real folders may already be in use
    raise RuntimeError("tests must be imported before jobtracker (run: python -m unittest discover -t . -s tests)")

TMP = Path(tempfile.mkdtemp(prefix="jobtracker-tests-"))
os.environ.update({
    "JOBTRACKER_NO_DOTENV": "1",          # ignore .env (real output folder, API key, password)
    "JOBTRACKER_DATA_DIR": str(TMP / "data"),
    "JOBTRACKER_OUTPUT_DIR": str(TMP / "out"),
    "JOBTRACKER_PDF_ENGINE": "none",
    "OPENAI_API_KEY": "",
    "JOBTRACKER_PASSWORD": "",
    "JOBTRACKER_REMINDERS": "0",
    "JOBTRACKER_TELEMETRY": "0",          # off, except in test_telemetry (which points it at its own file)
    "JOBTRACKER_TELEMETRY_DB": str(TMP / "telemetry.db"),
    "JOBTRACKER_TELEMETRY_BACKUP_DIR": "",
    "JOBTRACKER_LOGOS": "0",              # no background logo search on the web (tests call it with fakes)          # no background reminder thread (tests call it directly)
})


def assert_isolated() -> None:
    """Abort before any write if the app is not pointed at the temporary folders."""
    from jobtracker import config
    for path in (config.DATA_DIR, config.OUTPUT_DIR, config.USERS_DIR, config.TELEMETRY_DB):
        if not path.resolve().is_relative_to(TMP.resolve()):
            raise RuntimeError(f"Refusing to run tests against {path}: not a temporary test folder")
