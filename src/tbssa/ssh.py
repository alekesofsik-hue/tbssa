from __future__ import annotations

import os
import time

import paramiko


def _normalize_md5_fingerprint(value: str) -> str:
    v = (value or "").strip().lower()
    if v.startswith("md5:"):
        v = v[4:]
    v = v.replace(":", "")
    return v


def _md5_fingerprint_colonized(fp_bytes: bytes) -> str:
    return ":".join(f"{b:02x}" for b in fp_bytes)


class FingerprintPolicy(paramiko.client.MissingHostKeyPolicy):
    """
    Accepts unknown host keys only if their MD5 fingerprint matches the expected fingerprint.
    The expected format can be with ':' (aa:bb:..) or without.
    """

    def __init__(self, expected_md5: str):
        self.expected = _normalize_md5_fingerprint(expected_md5)

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        actual = _normalize_md5_fingerprint(_md5_fingerprint_colonized(key.get_fingerprint()))
        if actual != self.expected:
            raise paramiko.SSHException(
                f"SSH host key fingerprint mismatch for {hostname}: expected={self.expected} actual={actual}"
            )
        # accept
        client._host_keys.add(hostname, key.get_name(), key)  # type: ignore[attr-defined]


def ssh_exec(
    *,
    host: str,
    user: str,
    key_path: str,
    connect_timeout: int,
    command_timeout: int,
    cmd: str,
    known_hosts_path: str,
    pinned_fingerprint_md5: str,
) -> tuple[int, str, str]:
    key_path = os.path.expanduser(key_path)
    known_hosts_path = os.path.expanduser(known_hosts_path)
    key = paramiko.Ed25519Key.from_private_key_file(key_path)
    client = paramiko.SSHClient()
    try:
        # Prefer strict host key checking via known_hosts.
        try:
            client.load_host_keys(known_hosts_path)
        except OSError:
            # File missing/unreadable: only allow if fingerprint pinning is configured.
            pass

        if pinned_fingerprint_md5.strip():
            client.set_missing_host_key_policy(FingerprintPolicy(pinned_fingerprint_md5))
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

        client.connect(
            hostname=host,
            username=user,
            pkey=key,
            timeout=connect_timeout,
            banner_timeout=connect_timeout,
            auth_timeout=connect_timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        _, stdout, stderr = client.exec_command(cmd, timeout=command_timeout)
        deadline = time.time() + command_timeout
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        ch = stdout.channel
        while time.time() < deadline:
            if ch.recv_ready():
                out_chunks.append(ch.recv(65536))
            if ch.recv_stderr_ready():
                err_chunks.append(ch.recv_stderr(65536))
            if ch.exit_status_ready():
                while ch.recv_ready():
                    out_chunks.append(ch.recv(65536))
                while ch.recv_stderr_ready():
                    err_chunks.append(ch.recv_stderr(65536))
                rc = ch.recv_exit_status()
                return (
                    rc,
                    b"".join(out_chunks).decode("utf-8", "ignore"),
                    b"".join(err_chunks).decode("utf-8", "ignore"),
                )
            time.sleep(0.05)

        try:
            ch.close()
        except Exception:
            pass
        return -1, "", "timeout"
    finally:
        client.close()


def ps(cmd: str) -> str:
    # Short commands — use -Command (no EncodedCommand).
    return (
        'powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "'
        + cmd
        + '"'
    )

