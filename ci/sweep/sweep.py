#!/usr/bin/env python3
"""ci/sweep — the halcyon-packages upstream version sweeper (specs live
under pkgs/<name>/).

The Python replacement for `anda update` + the 48 update.rhai scripts (the
anda migration). Reads each registered package's [pkg.updates] table from
ci/packages.toml, asks the configured feed for the latest upstream version,
edits the spec in place (ci/sweep/spec.py has the exact anda semantics) and
reports what changed. The update workflow commits the result — straight to
main (default) or onto a bump branch — and the push makes copr-build.yml
rebuild the changed packages plus every higher batch.

Feed types:
  github-release  latest GitHub release tag of `repo` (leading v stripped)
  github-tag      newest git tag of `repo` (releases or not)
  custom          feeds.custom.custom_<name>() — the ported rhai logic
Packages without an [pkg.updates] table (the texlive-* rolling groups,
snapshot by tools/texlive-splitter) are never swept.

Usage:
  ci/sweep/sweep.py [--pkg NAME ...] [--dry-run] [--list]

Exits 1 when any feed failed (the workflow surfaces it); a feed error never
blocks the other packages.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feeds  # noqa: E402
from spec import SpecError, SpecFile  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_FILE = REPO_ROOT / "ci" / "packages.toml"


def die(msg: str) -> None:
    print(f"sweep.py: error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_swept() -> dict[str, tuple[dict, Path, Path]]:
    """Registry name -> (updates table, spec path, package dir) for every
    package that carries an [pkg.updates] table."""
    with open(PACKAGES_FILE, "rb") as fh:
        data = tomllib.load(fh)
    swept: dict[str, tuple[dict, Path, Path]] = {}
    for name, entry in data.items():
        updates = entry.get("updates")
        if not updates:
            continue  # texlive-* etc: rolled by texlive-update.yml, never swept
        base = REPO_ROOT / "pkgs" / name
        spec_path = base / f"{name}.spec"
        if not spec_path.is_file():
            die(f"[{name}]: {spec_path} does not exist")
        swept[name] = (updates, spec_path, base)
    return swept


def sweep_one(name: str, updates: dict, spec_path: Path, base: Path) -> tuple[bool, str, str]:
    """Sweep one package. Returns (wrote, old_version, new_version)."""
    import custom as custom_feeds

    spec = SpecFile(spec_path)
    old_version = spec.get_version()
    feed = updates.get("feed")
    if feed == "github-release":
        spec.set_version(feeds.github_release_tag(updates["repo"]))
    elif feed == "github-tag":
        spec.set_version(feeds.github_latest_tag(updates["repo"]))
    elif feed == "custom":
        fn_name = "custom_" + updates["custom"].replace("-", "_")
        fn = getattr(custom_feeds, fn_name, None)
        if fn is None:
            raise feeds.FeedError(f"no custom feed {fn_name} in ci/sweep/custom.py")
        fn(spec, base)
    else:
        raise feeds.FeedError(f"unknown feed type {feed!r}")
    wrote = spec.save()
    return wrote, old_version, spec.get_version()


def main() -> None:
    ap = argparse.ArgumentParser(description="halcyon-packages version sweep")
    ap.add_argument("--pkg", nargs="+", help="sweep only these packages (default: all swept)")
    ap.add_argument("--dry-run", action="store_true", help="do not write any spec")
    ap.add_argument("--list", action="store_true", help="print the swept packages and exit")
    args = ap.parse_args()

    swept = load_swept()
    if args.list:
        for name in sorted(swept):
            print(f"{swept[name][0].get('feed', '?'):>15}  {name}")
        return

    names = sorted(swept) if not args.pkg else args.pkg
    unknown = [n for n in names if n not in swept]
    if unknown:
        die(f"not swept (no [updates] table in {PACKAGES_FILE}): {', '.join(unknown)}")

    failed = False
    for name in names:
        updates, spec_path, base = swept[name]
        try:
            wrote, old_version, new_version = sweep_one(name, updates, spec_path, base)
        except (feeds.FeedError, SpecError, OSError, ValueError, KeyError,
                TypeError, subprocess.SubprocessError) as exc:
            # OSError covers urllib errors, ValueError malformed JSON,
            # KeyError/TypeError a malformed API payload, SubprocessError a
            # failing date/rpmspec — one broken feed must not abort the
            # remaining packages
            failed = True
            print(f"error: {name}: {exc}", file=sys.stderr)
            continue
        if wrote:
            print(f"bumped: {name}: {old_version} -> {new_version}")
        else:
            print(f"unchanged: {name} ({new_version})")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
