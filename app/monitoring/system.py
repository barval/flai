# app/monitoring/system.py
"""System load samples for the Monitoring tab (CPU, RAM, GPU, VRAM).

The other cards count what the application did; these four show what the host was
doing while it did it. A model that takes 30 s to load and a model that runs on a
swapped host look identical in the message counters, and the difference is
exactly what an administrator needs when a request "feels slow".

Samples are taken once a minute, kept for ``MONITORING_SYSTEM_RETENTION_DAYS``
(30) and averaged into the same buckets the other charts use. Everything degrades
to ``None`` when the source is absent (no ``nvidia-smi`` on a CPU host, the first
CPU sample has no previous reading to diff against): the card then shows nothing
instead of a zero that looks like idle.
"""

import logging
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

SYSTEM_INTERVAL_S = 60
SYSTEM_RETENTION_DAYS = 30

_previous: dict[str, float | None] = {"ts": None, "idle": None, "total": None}
_previous_lock = threading.Lock()


def parse_cpu_line(line: str) -> tuple[int, int]:
    """Idle and total jiffies from the first ``/proc/stat`` line."""
    fields = line.split()
    if not fields or fields[0] != "cpu":
        return 0, 0
    values = [int(value) for value in fields[1:8]]
    total = sum(values)
    # idle + iowait are "not doing work"; iowait is index 4 (5th field after cpu)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, total


def cpu_percent(idle: int, total: int, previous: dict[str, float | None]) -> float | None:
    """CPU utilisation between two readings, or None when there is no delta."""
    last_idle = previous.get("idle")
    last_total = previous.get("total")
    if last_idle is None or last_total is None:
        return None
    total_delta = total - last_total
    idle_delta = idle - last_idle
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)


def parse_meminfo(text: str) -> tuple[int, int]:
    """(used, total) in MiB from ``/proc/meminfo``."""
    total = available = 0
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) // 1024
        elif line.startswith("MemAvailable:"):
            available = int(line.split()[1]) // 1024
    used = max(0, total - available)
    return used, total


def parse_nvidia_smi(text: str) -> tuple[float, int, int] | None:
    """(utilisation %, used MiB, total MiB) from the nvidia-smi CSV line."""
    line = (text or "").strip().splitlines()
    if not line:
        return None
    parts = [part.strip() for part in line[0].split(",")]
    if len(parts) < 3:
        return None
    try:
        gpu = float(parts[0])
        used = int(float(parts[1]))
        total = int(float(parts[2]))
    except ValueError:
        return None
    return gpu, used, total


