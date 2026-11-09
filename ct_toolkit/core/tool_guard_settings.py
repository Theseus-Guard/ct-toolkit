"""
ct_toolkit.core.tool_guard_settings
-----------------------------------
Tool-call guard settings kept in a JSON file, so the web dashboard, the proxy and the guardrail server share one
configuration.

Precedence, per process: a flag given explicitly on the command line > the settings file > built-in defaults.
The file is read again whenever its modification time changes, so a running proxy or server picks up a change
without a restart. A file that fails validation is reported and the last good settings stay in effect.

The API key of a judge model is never stored here: it comes from the environment, as for every other client.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from ct_toolkit.core.tool_guard import DEFAULT_ALLOWED_HOSTS, ToolCallJudge, ToolGuard, ToolGuardMode

logger = logging.getLogger("ct_toolkit.tool_guard_settings")

SETTINGS_ENV = "CT_TOOL_GUARD_SETTINGS"
DEFAULT_SETTINGS_PATH = "tool_guard_settings.json"
MAX_EXTRA_HOSTS = 50
MAX_TEXT = 500
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
_FIELDS = ("mode", "extra_hosts", "judge_model", "judge_api_base", "judge_fail_closed")


@dataclass(frozen=True)
class ToolGuardSettings:
    mode: str = ToolGuardMode.OBSERVE.value
    extra_hosts: tuple[str, ...] = ()
    judge_model: Optional[str] = None
    judge_api_base: Optional[str] = None
    judge_fail_closed: bool = False

    def allowed_hosts(self) -> tuple[str, ...]:
        return (*DEFAULT_ALLOWED_HOSTS, *self.extra_hosts)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["extra_hosts"] = list(self.extra_hosts)
        return data


def settings_path(explicit: Optional[str] = None) -> Path:
    """The settings file: the explicit path, else CT_TOOL_GUARD_SETTINGS, else ./tool_guard_settings.json."""
    return Path(explicit or os.environ.get(SETTINGS_ENV) or DEFAULT_SETTINGS_PATH)


def validate(data: Mapping[str, Any]) -> ToolGuardSettings:
    """The settings described by `data`. Raises ValueError with a message a person can act on."""
    if not isinstance(data, Mapping):
        raise ValueError("settings must be a JSON object")
    unknown = sorted(set(data) - set(_FIELDS))
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(unknown)}")

    try:
        mode = ToolGuardMode(str(data.get("mode", ToolGuardMode.OBSERVE.value)).strip().lower()).value
    except ValueError:
        raise ValueError("mode must be 'observe' or 'enforce'") from None

    hosts = data.get("extra_hosts") or []
    if not isinstance(hosts, (list, tuple)):
        raise ValueError("extra_hosts must be a list of host names")
    if len(hosts) > MAX_EXTRA_HOSTS:
        raise ValueError(f"at most {MAX_EXTRA_HOSTS} extra hosts")
    cleaned_hosts: list[str] = []
    for host in hosts:
        if not isinstance(host, str):
            raise ValueError("extra_hosts must contain only strings")
        name = host.strip().lower()
        if not _HOSTNAME.match(name) or len(name) > 253:
            raise ValueError(f"not a valid host name: {host!r}")
        if name not in cleaned_hosts:
            cleaned_hosts.append(name)

    model = _text_or_none(data.get("judge_model"), "judge_model")
    api_base = _text_or_none(data.get("judge_api_base"), "judge_api_base")
    if api_base is not None:
        parsed = urlparse(api_base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("judge_api_base must be an http:// or https:// URL")

    fail_closed = data.get("judge_fail_closed", False)
    if not isinstance(fail_closed, bool):
        raise ValueError("judge_fail_closed must be true or false")

    return ToolGuardSettings(
        mode=mode,
        extra_hosts=tuple(sorted(cleaned_hosts)),
        judge_model=model,
        judge_api_base=api_base,
        judge_fail_closed=fail_closed,
    )


def _text_or_none(value: Any, name: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if len(text) > MAX_TEXT:
        raise ValueError(f"{name} is longer than {MAX_TEXT} characters")
    return text or None


def load(path: Path) -> Optional[ToolGuardSettings]:
    """The settings in `path`, or None when the file does not exist. Raises ValueError for an invalid file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    try:
        return validate(data)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e


def save(path: Path, settings: ToolGuardSettings) -> None:
    """Writes the settings atomically: a temporary file in the same directory, then a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tool_guard_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def apply_to_guard(guard: ToolGuard, settings: ToolGuardSettings) -> None:
    """Makes `guard` behave as `settings` says. The judge is rebuilt only when its configuration changes."""
    guard.mode = ToolGuardMode(settings.mode)
    guard.allowed_hosts = tuple(host.lower() for host in settings.allowed_hosts())
    guard.judge_fail_closed = settings.judge_fail_closed
    current = getattr(guard.judge, "model", None), getattr(guard.judge, "api_base", None)
    wanted = (settings.judge_model, settings.judge_api_base)
    if current != wanted:
        guard.judge = ToolCallJudge(settings.judge_model, api_base=settings.judge_api_base) if settings.judge_model else None


class SettingsWatcher:
    """Keeps the settings of one file current, re-reading it when it changes."""

    def __init__(
        self,
        path: Path,
        defaults: Optional[ToolGuardSettings] = None,
        overrides: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """`overrides` are the fields given explicitly on the command line. They win over the file, always."""
        self.path = path
        self._defaults = defaults or ToolGuardSettings()
        self._overrides = dict(overrides or {})
        self._mtime: Optional[int] = None
        self._file: ToolGuardSettings = self._defaults
        self._current = self._with_overrides(self._file)
        self.source = "defaults"
        self.last_error: Optional[str] = None
        self.current()

    def _with_overrides(self, base: ToolGuardSettings) -> ToolGuardSettings:
        if not self._overrides:
            return base
        merged = base.to_dict()
        merged.update(self._overrides)
        return validate(merged)

    def current(self) -> ToolGuardSettings:
        try:
            mtime = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            if self._mtime is not None:
                self._file, self._mtime, self.source = self._defaults, None, "defaults"
                self._current = self._with_overrides(self._file)
            return self._current
        if mtime == self._mtime:
            return self._current
        try:
            loaded = load(self.path)
        except (OSError, ValueError) as e:
            self.last_error = str(e)
            logger.warning(f"Tool-call guard settings not applied: {e}")
            return self._current
        self.last_error = None
        self._mtime = mtime
        self._file = loaded or self._defaults
        self.source = "file" if loaded is not None else "defaults"
        self._current = self._with_overrides(self._file)
        return self._current
