import asyncio

from tbssa.admin.monitor import (
    ConfirmedReachabilityCheck,
    check_servers_job,
    format_confirmed_reachability_report,
    run_confirmed_reachability_check,
)
from tbssa.config_service import ServerConfig


def _server() -> ServerConfig:
    return ServerConfig(
        id=1,
        name="dc-app",
        ssh_host="10.0.0.1",
        ssh_user="bot-admin",
        ssh_key_path="~/.ssh/id_ed25519_bot",
        ssh_known_hosts_path="~/.ssh/known_hosts",
        ssh_fingerprint="",
        ssh_connect_timeout=8,
        ssh_command_timeout=15,
        ping_host="10.0.0.1",
        ping_count=3,
        ping_timeout=1,
    )


class _DummySvc:
    def __init__(self, servers):
        self._servers = list(servers)

    def is_ready(self):
        return True

    def get_servers(self):
        return list(self._servers)

    def get_int(self, key, default=0):
        return 3

    def get_str(self, key, default=""):
        if key == "PING_CMD_TEMPLATE":
            return "ping -c 1 -n -w {timeout} {host}"
        return default


class _DummyJobQueue:
    def __init__(self):
        self.calls = []

    def run_once(self, callback, when, name):
        self.calls.append({"callback": callback, "when": when, "name": name})


class _DummyContext:
    def __init__(self, svc):
        self.bot_data = {"config_service": svc}
        self.job_queue = _DummyJobQueue()


def test_online_recovery_requires_successful_ssh_confirmation(monkeypatch):
    async def fake_icmp_check(server, cmd_template):
        return False

    ssh_results = iter([False, False])

    async def fake_ssh_check(server):
        return next(ssh_results)

    monkeypatch.setattr("tbssa.admin.monitor._icmp_check", fake_icmp_check)
    monkeypatch.setattr("tbssa.admin.monitor._ssh_check", fake_ssh_check)

    result = asyncio.run(
        run_confirmed_reachability_check(
            _server(),
            confirmed_ok=False,
            ping_template="ping -c 1 -n -w {timeout} {host}",
        )
    )

    assert result.confirmed_before is False
    assert result.confirmed_after is False
    assert result.changed is False
    assert result.icmp_ok is False
    assert result.ssh_stage1_ok is False
    assert result.ssh_stage2_ok is None


def test_online_recovery_is_reported_when_second_ssh_check_succeeds(monkeypatch):
    async def fake_icmp_check(server, cmd_template):
        return False

    ssh_results = iter([True, True])

    async def fake_ssh_check(server):
        return next(ssh_results)

    monkeypatch.setattr("tbssa.admin.monitor._icmp_check", fake_icmp_check)
    monkeypatch.setattr("tbssa.admin.monitor._ssh_check", fake_ssh_check)

    result = asyncio.run(
        run_confirmed_reachability_check(
            _server(),
            confirmed_ok=False,
            ping_template="ping -c 1 -n -w {timeout} {host}",
        )
    )

    assert result.confirmed_before is False
    assert result.confirmed_after is True
    assert result.changed is True
    assert result.ssh_stage1_ok is True
    assert result.ssh_stage2_ok is True


def test_offline_transition_requires_two_failed_ssh_checks(monkeypatch):
    async def fake_icmp_check(server, cmd_template):
        return True

    ssh_results = iter([False, True])

    async def fake_ssh_check(server):
        return next(ssh_results)

    monkeypatch.setattr("tbssa.admin.monitor._icmp_check", fake_icmp_check)
    monkeypatch.setattr("tbssa.admin.monitor._ssh_check", fake_ssh_check)

    result = asyncio.run(
        run_confirmed_reachability_check(
            _server(),
            confirmed_ok=True,
            ping_template="ping -c 1 -n -w {timeout} {host}",
        )
    )

    assert result.confirmed_before is True
    assert result.confirmed_after is True
    assert result.changed is False
    assert result.icmp_ok is True
    assert result.ssh_stage1_ok is False
    assert result.ssh_stage2_ok is True


def test_report_mentions_transition_when_two_ssh_checks_disagree():
    report = format_confirmed_reachability_report(
        ConfirmedReachabilityCheck(
            confirmed_before=False,
            confirmed_after=False,
            icmp_ok=True,
            ssh_stage1_ok=True,
            ssh_stage2_ok=False,
        )
    )

    assert "переходный результат" in report
    assert "Две SSH-проверки дали разный результат" in report


def test_report_clearly_separates_live_result_from_confirmed_status():
    report = format_confirmed_reachability_report(
        ConfirmedReachabilityCheck(
            confirmed_before=True,
            confirmed_after=False,
            icmp_ok=True,
            ssh_stage1_ok=False,
            ssh_stage2_ok=False,
        )
    )

    assert "Прямо сейчас SSH до сервера стабильно не проходит." in report
    assert "не совпадает с подтверждённым статусом мониторинга" in report
    assert "подтверждённый статус станет <b>SSH недоступен</b>" in report


def test_monitor_schedules_offline_confirmation_when_ssh_fails_even_if_icmp_ok(monkeypatch):
    svc = _DummySvc([_server()])
    context = _DummyContext(svc)

    async def fake_get_confirmed_ok(server_id):
        return True

    async def fake_ssh_check(server):
        return False

    async def fake_icmp_check(server, cmd_template):
        return True

    monkeypatch.setattr("tbssa.admin.monitor._get_confirmed_ok", fake_get_confirmed_ok)
    monkeypatch.setattr("tbssa.admin.monitor._ssh_check", fake_ssh_check)
    monkeypatch.setattr("tbssa.admin.monitor._icmp_check", fake_icmp_check)
    monkeypatch.setattr("tbssa.admin.monitor.secrets.token_hex", lambda _: "token")

    asyncio.run(check_servers_job(context))

    assert len(context.job_queue.calls) == 1
    assert context.job_queue.calls[0]["name"] == "confirm:offline:1:token:0"
    assert context.bot_data["monitor:pending"][1].direction == "offline"


def test_monitor_schedules_online_confirmation_when_ssh_works_even_if_icmp_fails(monkeypatch):
    svc = _DummySvc([_server()])
    context = _DummyContext(svc)

    async def fake_get_confirmed_ok(server_id):
        return False

    async def fake_ssh_check(server):
        return True

    async def fake_icmp_check(server, cmd_template):
        return False

    monkeypatch.setattr("tbssa.admin.monitor._get_confirmed_ok", fake_get_confirmed_ok)
    monkeypatch.setattr("tbssa.admin.monitor._ssh_check", fake_ssh_check)
    monkeypatch.setattr("tbssa.admin.monitor._icmp_check", fake_icmp_check)
    monkeypatch.setattr("tbssa.admin.monitor.secrets.token_hex", lambda _: "token")

    asyncio.run(check_servers_job(context))

    assert len(context.job_queue.calls) == 1
    assert context.job_queue.calls[0]["name"] == "confirm:online:1:token:0"
    assert context.bot_data["monitor:pending"][1].direction == "online"
