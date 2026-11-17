# services/netdiag — the diagnostics sidecar.
#
# One program per tool, no shell: the Flask app in app.py validates every field and
# runs the command as a list. NET_RAW is added by compose for ping, mtr and SYN
# scanning; nothing else in here needs a capability.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        nmap \
        iperf3 \
        bind9-dnsutils \
        whois \
        curl \
        iputils-ping \
        mtr-tiny \
        traceroute \
        netcat-openbsd \
        python3 \
        python3-pip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY app.py ./

RUN useradd --system --uid 10001 --shell /usr/sbin/nologin netdiag
USER netdiag

EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -fsS -H "Authorization: Bearer ${NETDIAG_API_TOKEN}" http://127.0.0.1:8099/health || exit 1

CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:8099", "app:app"]
