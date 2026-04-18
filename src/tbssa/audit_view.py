from __future__ import annotations

from tbssa.db.models import AuditLog

_ACTION_ICONS: list[tuple[str, str]] = [
    ("reboot", "🔄"),
    ("sos", "🆘"),
    ("status", "📡"),
    ("user:add", "👤➕"),
    ("user:deactivate", "👤🚫"),
    ("user:reactivate", "👤✅"),
    ("user:activate", "👤✅"),
    ("user:edit", "👤✏️"),
    ("server:add", "🖥➕"),
    ("server:delete", "🖥🗑"),
    ("server:deactivate", "🖥🚫"),
    ("server:activate", "🖥✅"),
    ("server:edit", "🖥✏️"),
    ("config:set", "⚙️✏️"),
    ("open:admin", "👁"),
    ("broadcast", "📢"),
]


def audit_action_icon(action: str) -> str:
    for prefix, icon in _ACTION_ICONS:
        if action.startswith(prefix):
            return icon
    return "•"


def audit_actor_label(entry: AuditLog) -> str:
    return f"@{entry.username}" if entry.username else f"id:{entry.actor_id}"


def format_audit_row(entry: AuditLog) -> str:
    ts = entry.created_at.strftime("%d.%m %H:%M")
    platform = (entry.platform or "telegram").upper()
    icon = audit_action_icon(entry.action)
    details = f" {entry.details}" if entry.details else ""
    return f"<code>{ts}</code>  [{platform}] {audit_actor_label(entry)}\n{icon} <b>{entry.action}</b>{details}"
