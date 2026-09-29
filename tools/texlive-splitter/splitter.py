#!/usr/bin/env python3
"""
TeX Live tlpdb parsing + Arch-style group partition.

The ONLY live surface is parse_tlpdb() + partition(): roll.py fetches the
newest tlnet-archive snapshot's texlive.tlpdb and partitions scheme-full
into the per-group claims that emit_groups.py renders into the 40 specs
(.github/workflows/texlive-update.yml drives it). The old one-spec
monolith generator (install-tl staging + texlive-texmf.spec) is gone.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# collection name -> RPM subpackage suffix (Arch naming)
GROUP_MAP = {
    "basic": "basic",
    "bibtexextra": "bibtexextra",
    "binextra": "binextra",
    "context": "context",
    "fontsextra": "fontsextra",
    "fontsrecommended": "fontsrecommended",
    "fontutils": "fontutils",
    "formatsextra": "formatsextra",
    "games": "games",
    "humanities": "humanities",
    "latex": "latex",
    "latexextra": "latexextra",
    "latexrecommended": "latexrecommended",
    "luatex": "luatex",
    "mathscience": "mathscience",
    "metapost": "metapost",
    "music": "music",
    "pictures": "pictures",
    "plaingeneric": "plaingeneric",
    "pstricks": "pstricks",
    "publishers": "publishers",
    "xetex": "xetex",
    # lang collections keep the lang- prefix: collection-langchinese -> texlive-langchinese
}


def collection_to_group(collection: str) -> str | None:
    """collection-<name> -> texlive-<group>, or None for non-groups (texworks, wintypes)."""
    if not collection.startswith("collection-"):
        return None
    name = collection.removeprefix("collection-")
    if name in ("texworks", "wintools"):
        return None
    # lang collections keep their name: collection-langchinese -> texlive-langchinese
    group = GROUP_MAP.get(name, name)
    return f"texlive-{group}"


def parse_tlpdb(tlpdb_path: Path) -> dict[str, dict]:
    """Parse texlive.tlpdb into {pkgname: {runfiles, depend, execute, shortdesc}}.

    tlpdb layout: a block starts with `name <pkg>`; its fields are top-level
    lines (`runfiles size=N`, `depend x`, ...), and the runfiles file list is
    the run of space-prefixed continuation lines that follows the `runfiles`
    line until the next top-level line.
    """
    packages: dict[str, dict] = {}
    current: dict | None = None
    for raw in tlpdb_path.read_text(errors="replace").splitlines():
        if not raw.strip():
            current = None
            continue
        if raw.startswith(" "):  # continuation line
            if current is None:
                continue
            entry = raw.strip()
            # annotation suffix: details="Package documentation"
            if " details=" in entry:
                entry = entry.split(" details=", 1)[0].strip()
            if current.get("runfiles_cont"):
                current["runfiles"].append(entry)
            elif current.get("docfiles_cont"):
                current["docfiles"].append(entry)
            continue
        if current is not None:
            current["runfiles_cont"] = False
            current["docfiles_cont"] = False
        if raw.startswith("name "):
            name = raw.removeprefix("name ").strip()
            current = packages.setdefault(
                name,
                {"runfiles": [], "docfiles": [], "depend": [], "execute": []},
            )
            continue
        if current is None:
            continue
        if raw.startswith("runfiles"):
            current["runfiles_cont"] = True
        elif raw.startswith("docfiles"):
            current["docfiles_cont"] = True
        elif raw.startswith(("shortdesc ",)):
            current["shortdesc"] = raw.removeprefix("shortdesc ").strip()
        elif raw.startswith("depend "):
            current["depend"].append(raw.removeprefix("depend ").strip())
        elif raw.startswith("execute "):
            current["execute"].append(raw.removeprefix("execute ").strip())
    return packages


def resolve_scheme_collections(tlpdb: dict[str, dict], scheme: str) -> set[str]:
    """All collection-* packages a scheme pulls in, transitively."""
    if scheme not in tlpdb:
        sys.exit(f"FATAL: scheme {scheme!r} not found in the tlpdb")
    collections: set[str] = set()
    stack = [_dep_name(d) for d in tlpdb[scheme]["depend"]]
    while stack:
        dep = stack.pop()
        if dep.startswith("collection-") and dep not in collections:
            collections.add(dep)
            stack.extend(
                _dep_name(d) for d in tlpdb.get(dep, {"depend": []})["depend"]
            )
    return collections


def _dep_name(dep: str, platform: str = "x86_64-linux") -> str:
    """Resolve a tlpdb depend token to the package name for our platform.

    Plain deps may be platform-qualified (synctex.x86_64-linux) or carry the
    ARCH placeholder install-tl substitutes (biber.ARCH)."""
    name = dep.split(":", 1)[0]
    if name.endswith(".ARCH"):
        name = name[: -len(".ARCH")] + "." + platform
    return name


def _claim(groups, target, rf, files_seen):
    if rf in files_seen:
        return False
    files_seen.add(rf)
    groups[target]["runfiles"].append(rf)
    return True


def _collection_topo_order(
    installed: set[str], packages: dict[str, dict]
) -> list[str]:
    """Collections in dependency order (a collection's dependencies before
    it), alphabetical tie-break — so the shared infrastructure lands in
    texlive-basic and each collection claims its own core members (the
    latex package lands in texlive-latex, not wherever a scheme-list
    accident put it). Cycle fallback: remaining collections alphabetically
    (the tlpdb DAG is acyclic in practice)."""
    import heapq

    deps = {c: set() for c in installed}
    rdeps = {c: set() for c in installed}
    for c in installed:
        for d in packages[c]["depend"]:
            d = _dep_name(d)
            if d in deps and d != c:
                deps[c].add(d)
                rdeps[d].add(c)
    ready = [c for c in installed if not deps[c]]
    heapq.heapify(ready)
    out: list[str] = []
    remaining = {c: len(deps[c]) for c in installed}
    while ready:
        c = heapq.heappop(ready)
        out.append(c)
        for r in rdeps[c]:
            remaining[r] -= 1
            if remaining[r] == 0:
                heapq.heappush(ready, r)
    out.extend(sorted(set(installed) - set(out)))
    return out


def partition(packages: dict[str, dict], scheme: str, docs: bool = False) -> dict[str, dict]:
    """Claim-partition of a scheme's texmf-dist files into Arch-style groups.

    Returns {group: {"members": sorted pkgs whose tarball the group must
    fetch, "runfiles": [...], "depend": [other groups], "shortdesc": str}}.
    A member lands in the FIRST collection's group that walks it and only
    if it contributed at least one freshly claimed file (so bin-only
    members are never fetched). Same semantics as the monolith generator.
    """
    installed = resolve_scheme_collections(packages, scheme)
    scheme_data = packages[scheme]
    ordered = _collection_topo_order(installed, packages)

    groups: dict[str, dict] = {}
    if docs:
        groups["texlive-doc"] = {
            "members": [], "runfiles": [], "depend": [], "shortdesc": "documentation",
        }
    claimed: set[str] = set()
    # a shared file (fonts READMEs, doc/info) may be reached through several
    # member packages — rpm rejects duplicate %files entries, so the first
    # attribution wins everywhere
    files_seen: set[str] = set()

    def _group_entry(group: str, collection: str) -> dict:
        return groups.setdefault(
            group,
            {
                "members": [],
                "runfiles": [],
                "depend": [],
                "shortdesc": packages.get(collection, {}).get(
                    "shortdesc", f"{group.removeprefix('texlive-')} collection"
                ),
            },
        )

    def _walk_member(dep: str, target: str) -> bool:
        """Claim one member package's files for target; True if any landed."""
        member = packages[dep]
        contributed = False
        # docfiles are the package's documentation — always texlive-doc
        # (skipped entirely in no-docs builds)
        for rf in member["docfiles"]:
            if not docs:
                continue
            if rf.startswith("RELOC/"):
                rf = "texmf-dist/" + rf[len("RELOC/"):]
            if rf.startswith("texmf-dist/"):
                contributed |= _claim(groups, "texlive-doc", rf, files_seen)
        for rf in member["runfiles"]:
            # RELOC/ marks files whose real root is the texmf tree
            if rf.startswith("RELOC/"):
                rf = "texmf-dist/" + rf[len("RELOC/"):]
            # this set is noarch (BuildArch: noarch); the bin/ tree
            # (texlive-bin sources) is a follow-up split of its own
            if not rf.startswith("texmf-dist/"):
                continue
            if rf.startswith("texmf-dist/doc/"):
                if not docs:
                    continue
                contributed |= _claim(groups, "texlive-doc", rf, files_seen)
            else:
                contributed |= _claim(groups, target, rf, files_seen)
        return contributed

    def _collection_group(pkgname: str) -> str | None:
        group = collection_to_group(pkgname)
        if group is None:
            return None
        _group_entry(group, pkgname)
        for dep in packages[pkgname]["depend"]:
            dep = _dep_name(dep)
            if dep.startswith("collection-"):
                dep_group = collection_to_group(dep)
                if dep_group and dep_group != group:
                    groups[group]["depend"].append(dep_group)
        return group

    # pass 1: a collection's DIRECT (level-1, non-collection) members are
    # its own — claim them for every collection first, so the latex package
    # lands in texlive-latex even when some other collection's closure
    # also reaches it
    for pkgname in ordered:
        group = _collection_group(pkgname)
        if group is None:
            continue
        for dep in packages[pkgname]["depend"]:
            dep = _dep_name(dep)
            if dep.startswith("collection-") or dep not in packages:
                continue
            if _walk_member(dep, group):
                groups[group]["members"].append(dep)
        claimed.update(
            _dep_name(d)
            for d in packages[pkgname]["depend"]
            if not _dep_name(d).startswith("collection-")
            and _dep_name(d) in packages
        )

    # pass 2: transitive closures — packages a collection's members pull in
    # that no level-1 claim took (first claiming collection wins)
    for pkgname in ordered:
        group = _collection_group(pkgname)
        if group is None:
            continue
        stack = [_dep_name(d) for d in packages[pkgname]["depend"]]
        seen: set[str] = set()
        while stack:
            dep = stack.pop()
            if dep in seen or dep.startswith("collection-"):
                continue
            if dep not in packages:
                continue
            seen.add(dep)
            member = packages[dep]
            stack.extend(_dep_name(d) for d in member["depend"])
            if _walk_member(dep, group):
                groups[group]["members"].append(dep)
        claimed.update(seen)

    # scheme-level plain deps no collection claims (e.g. scheme-small's
    # babel-* set): their files go to texlive-basic, the root group
    for dep in scheme_data["depend"]:
        dep = _dep_name(dep)
        if dep.startswith("collection-") or dep in claimed or dep not in packages:
            continue
        _group_entry("texlive-basic", "collection-basic")
        claimed.add(dep)
        if _walk_member(dep, "texlive-basic"):
            groups["texlive-basic"]["members"].append(dep)

    for entry in groups.values():
        entry["members"] = sorted(entry["members"])
        entry["depend"] = sorted(set(entry["depend"]))
        entry["runfiles"].sort()
    return groups