def bucket_average(
    rows: Iterable[tuple[datetime, float | None]],
    starts: list[datetime],
    size_seconds: int,
) -> list[float | None]:
    """Average of the samples that fall into each bucket.

    ``None`` means "no sample in this bucket" — an idle host and an unmeasured
    host must not look the same on the chart.
    """
    totals: list[float] = [0.0] * len(starts)
    counts: list[int] = [0] * len(starts)
    for taken_at, value in rows:
        if value is None or size_seconds <= 0:
            continue
        index = int((taken_at - starts[0]).total_seconds() // size_seconds)
        if 0 <= index < len(starts):
            totals[index] += float(value)
            counts[index] += 1
    return [round(totals[index] / counts[index], 1) if counts[index] else None for index in range(len(starts))]


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _gpu_reading() -> tuple[float, int, int] | None:
    import subprocess

    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return parse_nvidia_smi(out.stdout)


def sample() -> dict[str, object]:
    """One reading of the host; missing sources become None."""
    stat_lines = _read("/proc/stat").splitlines()
    idle, total = parse_cpu_line(stat_lines[0] if stat_lines else "")
    with _previous_lock:
        cpu = cpu_percent(idle, total, _previous) if total else None
        _previous.update({"ts": time.time(), "idle": idle, "total": total})

    ram_used, ram_total = parse_meminfo(_read("/proc/meminfo"))
    gpu = _gpu_reading()
    return {
        "cpu_pct": cpu,
        "ram_used_mb": ram_used or None,
        "ram_total_mb": ram_total or None,
        "gpu_pct": gpu[0] if gpu else None,
        "vram_used_mb": gpu[1] if gpu else None,
        "vram_total_mb": gpu[2] if gpu else None,
    }


def write_sample(now: datetime | None = None) -> None:
    from app.database import get_db

    reading = sample()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO system_samples
                (taken_at, cpu_pct, ram_used_mb, ram_total_mb, gpu_pct, vram_used_mb, vram_total_mb)
            VALUES (%(taken_at)s, %(cpu_pct)s, %(ram_used_mb)s, %(ram_total_mb)s,
                    %(gpu_pct)s, %(vram_used_mb)s, %(vram_total_mb)s)
            """,
            {"taken_at": now or datetime.now(UTC), **reading},
        )


def prune_samples(days: int = SYSTEM_RETENTION_DAYS) -> int:
    from app.database import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM system_samples WHERE taken_at < NOW() - (%s || ' days')::interval",
            (int(days),),
        )
        return int(cursor.rowcount or 0)


def system_series(starts: list[datetime], size_seconds: int) -> dict[str, list]:
    """Per-bucket averages plus the newest used/total pair for the value labels."""
    from app.database import get_db

    empty = {
        "cpu_pct": [None] * len(starts),
        "ram_pct": [None] * len(starts),
        "gpu_pct": [None] * len(starts),
        "vram_pct": [None] * len(starts),
    }
    meta: dict[str, object] = {"ram_used_mb": None, "ram_total_mb": None, "vram_used_mb": None, "vram_total_mb": None}
    if not starts:
        return {"series": empty, "latest": meta}

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT taken_at, cpu_pct, ram_used_mb, ram_total_mb, gpu_pct, vram_used_mb, vram_total_mb
            FROM system_samples
            WHERE taken_at >= %s
            ORDER BY taken_at
            """,
            (starts[0],),
        )
        rows = cursor.fetchall()

    if not rows:
        return {"series": empty, "latest": meta}

    def column(name: str) -> list[tuple[datetime, float | None]]:
        return [(row["taken_at"], row[name]) for row in rows]

    def percent(used: str, total: str) -> list[tuple[datetime, float | None]]:
        return [
            (
                row["taken_at"],
                round(100.0 * row[used] / row[total], 1) if row.get(total) and row.get(used) is not None else None,
            )
            for row in rows
        ]

    series = {
        "cpu_pct": bucket_average(column("cpu_pct"), starts, size_seconds),
        "ram_pct": bucket_average(percent("ram_used_mb", "ram_total_mb"), starts, size_seconds),
        "gpu_pct": bucket_average(column("gpu_pct"), starts, size_seconds),
        "vram_pct": bucket_average(percent("vram_used_mb", "vram_total_mb"), starts, size_seconds),
    }
    last = rows[-1]
    meta = {
        "ram_used_mb": last.get("ram_used_mb"),
        "ram_total_mb": last.get("ram_total_mb"),
        "vram_used_mb": last.get("vram_used_mb"),
        "vram_total_mb": last.get("vram_total_mb"),
    }
    return {"series": series, "latest": meta}


def start_system_sampler(app) -> None:
    """Daemon thread: one sample a minute, pruned daily."""
    running = getattr(app, "_system_sampler_thread", None)
    if running is not None and running.is_alive():
        return

    stop_event = threading.Event()
    app._system_sampler_stop = stop_event

    def _loop() -> None:
        pruned_on = None
        while not stop_event.is_set():
            try:
                with app.app_context():
                    write_sample()
                    today = datetime.now(UTC).date()
                    if pruned_on != today:
                        prune_samples()
                        pruned_on = today
            except Exception as exc:  # never kill the sampler on a bad reading
                logger.warning(f"System sample failed: {exc}")
            if stop_event.wait(SYSTEM_INTERVAL_S):
                return

    thread = threading.Thread(target=_loop, name="flai-system-sampler", daemon=True)
    app._system_sampler_thread = thread
    thread.start()
