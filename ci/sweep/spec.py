"""Spec text editing for the version sweeper — the Python port of andax's
`rpm.version()` / `rpm.global()` / `rpm.source()` with byte-identical output
semantics (verified against `anda update` on the real specs, 2026-09-25):

- line whitespace before the value is preserved on every rewrite;
- rpm.version() resets Release to `1%{?dist}` ONLY when the version actually
  changes (a no-op sweep never touches a hand-bumped Release);
- %changelog is never touched;
- the file is written only when the content actually changed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_VALUE = r"[^\s]+"  # spec preamble values are single tokens (no spaces)


class SpecError(Exception):
    pass


class SpecFile:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.text = self.path.read_text()
        self._original = self.text

    @property
    def changed(self) -> bool:
        return self._original != self.text

    def save(self) -> bool:
        """Write back only when the content actually changed (anda's rule)."""
        if self.text == self._original:
            return False
        self.path.write_text(self.text)
        return True

    # ---- Version / Release -------------------------------------------------

    def get_version(self) -> str:
        m = re.search(rf"(?m)^Version:[ \t]*({_VALUE})", self.text)
        if not m:
            raise SpecError(f"{self.path}: no Version: line")
        val = m.group(1)
        if val.startswith("%"):
            try:
                proc = subprocess.run(
                    ["rpmspec", "-D", "fedora 44", "-q", "--qf", "%{version}\\n", str(self.path)],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                first_line = proc.stdout.strip().splitlines()
                if first_line:
                    return first_line[0].strip()
            except Exception:
                pass
        return val

    def set_version(self, version: str, reset_release: bool = True) -> bool:
        """rpm.version() port: strip a leading `v`, rewrite the Version value,
        reset Release to 1%{?dist} — only when the version really changes.
        Returns whether anything was rewritten."""
        version = version.removeprefix("v")
        if version == self.get_version():
            return False
        text = re.sub(
            rf"(?m)^(Version:[ \t]*){_VALUE}",
            lambda m: m.group(1) + version,
            self.text,
            count=1,
        )
        if reset_release:
            text = re.sub(
                rf"(?m)^(Release:[ \t]*){_VALUE}",
                lambda m: m.group(1) + "1%{?dist}",
                text,
                count=1,
            )
        self.text = text
        return True

    def set_snapshot_version(self, base: str, counter: int | None) -> None:
        """noctalia-style Version rewrite: the whole value token becomes
        `<base>^<counter>.%{shortcommit}` (counter None = `^1` reset semantics
        handled by the caller); mirrors the rhai's `Version:\t...` template —
        a literal tab."""
        counter = 1 if counter is None else counter
        value = f"{base}^{counter}.%{{shortcommit}}"
        self.text, n = re.subn(
            rf"(?m)^(Version:[ \t]*){_VALUE}",
            lambda m: m.group(1) + value,
            self.text,
            count=1,
        )
        if n != 1:
            raise SpecError(f"{self.path}: no Version: line")

    # ---- %global macros ----------------------------------------------------

    def get_global(self, name: str) -> str | None:
        m = re.search(
            rf"(?m)^%global[ \t]+{re.escape(name)}[ \t]+([^\n]+)$", self.text
        )
        return m.group(1).strip() if m else None

    def set_global(self, name: str, value: str) -> bool:
        """rpm.global() port: rewrite the `%global <name> <value>` line in
        place, preserving the whitespace columns. Returns whether the value
        actually changed."""
        pattern = rf"(?m)^%global([ \t]+){re.escape(name)}([ \t]+)[^\n]*$"
        if not re.search(pattern, self.text):
            raise SpecError(f"{self.path}: no %%global {name} line to rewrite")
        new_text = re.sub(
            pattern,
            lambda m: f"%global{m.group(1)}{name}{m.group(2)}{value}",
            self.text,
            count=1,
        )
        changed = new_text != self.text
        self.text = new_text
        return changed

    # ---- Source entries ----------------------------------------------------

    def _source_pattern(self, n: int) -> str:
        # Source: and Source0: both denote source zero
        tag = "Source" if n == 0 else f"Source{n}"
        return rf"(?m)^(?P<tag>{tag}:(?P<w>[ \t]*))(?P<val>{_VALUE})"

    def get_source(self, n: int) -> str | None:
        m = re.search(self._source_pattern(n), self.text)
        return m.group("val") if m else None

    def set_source(self, n: int, url: str) -> bool:
        """rpm.source() port: rewrite the SourceN URL. Returns whether the
        URL actually changed."""
        m = re.search(self._source_pattern(n), self.text)
        if not m:
            raise SpecError(f"{self.path}: no Source{n}: line")
        if m.group("val") == url:
            return False
        self.text = re.sub(
            self._source_pattern(n),
            lambda mm: f"{mm.group('tag')}{url}",
            self.text,
            count=1,
        )
        return True
