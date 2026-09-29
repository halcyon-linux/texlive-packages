#!/usr/bin/env python3
"""Biweekly texlive roll driver — the automation behind
.github/workflows/texlive-update.yml.

Steps:
  1. resolve the newest tlnet-archive daily snapshot (HEAD probe, walking
     back from today; the archive keeps daily snapshots since 2019)
  2. fetch + decompress that snapshot's texlive.tlpdb (the .xz form,
     ~6 MB)
  3. partition scheme-full into the Arch-style groups (splitter.partition,
     no docs) and render one spec per group + texlive-meta (emit_groups)
  4. write only specs whose bytes changed; append batch-5 registry
     entries for any group the registry does not know yet

Everything derives from the snapshot date alone (no wall-clock in the
output), so re-running against the same snapshot writes nothing — that
idempotence is what lets the workflow commit unconditionally.

Usage:
  tools/texlive-splitter/roll.py [--snapshot YYYYMMDD] [--dry-run]
"""

from __future__ import annotations

import argparse
import ipaddress
import lzma
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import emit_groups  # noqa: E402
import splitter  # noqa: E402

ARCHIVE_ROOT = "https://texlive.info/tlnet-archive"
SCHEME = "scheme-full"
# halcyon trim (2026-09-29, maintainer decision): groups the image does not
# want. Pruned from the partition after scheme-full is split, so the biweekly
# roll can never regenerate or re-add them: their specs are not written, they
# never enter the registry again, and texlive-meta's Requires shrink to the
# survivors automatically. Surviving groups' cross-group Requires pointing
# into the pruned set are dropped too (render_group_spec only emits the
# depend entries that remain) — a surviving member needing a pruned-only
# file loses that feature, which is the accepted cost of the trim.
EXCLUDED_GROUPS = frozenset(
    [
        # 17 language collections
        "texlive-langarabic",
        "texlive-langchinese",
        "texlive-langcjk",
        "texlive-langcyrillic",
        "texlive-langczechslovak",
        "texlive-langenglish",
        "texlive-langeuropean",
        "texlive-langfrench",
        "texlive-langgerman",
        "texlive-langgreek",
        "texlive-langitalian",
        "texlive-langjapanese",
        "texlive-langkorean",
        "texlive-langother",
        "texlive-langpolish",
        "texlive-langportuguese",
        "texlive-langspanish",
        # heavy / niche collections
        "texlive-fontsextra",
        "texlive-context",
        "texlive-games",
        "texlive-music",
    ]
)
# texlive.info fronts the archive with Anubis, which allows the archive's
# sanctioned CLI clients (curl/wget — the same shape install-tl and the
# package %build use) and serves a challenge page to everything else;
# a generic bot UA gets HTTP 200 HTML instead of the tlpdb
UA = "Wget/1.21.3 (halcyon-packages texlive roll)"


def die(msg: str) -> None:
    print(f"roll.py: error: {msg}", file=sys.stderr)
    raise SystemExit(1)


# the roll only ever talks to the tlnet-archive host; every fetch is
# validated against this set (SSRF guard: scheme, allowlisted host, resolved
# global IPs, same-host redirects). Residual TOCTOU: urlopen re-resolves DNS
# after the check — the allowlisted host is the practical stdlib bound.
_ALLOWED_HOSTS = {urllib.parse.urlparse(ARCHIVE_ROOT).hostname}


def _validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        die(f"refusing non-https fetch: {url!r}")
    host = (parsed.hostname or "").rstrip(".")
    if not host or host not in _ALLOWED_HOSTS:
        die(f"refusing fetch to non-allowed host {host!r}")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 443)
    except socket.gaierror as exc:
        die(f"cannot resolve {host!r}: {exc}")
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            die(f"refusing fetch to non-public address {addr} ({host!r})")


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave the allowlisted host; urllib's own
    redirection cap (10) bounds the chain."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = (urllib.parse.urlparse(newurl).hostname or "").rstrip(".")
        if host not in _ALLOWED_HOSTS:
            raise urllib.error.HTTPError(
                newurl, code, f"redirect to non-allowed host {host!r}",
                headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirect())


def _open(url: str):
    _validate_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return _OPENER.open(req, timeout=120)


def snapshot_tlpdb_url(snapshot: str, archive_root: str) -> str:
    return (
        f"{archive_root}/{snapshot[:4]}/{snapshot[4:6]}/{snapshot[6:]}"
        "/tlnet/tlpkg/texlive.tlpdb.xz"
    )


def probe_newest(archive_root: str, max_back: int = 8) -> str:
    """Newest daily snapshot with a real tlpdb, walking back from today.

    Verified by the xz magic bytes, not the HTTP status: an incomplete
    snapshot answers 200 with an HTML placeholder."""
    today = date.today()
    for offset in range(max_back + 1):
        day = today - timedelta(days=offset)
        snapshot = day.strftime("%Y%m%d")
        try:
            with _open(snapshot_tlpdb_url(snapshot, archive_root)) as resp:
                if resp.read(6) != b"\xfd7zXZ\x00":
                    continue
                return snapshot
        except Exception:
            continue
    die(
        f"no tlnet-archive snapshot answered in the last {max_back + 1} "
        f"days ({archive_root}) — site down or layout changed"
    )
    raise AssertionError  # unreachable


