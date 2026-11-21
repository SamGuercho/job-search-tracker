# Job Search Tracker: web app + PDF export (LibreOffice) + the CV font, in one image.
# Build: docker build -t job-search-tracker .   Run: docker compose up (see README)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# LibreOffice (Word -> PDF) and STIX Two Text, the font of the CV template (pinned release)
ARG STIX_VERSION=v2.13b171
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer-nogui fontconfig curl ca-certificates \
 && mkdir -p /usr/share/fonts/stix \
 && for style in Regular Bold Italic BoldItalic; do \
      curl -fsSL -o /usr/share/fonts/stix/STIXTwoText-$style.otf \
        "https://raw.githubusercontent.com/stipub/stixfonts/${STIX_VERSION}/fonts/static_otf/STIXTwoText-$style.otf"; \
    done \
 && fc-cache -f \
 && rm -rf /var/lib/apt/lists/*

# Codex CLI (pinned release): writes interview preps with the owner's ChatGPT plan (see services/prep.py).
# Sign in once on the server: docker compose exec app codex login --device-auth  (kept in the /codex volume)
ARG CODEX_VERSION=0.167.0
RUN arch="$(uname -m)" \
 && curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${arch}-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin \
 && mv "/usr/local/bin/codex-${arch}-unknown-linux-musl" /usr/local/bin/codex \
 && codex --version

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY jobtracker ./jobtracker
COPY web ./web
COPY data/seed ./data/seed
COPY mcp_server.py ./

# Unprivileged user; data and generated files live in volumes
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data /output /codex \
 && chown -R app:app /data /output /codex
USER app

ENV JOBTRACKER_HOST=0.0.0.0 \
    PORT=8000 \
    JOBTRACKER_DATA_DIR=/data \
    JOBTRACKER_OUTPUT_DIR=/output \
    JOBTRACKER_PDF_ENGINE=libreoffice \
    CODEX_HOME=/codex \
    LOG_FORMAT=json
VOLUME ["/data", "/output", "/codex"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["python", "-m", "jobtracker", "serve"]
