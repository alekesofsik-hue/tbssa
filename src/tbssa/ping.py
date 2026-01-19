from __future__ import annotations

import re
import subprocess


def ping_once(host: str, timeout_s: int) -> float | None:
    """
    Ping 1 time. Returns RTT (ms) or None.

    Ubuntu: ping -c 1 -n -w {timeout}
    """
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-n", "-w", str(timeout_s), host],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if res.returncode != 0:
            return None
        m = re.search(r"time[=<]([\d\.]+)\s*ms", res.stdout)
        if not m:
            return None
        return float(m.group(1))
    except Exception:
        return None


def ping_status(host: str, count: int, timeout_s: int) -> tuple[int, list[float]]:
    rtts: list[float] = []
    for _ in range(max(1, count)):
        rtt = ping_once(host, timeout_s)
        if rtt is not None:
            rtts.append(rtt)
    return len(rtts), rtts

