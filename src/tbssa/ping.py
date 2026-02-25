from __future__ import annotations

import re
import subprocess


def ping_once(
    host: str,
    timeout_s: int,
    cmd_template: str | None = None,
) -> float | None:
    """
    Ping 1 time. Returns RTT (ms) or None.

    If cmd_template is provided, it must contain {timeout} and {host}.
    Example: "ping -c 1 -n -w {timeout} {host}"
    """
    try:
        if cmd_template:
            cmd_str = cmd_template.format(timeout=timeout_s, host=host)
            res = subprocess.run(
                cmd_str,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        else:
            res = subprocess.run(
                ["ping", "-c", "1", "-n", "-w", str(timeout_s), host],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        if res.returncode != 0:
            return None
        m = re.search(r"time[=<](\d*\.?\d+)\s*ms", res.stdout, re.IGNORECASE)
        if not m:
            return None
        return float(m.group(1))
    except Exception:
        return None


def ping_status(
    host: str,
    count: int,
    timeout_s: int,
    cmd_template: str | None = None,
) -> tuple[int, list[float]]:
    rtts: list[float] = []
    for _ in range(max(1, count)):
        rtt = ping_once(host, timeout_s, cmd_template)
        if rtt is not None:
            rtts.append(rtt)
    return len(rtts), rtts

