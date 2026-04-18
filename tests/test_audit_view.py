from datetime import datetime

from tbssa.audit_view import format_audit_row
from tbssa.db.models import AuditLog


def test_format_audit_row_includes_platform_actor_and_icon():
    entry = AuditLog(
        actor_id=42,
        platform="max",
        username="alice",
        action="server:delete",
        details="server=dc-app",
    )
    entry.created_at = datetime(2026, 4, 18, 20, 15)

    row = format_audit_row(entry)

    assert "[MAX]" in row
    assert "@alice" in row
    assert "🖥🗑" in row
    assert "<b>server:delete</b> server=dc-app" in row
