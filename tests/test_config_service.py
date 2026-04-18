import logging

from tbssa.config_service import ConfigService


def test_log_load_summary_suppresses_duplicate_messages(caplog):
    svc = ConfigService()
    signature = svc._build_load_signature(
        telegram_admin_ids={1},
        max_admin_ids={2},
        server_ids={10},
        config_values={"PING_COUNT": "3"},
    )

    with caplog.at_level(logging.INFO, logger="tbssa"):
        svc._log_load_summary(signature)
        svc._log_load_summary(signature)

    records = [record.message for record in caplog.records if "[config_service]" in record.message]
    assert records == ["[config_service] loaded: 1 telegram admin(s), 1 max admin(s), 1 server(s), 1 config key(s)"]


def test_log_load_summary_logs_reload_when_signature_changes(caplog):
    svc = ConfigService()
    first = svc._build_load_signature(
        telegram_admin_ids={1},
        max_admin_ids=set(),
        server_ids={10},
        config_values={"PING_COUNT": "3"},
    )
    second = svc._build_load_signature(
        telegram_admin_ids={1},
        max_admin_ids={7},
        server_ids={10, 11},
        config_values={"PING_COUNT": "3"},
    )

    with caplog.at_level(logging.INFO, logger="tbssa"):
        svc._log_load_summary(first)
        svc._log_load_summary(second)

    records = [record.message for record in caplog.records if "[config_service]" in record.message]
    assert records[0].startswith("[config_service] loaded:")
    assert records[1] == "[config_service] reloaded: 1 telegram admin(s), 1 max admin(s), 2 server(s), 1 config key(s)"