def fetch_tlpdb(snapshot: str, archive_root: str, dest: Path) -> None:
    try:
        with _open(snapshot_tlpdb_url(snapshot, archive_root)) as resp:
            compressed = resp.read()
    except Exception as exc:
        die(f"fetching tlpdb for {snapshot} failed: {exc}")
    try:
        text = lzma.decompress(compressed).decode("utf-8", errors="replace")
    except lzma.LZMAError as exc:
        die(f"tlpdb for {snapshot} is not valid xz: {exc}")
    dest.write_text(text)


def registry_sync(names: list[str], registry: Path, dry_run: bool) -> bool:
    """Append batch-5 entries for groups missing from ci/packages.toml."""
    text = registry.read_text()
    missing = [n for n in names if f"[{n}]\n" not in text]
    if not missing:
        return False
    block = [
        "",
        "# texlive rolling groups (batch 5): generated specs owned by the",
        "# biweekly texlive roll (.github/workflows/texlive-update.yml);",
        "# never swept by the Monday update.yml — no updates tables, the",
        "# roll rewrites the specs wholesale.",
    ]
    for name in missing:
        block += ["", f"[{name}]", "batch = 5"]
    new_text = text.rstrip("\n") + "\n" + "\n".join(block) + "\n"
    if not dry_run:
        registry.write_text(new_text)
    print(f"registry: appended {len(missing)} batch-5 entries")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", help="YYYYMMDD (default: probe the newest)")
    ap.add_argument("--archive-root", default=ARCHIVE_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        repo_root = Path(
            subprocess.run(
                ["git", "-C", str(TOOLS), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        repo_root = TOOLS.parent.parent
    pkgs_root = repo_root / "pkgs"
    registry = repo_root / "ci" / "packages.toml"

    snapshot = args.snapshot or probe_newest(args.archive_root)
    print(f"snapshot: {snapshot}")

    with tempfile.TemporaryDirectory(prefix="tl-roll-") as tmp:
        tlpdb = Path(tmp) / "texlive.tlpdb"
        fetch_tlpdb(snapshot, args.archive_root, tlpdb)
        packages = splitter.parse_tlpdb(tlpdb)
        groups = splitter.partition(packages, SCHEME, docs=False)

    # halcyon trim: prune the excluded groups (and cross-group Requires
    # pointing into them) AFTER the partition — pruning here rather than
    # filtering the scheme keeps partition's first-walker claim semantics
    # byte-identical for the survivors, and render_meta_spec derives the
    # meta Requires from the surviving dict on its own
    pruned = sorted(EXCLUDED_GROUPS & set(groups))
    for group in pruned:
        del groups[group]
    for entry in groups.values():
        entry["depend"] = [d for d in entry["depend"] if d in groups]
    if pruned:
        print(f"trim: pruned {len(pruned)} groups, {len(groups)} survive")

    specs = emit_groups.emit_all(groups, snapshot, args.archive_root)
    total_files = sum(len(e["runfiles"]) for e in groups.values())
    total_members = sum(len(e["members"]) for e in groups.values())
    print(
        f"partition: {len(groups)} groups, {total_members} member tarballs, "
        f"{total_files} files claimed"
    )

    changed, unchanged = [], []
    for name, text in specs.items():
        path = pkgs_root / name / f"{name}.spec"
        if path.is_file() and path.read_text() == text:
            unchanged.append(name)
            continue
        changed.append(name)
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    stale = pkgs_root / "texlive-texmf"
    if stale.is_dir():
        print(
            "WARNING: pkgs/texlive-texmf still exists — the monolith was "
            "replaced by the per-group specs; remove it (and its registry "
            "entry) or it keeps building the old subpackage set."
        )

    # registry entries for texlive groups this roll did not produce mean an
    # upstream collection vanished or was renamed — the stale spec stays
    # pinned to its old snapshot and will 404 on the next rebuild
    registry_names = set()
    for line in registry.read_text().splitlines():
        line = line.strip()
        if line.startswith("[texlive-") and line.endswith("]"):
            registry_names.add(line[1:-1])
    orphaned = sorted(registry_names - set(specs))
    if orphaned:
        print(
            "WARNING: registry entries for groups this roll did not "
            f"produce (stale collections?): {', '.join(orphaned)} — "
            "handle manually (remove spec dir + registry entry)"
        )

    if changed:
        print(f"wrote {len(changed)} specs: {', '.join(sorted(changed))}")
    registry_sync(
        sorted(groups.keys()) + ["texlive-meta"], registry, args.dry_run
    )
    if not changed:
        print("unchanged: every spec already at this snapshot")
        return
    print(
        "next: commit + push; copr-build.yml rebuilds the changed texlive "
        "dirs as the batch-5 wave"
    )


if __name__ == "__main__":
    main()
