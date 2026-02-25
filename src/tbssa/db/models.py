from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)  # @username из Telegram, автообновляется
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # first_name из Telegram
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # Реальное имя, вводится вручную
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<User telegram_id={self.telegram_id} username={self.username}>"


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ssh_host: Mapped[str] = mapped_column(String(256), nullable=False)
    ssh_user: Mapped[str] = mapped_column(String(64), default="bot-admin", nullable=False)
    ssh_key_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ssh_known_hosts_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ssh_fingerprint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ssh_connect_timeout: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    ssh_command_timeout: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    ping_host: Mapped[str] = mapped_column(String(256), nullable=False)
    ping_count: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    ping_timeout: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_ping_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<Server name={self.name} host={self.ssh_host}>"


class Config(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Config key={self.key} value={self.value}>"


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<AuditLog telegram_id={self.telegram_id} action={self.action}>"
