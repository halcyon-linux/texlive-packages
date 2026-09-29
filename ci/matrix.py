#!/usr/bin/env python3
"""ci/matrix.py - emit the Copr build matrix for texlive-packages.

Selects which packages a push/dispatch rebuilds and emits a terra-shaped
`build_matrix=<json>` for the workflow. Entries: {"name": "<registry key>",
"arch": "x86_64", "labels": {}} — copr-build.yml keys jobs off `name` and
`labels.batch`.

Selection semantics:

  --since REV   every package whose pkgs/<name>/ directory changed since REV,
                plus its transitive dependents (packages that BuildRequire
                it, resolved from the specs). Touches under ci/ or
                .github/builder/ rebuild everything, as does a push that
                touches no registered package. A spec whose BuildRequires
                cannot be parsed confidently is seeded on every lower-batch
                change (with its own dependents following).
  no --since    the whole registry (first submission / manual full runs).

Batch numbers come from ci/packages.toml, which stays the dependency-order
registry: packages in one batch must never depend on each other, batch N may
BuildRequire batch < N output (Copr makes each successful build visible to
the project repo immediately, and copr-build.yml submits wave-by-wave).

The same registry carries each package's [pkg.updates] sweep configuration
(consumed by ci/sweep/sweep.py); matrix.py ignores it.

Usage:
  ci/matrix.py [--since REV] [--only PKG...] [--batch N ...] [--list]
               [--label LABEL=VALUE]

With --label each emitted entry carries extra labels (the workflow uses
labels.batch to wave the matrix). Without --list the script prints
`build_matrix=...` on stdout plus `batches=N,M,...` so the workflow knows
which waves exist without re-parsing the JSON.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES_FILE = REPO_ROOT / "ci" / "packages.toml"
# Paths whose changes invalidate every package (infrastructure).
INFRA_PREFIXES = ("ci/", ".github/builder/")
ARCH = "x86_64"
# the Copr projects a package can build into (ci/packages.toml `project`
# field; absent = this repository's project). Each project must own a disjoint set
# of packages — the registry is the single source of truth for membership.
KNOWN_PROJECTS = ("texlive-packages",)


def die(msg: str) -> None:
    print(f"matrix.py: error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def git_lines(*args: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def git_rev_exists(rev: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{rev}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def package_paths(name: str) -> tuple[Path, Path]:
    """Spec location for a registry name: every package (hand-maintained or
    generated, the texlive-* groups included) lives at pkgs/<name>/<name>.spec."""
    base = REPO_ROOT / "pkgs" / name
    spec = base / f"{name}.spec"
    return base, spec


def load_packages() -> dict[str, dict]:
    with open(PACKAGES_FILE, "rb") as fh:
        data = tomllib.load(fh)
    pkgs: dict[str, dict] = {}
    for name, entry in data.items():
        base, spec = package_paths(name)
        if not spec.is_file():
            die(f"[{name}]: {spec} does not exist")
        pkgs[name] = {
            "batch": int(entry.get("batch", 0)),
            "project": entry.get("project", "texlive-packages"),
            "conservative": False,
        }
        if pkgs[name]["project"] not in KNOWN_PROJECTS:
            die(
                f"[{name}]: unknown project {pkgs[name]['project']!r} "
                f"(known: {', '.join(KNOWN_PROJECTS)})"
            )
        parsed = _br_tokens(spec)
        if parsed is None:
            pkgs[name]["brs"] = []
            pkgs[name]["conservative"] = True
        else:
            pkgs[name]["brs"], conservative = parsed
            pkgs[name]["conservative"] = conservative
    if not pkgs:
        die(f"{PACKAGES_FILE}: no packages defined")
    depths = sorted({meta["batch"] for meta in pkgs.values()})
    if not depths:
        die(f"{PACKAGES_FILE}: no packages defined")
    if len(depths) > 1 and depths[0] != 0:
        die(f"{PACKAGES_FILE}: multi-batch registries must start at 0, got {depths}")
    # gaps are allowed — an intentionally empty dependency level (batch 4 is
    # empty); waves with no members just skip
    _validate_batches(pkgs)
    return pkgs


def select(
    pkgs: dict[str, dict],
    only: list[str] | None,
    batches: list[int] | None,
    since: str | None,
) -> set[str]:
    """Resolve --only / --batch / --since into the package names to build."""
    if only and since:
        die("--only and --since are mutually exclusive")

    if since:
        chosen = select_since(pkgs, since)
    else:
        chosen = set(pkgs)
    if only:
        unknown = sorted(set(only) - set(pkgs))
        if unknown:
            die(f"not in {PACKAGES_FILE}: {', '.join(unknown)}")
        chosen &= set(only)
    if batches:
        chosen &= {n for n, meta in pkgs.items() if meta["batch"] in batches}
    return chosen


def _br_tokens(spec: Path) -> tuple[list[str], bool] | None:
    """BuildRequires tokens of a spec + whether any line was macro-wrapped.

    A macro-wrapped name (token starting with %) cannot be resolved
    statically; such specs are handled conservatively by the caller
    instead of failing the whole selection. Conditional macro suffixes
    (qt6-qtbase-devel%{?_isa}) are stripped before resolution."""
    try:
        text = spec.read_text(errors="replace")
    except OSError:
        return None
    tokens: list[str] = []
    conservative = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("buildrequires:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if not value:
            continue
        token = value.split()[0]
        if token.startswith("%"):
            conservative = True
            continue
        token = token.split("%", 1)[0]
        if token:
            tokens.append(token)
    return tokens, conservative


def _resolve_token(token: str, registry: dict[str, str]) -> str | None:
    """Map a BuildRequires token to a registry package, or None.

    Covers the shapes the repo actually uses: the raw name, the -devel
    subpackage, and pkgconfig()/cmake() wrapped provider names (the hyprwm
    stack's .pc/config names equal the registry names)."""
    name = token
    if name.startswith(("pkgconfig(", "cmake(")) and name.endswith(")"):
        name = name[name.index("(") + 1 : -1]
    for cand in (name, name.removesuffix("-devel")):
        if cand in registry:
            return cand
    lowered = registry.get(name.lower())
    if lowered:
        return lowered
    return registry.get(name.lower().removesuffix("-devel"))


def _validate_batches(pkgs: dict[str, dict]) -> None:
    """Enforce that every package has batch >= 1 + batch of anything it BuildRequires."""
    registry = {n: n for n in pkgs}
    registry.update({n.lower(): n for n in pkgs})
    for name, meta in pkgs.items():
        batch = meta["batch"]
        for token in meta["brs"]:
            provider = _resolve_token(token, registry)
            if provider and provider != name:
                dep_batch = pkgs[provider]["batch"]
                if batch <= dep_batch:
                    die(
                        f"batch violation: [{name}] (batch {batch}) BuildRequires "
                        f"[{provider}] (batch {dep_batch}) -> must be >= {dep_batch + 1}"
                    )


def dependents_closure(pkgs: dict[str, dict], changed: set[str]) -> set[str]:
    """Changed set + everything that (transitively) BuildRequires it.

    The registry is a static build plan: a package only sees another
    package's output through its BuildRequires, so runtime Requires do not
    force rebuilds. Specs with macro-wrapped BuildRequires (unresolvable)
    are seeded whenever a strictly lower batch changed — and their own
    dependents BFS from them — the conservative per-spec form of the old
    batch-floor cascade."""
    registry = {n: n for n in pkgs}
    registry.update({n.lower(): n for n in pkgs})
    reverse: dict[str, set[str]] = {}
    for name, meta in pkgs.items():
        for token in meta["brs"]:
            provider = _resolve_token(token, registry)
            if provider and provider != name:
                reverse.setdefault(provider, set()).add(name)

    def bfs(seeds: set[str], closure: set[str]) -> set[str]:
        frontier = set(seeds)
        while frontier:
            nxt: set[str] = set()
            for name in frontier:
                for dependent in reverse.get(name, ()):
                    if dependent not in closure:
                        closure.add(dependent)
                        nxt.add(dependent)
            frontier = nxt
        return closure

    closure = bfs(changed, set(changed))
    floor = min(pkgs[name]["batch"] for name in changed)
    conservative = {
        n for n, m in pkgs.items()
        if m["conservative"] and n not in closure and m["batch"] > floor
    }
    if conservative:
        # their own dependents must follow them too — BFS from the seeds
        bfs(conservative, closure)
    return closure


def select_since(pkgs: dict[str, dict], rev: str) -> set[str]:
    """Changed packages since REV, plus their BuildRequirements dependents."""
    if not git_rev_exists(rev):
        die(f"--since {rev}: not a valid revision")
    paths = git_lines("diff", "--name-only", f"{rev}..HEAD")
    if not paths:
        print(f"no file changes in {rev}..HEAD", flush=True)
        return set()
    infra = any(p.startswith(pref) for pref in INFRA_PREFIXES for p in paths)
    dirs = {
        p.split("/")[1]
        for p in paths
        if p.startswith("pkgs/") and len(p.split("/")) > 2
    }
    dirs = {d for d in dirs if d in pkgs}
    if infra or not dirs:
        print(
            f"{'infrastructure changed' if infra else 'no registered package changed'}"
            f" in {rev}..HEAD -> all packages",
            flush=True,
        )
        return set(pkgs)
    selected = dependents_closure(pkgs, dirs)
    batches = sorted({pkgs[n]["batch"] for n in selected})
    print(
        f"changed since {rev[:12]}: {', '.join(sorted(dirs))}"
        f" -> rebuilding {len(selected)} packages (batches {batches})",
        flush=True,
    )
    return selected


def entries(names: set[str], pkgs: dict[str, dict], labels: dict[str, str]):
    for name in sorted(names):
        yield {
            # "name" is the registry key — the workflow needs it for artifact
            # names and spec paths
            "name": name,
            "arch": ARCH,
            "labels": {
                "batch": str(pkgs[name]["batch"]),
                "project": pkgs[name]["project"],
                **labels,
            },
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", help="git revision; build everything changed since it")
    ap.add_argument("--only", nargs="*", help="build only these packages")
    ap.add_argument("--batch", nargs="*", type=int, help="build only these batch numbers")
    ap.add_argument(
        "--label", action="append", default=[],
        help="add LABEL=VALUE to every matrix entry (repeatable)",
    )
    ap.add_argument("--list", action="store_true", help="print a table and exit")
    args = ap.parse_args()

    pkgs = load_packages()
    chosen = select(pkgs, args.only, args.batch, args.since)
    if not chosen:
        print("matrix.py: nothing to build")
        return

    if args.list:
        for name in sorted(chosen, key=lambda n: (pkgs[n]["batch"], n)):
            print(f"{pkgs[name]['batch']}  {pkgs[name]['project']:<15}  {name}")
        return

    labels = dict(kv.split("=", 1) for kv in args.label)
    matrix = list(entries(chosen, pkgs, labels))
    batches = sorted({int(e["labels"]["batch"]) for e in matrix})
    print(f"build_matrix={json.dumps(matrix, sort_keys=True)}")
    print(f"batches={','.join(map(str, batches))}")


if __name__ == "__main__":
    main()
