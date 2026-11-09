"""
ct_toolkit.utils.trajectory_scrub
---------------------------------
Redacts what should not leave the machine in agent trajectories: JSON, JSON lines or
plain-text session logs.

Found and masked: credentials (the patterns of SensitiveDataMasker), email addresses,
home directory names, public IPv4 addresses, and values listed by the caller or held in
secret-looking environment variables. A credential found once is masked wherever else it
appears in the same run, even printed bare. Redaction is best-effort: read the report.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from ct_toolkit.utils.sensitive_masker import SensitiveDataMasker

SECRET_ENV_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)
MIN_SECRET_LENGTH = 8

EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
HOME_DIR = re.compile(r"((?:/Users/|/home/))([^/\s\"']+)")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

CREDENTIAL_PATTERNS = [(re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in SensitiveDataMasker.API_KEY_PATTERNS]


@dataclass(frozen=True)
class Hit:
    file: str
    location: str
    category: str
    preview: str  # the masked value, never the original


@dataclass
class ScrubReport:
    hits: list[Hit] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # files that are not UTF-8 text
    files_changed: int = 0

    @property
    def by_category(self) -> Counter:
        return Counter(hit.category for hit in self.hits)


def _preview(value: str) -> str:
    return f"{value[:4]}…({len(value)} chars)" if len(value) > 4 else "***"


class Scrubber:
    """Scrubs trajectories. Use `scrub_paths` for files, or `scrub_text` / `scrub_object` for in-memory data."""

    def __init__(self, redact: Iterable[str] = (), env: Optional[Mapping[str, str]] = None, env_secrets: bool = True) -> None:
        self.listed: set[str] = {value for value in redact if value}
        if env_secrets:
            environment = os.environ if env is None else env
            self.listed.update(
                value
                for name, value in environment.items()
                if SECRET_ENV_NAME.search(name) and len(value) >= MIN_SECRET_LENGTH
            )
        self.learned: set[str] = set()  # credentials found so far, masked bare everywhere

    # -- Learning and masking one string -------------------------------------------

    def _learn(self, text: str) -> None:
        for pattern, _ in CREDENTIAL_PATTERNS:
            for match in pattern.finditer(text):
                if len(match.group(0)) >= MIN_SECRET_LENGTH:  # short matches would mask ordinary words
                    self.learned.add(match.group(0))

    def _mask(self, text: str, where: str, hits: list[Hit], file: str) -> str:
        def record(category: str, value: str) -> None:
            hits.append(Hit(file, where, category, _preview(value)))

        for value in sorted(self.listed | self.learned, key=len, reverse=True):
            if value and value in text:
                category = "credential" if value in self.learned else "listed value"
                record(category, value)
                text = text.replace(value, "***")
        for pattern, replacement in CREDENTIAL_PATTERNS:
            def credential(match: re.Match[str]) -> str:
                record("credential", match.group(0))
                return replacement

            text = pattern.sub(credential, text)
        def email(match: re.Match[str]) -> str:
            record("email", match.group(0))
            return "<email>"

        text = EMAIL.sub(email, text)

        def home(match: re.Match[str]) -> str:
            record("home directory", match.group(2))
            return f"{match.group(1)}<user>"

        text = HOME_DIR.sub(home, text)

        def public_ip(match: re.Match[str]) -> str:
            address = match.group(0)
            if all(int(octet) <= 255 for octet in address.split(".")) and ipaddress.ip_address(address).is_global:
                record("public IP", address)
                return "<ip>"
            return address

        return IPV4.sub(public_ip, text)

    # -- Walking data ----------------------------------------------------------------

    def _walk(self, value: Any, transform: Callable[[str, str], str], location: str = "") -> Any:
        if isinstance(value, str):
            return transform(value, location or "$")
        if isinstance(value, list):
            return [self._walk(item, transform, f"{location}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, dict):
            return {
                transform(str(key), f"{location}.<key>"): self._walk(item, transform, f"{location}.{key}")
                for key, item in value.items()
            }
        return value

    def _strings(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [text for item in value for text in self._strings(item)]
        if isinstance(value, dict):
            return [text for key, item in value.items() for text in (str(key), *self._strings(item))]
        return []

    # -- Files -----------------------------------------------------------------------

    def scrub_text(self, text: str, file: str = "<text>", hits: Optional[list[Hit]] = None) -> str:
        hits = [] if hits is None else hits
        self._learn(text)
        return self._mask(text, "text", hits, file)

    def scrub_object(self, value: Any, file: str = "<object>", hits: Optional[list[Hit]] = None) -> Any:
        """Scrubs the strings (and keys) of parsed JSON data; the structure is kept."""
        hits = [] if hits is None else hits
        for text in self._strings(value):
            self._learn(text)
        return self._walk(value, lambda text, where: self._mask(text, where, hits, file))

    def _scrub_content(self, content: str, name: str, hits: list[Hit]) -> str:
        """Scrubs one file's content, keeping JSON valid and unchanged lines byte for byte."""
        if name.endswith(".json"):
            try:
                data = json.loads(content)
            except ValueError:
                return self.scrub_text(content, name, hits)
            before = len(hits)
            cleaned = self.scrub_object(data, name, hits)
            return json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n" if len(hits) > before else content
        if name.endswith(".jsonl"):
            lines = content.splitlines(keepends=True)
            out = []
            for line in lines:
                body = line.rstrip("\r\n")
                ending = line[len(body) :]
                try:
                    data = json.loads(body)
                except ValueError:
                    out.append(self.scrub_text(line, name, hits))
                    continue
                before = len(hits)
                cleaned = self.scrub_object(data, name, hits)
                out.append(json.dumps(cleaned, ensure_ascii=False) + ending if len(hits) > before else line)
            return "".join(out)
        return self.scrub_text(content, name, hits)

    def scrub_paths(self, paths: Iterable[Path], out: Optional[Path] = None, root: Optional[Path] = None) -> ScrubReport:
        """Scrubs files (directories are walked). With `out`, writes the scrubbed copies there, keeping paths
        relative to `root`. Files without hits are copied byte for byte. Without `out`, nothing is written."""
        files = self._collect(paths)
        report = ScrubReport()
        texts: dict[Path, str] = {}
        for path in files:
            try:
                texts[path] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                report.skipped.append(str(path))
        # First pass: learn every credential in the run, so each is masked wherever it appears.
        for text in texts.values():
            self._learn(text)
        for path, text in texts.items():
            hits: list[Hit] = []
            cleaned = self._scrub_content(text, path.name, hits)
            report.hits.extend(hits)
            if not hits:
                if out is not None:
                    self._target(path, out, root).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, self._target(path, out, root))
                continue
            report.files_changed += 1
            if out is not None:
                target = self._target(path, out, root)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(cleaned, encoding="utf-8")
        return report

    @staticmethod
    def _collect(paths: Iterable[Path]) -> list[Path]:
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(sorted(child for child in path.rglob("*") if child.is_file()))
            elif path.is_file():
                files.append(path)
        return files

    @staticmethod
    def _target(path: Path, out: Path, root: Optional[Path]) -> Path:
        base = root if root is not None and root.is_dir() else path.parent
        try:
            relative = path.relative_to(base)
        except ValueError:
            relative = Path(path.name)
        return out / relative
