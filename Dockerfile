# CARACAL node image: admin web app + API (service "app"), Chromium player ("player") and the countdown
# overlay ("overlay"). The X display runs on the host (see docker/README.md); containers use its socket.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CARACAL_RUNTIME=docker \
    CARACAL_DATA=/var/lib/caracal \
    CARACAL_BASE=http://127.0.0.1:8080 \
    CARACAL_PROFILE=/var/lib/caracal/chromium

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-tk chromium xdotool wmctrl x11-xserver-utils x11-utils alsa-utils \
      fonts-dejavu-core fonts-noto-color-emoji curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/caracal
COPY requirements.txt .
RUN python3 -m venv /opt/caracal/.venv \
 && /opt/caracal/.venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/caracal/.venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app app
COPY player player
COPY docker/entrypoint-player.sh docker/entrypoint-overlay.sh /usr/local/bin/
COPY VERSION .
RUN chmod 755 /usr/local/bin/entrypoint-*.sh player/*.sh player/*.py

ARG VERSION=dev
LABEL org.opencontainers.image.title="CARACAL node" \
      org.opencontainers.image.description="Digital signage node: web admin, Chromium player and overlay" \
      org.opencontainers.image.version="${VERSION}"

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/setup-status || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/caracal/.venv/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
