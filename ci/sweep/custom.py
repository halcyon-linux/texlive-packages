"""The custom version feeds — 1:1 Python ports of the 11 update.rhai scripts
that carry real logic (the other 35 are one-liner gh()/gh_tag() sweeps driven
from ci/packages.toml).

Two intentional deviations from the rhai, both bug fixes (the rhai versions
never ran successfully — see TODO.md's sweep-verification note):

- hyprland: the rhai referenced an undefined `old_tag` (it would abort) and
  rewrote the commit date with a whole-spec string substitution (which
  corrupted the spec's %global commit_date line by colliding with changelog
  text). The port compares against the spec's version base and rewrites the
  %global line in place.
- obsidian: the rhai used `#` lines as comments — a rhai syntax error, so the
  script never parsed. The port implements the documented intent.
"""

from __future__ import annotations

import re
import subprocess
import urllib.request
from pathlib import Path

import feeds
from spec import SpecFile


def _parse_int(value: str | None) -> int:
    try:
        return int(value or "")
    except ValueError:
        return 0


def _aur_pkgver(pkg: str) -> str:
    """pkgver of an AUR package (pkgrel stripped) — for vendor apps whose
    only version tracker is the AUR maintainer."""
    data = feeds.fetch_json(
        "https://aur.archlinux.org/rpc/v5/info?arg%5B%5D=" + pkg)
    results = data.get("results") or []
    aur_version = results[0]["Version"] if results else ""
    if not aur_version:
        raise feeds.FeedError(f"{pkg}: the AUR RPC returned no version")
    return aur_version.rsplit("-", 1)[0]


def custom_antigravity_cli(spec: SpecFile, pkg_dir: Path) -> None:
    # the AUR pkgver carries the full "1.2.10_4751581200121856" pair; the
    # spec keeps the dotted version and pins the build id in the cli_build
    # global that the Source URL interpolates
    pkgver = _aur_pkgver("antigravity-cli")
    m = re.fullmatch(r"([0-9.]+)_(\d+)", pkgver)
    if not m:
        raise feeds.FeedError(f"antigravity-cli: unexpected AUR pkgver {pkgver!r}")
    spec.set_version(m.group(1))
    spec.set_global("cli_build", m.group(2))


def custom_antigravity_ide(spec: SpecFile, pkg_dir: Path) -> None:
    # no first-party feed — the download URL embeds a per-release execution
    # id Google does not expose anywhere — so mirror the AUR maintainer's
    # pins: pkgver for the version, the PKGBUILD's _build for the id
    pkgver = _aur_pkgver("antigravity-ide")
    pkgtxt = feeds.fetch_text(
        "https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=antigravity-ide")
    spec.set_version(pkgver)
    spec.set_global("ide_build", feeds.find_group(r"(?m)^_build=(\d+)", pkgtxt))


def custom_bitwarden(spec: SpecFile, pkg_dir: Path) -> None:
    # bitwarden/clients cuts other tags too; the raw-text scan picks the first
    # desktop-v tag of the releases list (GitHub orders newest-first).
    text = feeds.fetch_text(
        "https://api.github.com/repos/bitwarden/clients/releases?per_page=30"
    )
    tag = feeds.find_group(r'"tag_name":"desktop-v([^"]+)"', text)
    spec.set_version(tag)


def custom_bun(spec: SpecFile, pkg_dir: Path) -> None:
    # bun publishes no GitHub release feed; the LATEST file is the official
    # channel. Version tracks the -baseline build of the release zip.
    spec.set_version(feeds.fetch_text(
        "https://raw.githubusercontent.com/oven-sh/bun/main/LATEST").strip())


def custom_gnuplot(spec: SpecFile, pkg_dir: Path) -> None:
    # gnuplot publishes on SourceForge, not GitHub; the linux filename of
    # best_release.json carries the version.
    data = feeds.fetch_json("https://sourceforge.net/projects/gnuplot/best_release.json")
    filename = data["platform_releases"]["linux"]["filename"]
    spec.set_version(feeds.find_group(r"gnuplot-([0-9.]+)\.tar\.gz$", filename))


def custom_hyprland(spec: SpecFile, pkg_dir: Path) -> None:
    # hyprland tracks the newest upstream RELEASE only (maintainer: no git
    # snapshots). Version is the plain release tag; the official
    # source-vX.Y.Z.tar.gz asset bundles every subproject, so there is
    # nothing else to pin — a new release is just a version bump.
    repo = "hyprwm/Hyprland"
    tag = feeds.github_release_tag(repo)
    if not tag:
        raise feeds.FeedError("hyprland: no release found upstream")
    spec.set_version(tag.removeprefix("v"))


def custom_hyprland_git(spec: SpecFile, pkg_dir: Path) -> None:
    # hyprland-git tracks the hyprwm/Hyprland main-branch tip:
    # records the commit, its commit date, total commit count, and
    # the SHAs of the bundled hyprland-protocols and udis86 submodules.
    # A new tag resets bumpver; any revision change bumps bumpver.
    repo = "hyprwm/Hyprland"

    old_commit = spec.get_global("hyprland_commit") or ""
    new_commit = feeds.github_commit(repo)

    proc = subprocess.run(
        ["rpmspec", "-q", "--qf", "%{version}", str(pkg_dir / spec.path.name)],
        check=True, capture_output=True, text=True, cwd=pkg_dir,
    )
    old_version = proc.stdout.strip()
    old_base = re.sub(r"\^.*", "", old_version)

    new_tag = feeds.github_latest_tag(repo) or old_base

    protocols_data = feeds.fetch_json(
        f"https://api.github.com/repos/{repo}/contents/subprojects/hyprland-protocols?ref={new_commit}"
    )
    new_protocols = protocols_data["sha"]
    old_protocols = spec.get_global("protocols_commit") or ""

    udis86_data = feeds.fetch_json(
        f"https://api.github.com/repos/{repo}/contents/subprojects/udis86?ref={new_commit}"
    )
    new_udis86 = udis86_data["sha"]
    old_udis86 = spec.get_global("udis86_commit") or ""

    commit_data = feeds.fetch_json(
        f"https://api.github.com/repos/{repo}/commits/{new_commit}"
    )
    date_str = commit_data["commit"]["author"]["date"]
    date_proc = subprocess.run(
        ["date", "-u", "-d", date_str, "+%a %b %d %T %Y"],
        check=True, capture_output=True, text=True,
    )
    new_date = date_proc.stdout.strip()
    old_date = spec.get_global("commit_date") or ""

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits?per_page=1&sha={new_commit}",
        headers=feeds._headers_for("https://api.github.com"),
    )
    with feeds._open(req) as resp:
        link_header = resp.headers.get("Link", "")
    new_commits = feeds.find_group(r'page=(\d+)>;\s*rel="last"', link_header)

    from vercmp import vercmp_rc
    ec = vercmp_rc(old_version, new_tag)

    bumpver_str = spec.get_global("bumpver") or "0"
    bump = int(bumpver_str)

    tag_changed = False
    if ec == 12:
        # new upstream tag: reset snapshot counter and update Version line
        bump = 0
        spec.set_global("bumpver", "0")
        spec.text = re.sub(
            r"(?m)^(Version:[ \t]*)[0-9.]+",
            lambda m: f"{m.group(1)}{new_tag}",
            spec.text,
            count=1,
        )
        tag_changed = True

    changed = (
        old_commit != new_commit
        or old_protocols != new_protocols
        or old_udis86 != new_udis86
        or old_date != new_date
        or tag_changed
    )

    if changed:
        spec.set_global("commits_count", new_commits)
        spec.set_global("commit_date", new_date)
        spec.set_global("hyprland_commit", new_commit)
        spec.set_global("protocols_commit", new_protocols)
        spec.set_global("udis86_commit", new_udis86)
        spec.set_global("bumpver", str(bump + 1))


def custom_kilo(spec: SpecFile, pkg_dir: Path) -> None:
    # the GitHub tag space is polluted by jetbrains/* tags; the npm dist-tag
    # is the channel the CLI's own updater follows
    data = feeds.fetch_json("https://registry.npmjs.org/@kilocode/cli/latest")
    spec.set_version(data["version"])


def custom_marksman(spec: SpecFile, pkg_dir: Path) -> None:
    # the raw date tag goes into %markstag for the source URLs; the version is
    # the tag with dashes converted to dots (RPM versions cannot carry dashes)
    tag = feeds.github_release_tag("artempyanykh/marksman")
    spec.set_global("markstag", tag)
    spec.set_version(tag.replace("-", "."))


def custom_noctalia_greeter_git(spec: SpecFile, pkg_dir: Path) -> None:
    # tracks the noctalia-dev/noctalia-greeter branch tip, not a release: the
    # spec pins the commit in a %global and keeps the last tag as the version
    # base with a ^N snapshot counter — a new commit under the same tag bumps
    # the counter, a new tag resets it. The Release: line is left alone.
    repo = "noctalia-dev/noctalia-greeter"
    old_commit = feeds.find_group(
        r"(?m)^%global[ \t]+commit[ \t]+(\S+)", spec.text)
    new_commit = feeds.github_commit(repo)
    # old_version as rpmdev sees it (the Version line carries %{shortcommit})
    proc = subprocess.run(
        ["rpmspec", "-q", "--qf", "%{version}", str(pkg_dir / spec.path.name)],
        check=True, capture_output=True, text=True, cwd=pkg_dir,
    )
    old_version = proc.stdout.strip()
    old_base = re.sub(r"\^.*", "", old_version)
    # the version base is unprefixed (github_latest_tag strips the v; the
    # Sources carry commit SHAs so no URL is affected)
    tag_raw = feeds.github_latest_tag(repo)
    new_tag = old_base if not tag_raw else tag_raw.replace("-", "~")

    from vercmp import vercmp_rc

    ec = vercmp_rc(old_version, new_tag)
    if old_commit != new_commit or ec == 12:
        if ec == 12:
            # newer tag: reset the snapshot counter
            spec.set_snapshot_version(new_tag, None)
        else:
            # same version base: bump the snapshot counter
            m = re.search(r"(?m)^Version:[ \t]*[^ \t]*\^(\d+)", spec.text)
            counter = int(m.group(1)) + 1 if m else 1
            spec.set_snapshot_version(old_base, counter)
        spec.set_global("commit", new_commit)


def custom_noctalia_git(spec: SpecFile, pkg_dir: Path) -> None:
    # tracks the noctalia-dev/noctalia branch tip, not a release: the
    # spec pins the commit in a %global and keeps the last tag as the version
    # base with a ^N snapshot counter — a new commit under the same tag bumps
    # the counter, a new tag resets it. The Release: line is left alone.
    repo = "noctalia-dev/noctalia"
    old_commit = feeds.find_group(
        r"(?m)^%global[ \t]+commit[ \t]+(\S+)", spec.text)
    new_commit = feeds.github_commit(repo)
    # old_version as rpmdev sees it (the Version line carries %{shortcommit})
    proc = subprocess.run(
        ["rpmspec", "-q", "--qf", "%{version}", str(pkg_dir / spec.path.name)],
        check=True, capture_output=True, text=True, cwd=pkg_dir,
    )
    old_version = proc.stdout.strip()
    old_base = re.sub(r"\^.*", "", old_version)
    # the version base is unprefixed (github_latest_tag strips the v; the
    # Sources carry commit SHAs so no URL is affected)
    tag_raw = feeds.github_latest_tag(repo)
    new_tag = old_base if not tag_raw else tag_raw.replace("-", "~")

    from vercmp import vercmp_rc

    ec = vercmp_rc(old_version, new_tag)
    if old_commit != new_commit or ec == 12:
        if ec == 12:
            # newer tag: reset the snapshot counter
            spec.set_snapshot_version(new_tag, None)
        else:
            # same version base: bump the snapshot counter
            m = re.search(r"(?m)^Version:[ \t]*[^ \t]*\^(\d+)", spec.text)
            counter = int(m.group(1)) + 1 if m else 1
            spec.set_snapshot_version(old_base, counter)
        spec.set_global("commit", new_commit)


def custom_obsidian(spec: SpecFile, pkg_dir: Path) -> None:
    # the newest obsidianmd/obsidian-releases release that ships the desktop
    # linux tarball; the release's sha256 asset digest is pinned in %global
    # digest for the %prep checksum ('none' skips the check when upstream
    # publishes no digest)
    text = feeds.fetch_text(
        "https://api.github.com/repos/obsidianmd/obsidian-releases/releases?per_page=30"
    )
    spec.set_version(feeds.find_group(r'"name":"obsidian-([0-9.]+)\.tar\.gz"', text))
    try:
        digest = feeds.find_group(
            r'"name":"obsidian-[0-9.]+\.tar\.gz".*?"digest":"sha256:([0-9a-f]{64})"',
            text, dotall=True,
        )
    except feeds.FeedError:
        digest = "none"
    spec.set_global("digest", digest)


def custom_opencode(spec: SpecFile, pkg_dir: Path) -> None:
    # the v2 line ships through opencode.ai's own update API, not GitHub
    # releases; the npm scope carrying the cli-linux-x64 tarball is resolved
    # from the same endpoint and Source0 is rewritten when the scope moves.
    meta = feeds.fetch_text("https://opencode.ai/update/api/latest/cli/npm")
    spec.set_version(feeds.find_group(r'"version":"([^"]+)"', meta))
    scope = "@opencode"
    m = re.search(r'"package":"([^"]*)/cli"', meta)
    if m:
        scope = m.group(1)
    if scope != "@opencode":
        spec.set_source(
            0,
            "https://registry.npmjs.org/" + scope
            + "%2Fcli-linux-x64/-/cli-linux-x64-%{version}.tgz",
        )


def custom_opencode_desktop(spec: SpecFile, pkg_dir: Path) -> None:
    # the desktop app ships through opencode.ai's own stable download
    # endpoint, which 302s to the versioned RPM; the version is read off the
    # redirect target (no first-party release feed)
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    req = urllib.request.Request(
        "https://opencode.ai/download/stable/linux-x64-rpm",
        headers={"User-Agent": "halcyon-packages-sweeper"},
    )
    feeds.validate_url(req.full_url)
    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
    else:
        raise feeds.FeedError(
            "opencode-desktop: the stable endpoint did not redirect")
    spec.set_version(feeds.find_group(r"/files/bin/([0-9.]+)/", location))


def custom_kernel_p03(spec: SpecFile, pkg_dir: Path) -> None:
    # kernel-p03 tracks the latest GitHub release tag of CatPieLeaf/linux-p03.
    # Each release bumps _tag_ver (e.g. p03.32), and typically also updates
    # _koji_nvr (the Fedora Koji kernel NVR to build against), _suse_nvr
    # (the openSUSE equivalent), and _nv_ver (the NVIDIA open-gpu-kernel-modules
    # version). Rather than guessing these from the tag alone, we fetch upstream's
    # own specfile at the release ref and lift the values from it directly — the
    # same three globals that Copr used when the upstream Copr project succeeded.
    repo = "CatPieLeaf/linux-p03"
    new_tag = feeds.github_release_tag(repo)
    if not new_tag:
        raise feeds.FeedError("kernel-p03: no release found upstream")

    old_tag = spec.get_global("_tag_ver") or ""
    if old_tag == new_tag:
        return

    # Fetch upstream's specfile at the new release tag to extract the NVR pins.
    upstream_spec_url = (
        f"https://raw.githubusercontent.com/{repo}/refs/tags/{new_tag}"
        "/sources/kernel-p03/kernel-p03.spec"
    )
    upstream_text = feeds.fetch_text(upstream_spec_url)

    m_koji = re.search(r"(?m)^%(?:global|define)[ \t]+_koji_nvr[ \t]+(\S+)", upstream_text)
    m_suse = re.search(r"(?m)^%(?:global|define)[ \t]+_suse_nvr[ \t]+(\S+)", upstream_text)
    m_nv   = re.search(r"(?m)^%(?:global|define)[ \t]+_nv_ver[ \t]+(\S+)", upstream_text)
    if not m_koji:
        raise feeds.FeedError(
            f"kernel-p03: could not parse _koji_nvr from upstream spec at {new_tag}")

    new_koji = m_koji.group(1)
    # Derive the RPM version: kernel-7.2.6-300.fc45 -> 7.2.6
    kver_m = re.search(r"^kernel-([0-9]+\.[0-9]+\.[0-9]+)", new_koji)
    if not kver_m:
        raise feeds.FeedError(
            f"kernel-p03: unexpected _koji_nvr format: {new_koji!r}")
    new_kver = kver_m.group(1)

    # Extract build number from new_tag (p03.N -> N)
    buildnum_m = re.search(r"^p03\.(\d+)$", new_tag)
    if not buildnum_m:
        raise feeds.FeedError(
            f"kernel-p03: unexpected tag format: {new_tag!r}")

    # Update the three NVR globals first (before set_version resets Release)
    spec.set_global("_tag_ver",  new_tag)
    spec.set_global("_koji_nvr", new_koji)
    if m_suse:
        spec.set_global("_suse_nvr", m_suse.group(1))
    if m_nv:
        spec.set_global("_nv_ver", m_nv.group(1))

    # The RPM version is <kver>.p03.<N>; set_version drives the Release reset.
    new_version = f"{new_kver}.p03.{buildnum_m.group(1)}"
    spec.set_version(new_version)


def custom_qt6ct(spec: SpecFile, pkg_dir: Path) -> None:
    # GitLab releases of opencode.net/trialuser/qt6ct (the canonical repo; the
    # GitHub mirror lags behind). The spec pins the release commit: the
    # Source0 archive URL follows the %global commit.
    releases = feeds.fetch_json(
        "https://www.opencode.net/api/v4/projects/5459/releases?per_page=1")
    release = releases[0]
    spec.set_version(release["tag_name"])
    spec.set_global("commit", release["commit"]["id"])


def custom_ticktick(spec: SpecFile, pkg_dir: Path) -> None:
    # TickTick publishes no first-party version feed, but the AUR package
    # tracks it: the AUR maintainer bumps pkgver on every upstream release.
    # Version is pkgver-pkgrel — keep pkgver only.
    data = feeds.fetch_json("https://aur.archlinux.org/rpc/v5/info?arg%5B%5D=ticktick")
    results = data.get("results") or []
    aur_version = results[0]["Version"] if results else ""
    if not aur_version:
        raise feeds.FeedError("ticktick: the AUR RPC returned no version")
    spec.set_version(re.sub(r"-[^-]*$", "", aur_version))


def custom_xdg_desktop_portal_hyprland(spec: SpecFile, pkg_dir: Path) -> None:
    # the portal release plus the sdbus-c++ release the bundled Source1
    # tarball is pinned to. The global stays unprefixed — the Source template
    # carries the v itself (the rhai wrote the raw v-tag and would have made
    # the URL vv2.3.1; it never ran to catch it).
    spec.set_version(feeds.github_release_tag("hyprwm/xdg-desktop-portal-hyprland"))
    spec.set_global(
        "sdbus_version",
        feeds.github_release_tag("Kistler-Group/sdbus-cpp").removeprefix("v"),
    )


def custom_zotero(spec: SpecFile, pkg_dir: Path) -> None:
    # zotero's git tags can outrun its release artifacts: the 10.0.4 tag has
    # no linux tarball on download.zotero.org (S3 answers 403 for the absent
    # key) and the 2026-09-27 batch-0 submit died in spectool on exactly
    # that. Version is the newest tag whose official linux tarball really
    # downloads — probe newest-first with HEAD, skipping 403/404 (yanked or
    # never-published) and erroring on anything else, so a dead network or a
    # moved host never silently downgrades the package.
    import urllib.error
    from functools import cmp_to_key

    from vercmp import rpmvercmp

    versions = sorted(
        {t.removeprefix("v") for t in feeds.github_tag_names("zotero/zotero")},
        key=cmp_to_key(rpmvercmp),
        reverse=True,
    )
    for version in versions:
        url = (
            "https://download.zotero.org/client/release/"
            + version
            + "/Zotero-"
            + version
            + "_linux-x86_64.tar.xz"
        )
        feeds.validate_url(url)
        req = urllib.request.Request(
            url, method="HEAD", headers=feeds._headers_for(url)
        )
        try:
            feeds._open(req)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                continue
            raise feeds.FeedError(
                f"zotero: HTTP {exc.code} probing {url}") from exc
        spec.set_version(version)
        return
    raise feeds.FeedError("zotero: no tag carries a published linux tarball")


def custom_emacs_pgtk(spec: SpecFile, pkg_dir: Path) -> None:
    # GNU Emacs publishes on ftp.gnu.org only; the emacs-mirror/emacs GitHub
    # mirror tags releases as emacs-<version> and pretests as
    # emacs-<version>.<90+> (pretest tarballs land on alpha.gnu.org, not
    # ftp.gnu.org). Rank the numeric tags newest-first and HEAD-probe the
    # ftp.gnu.org tarball zotero-style so a pretest tag never bumps the spec.
    import urllib.error
    from functools import cmp_to_key

    from vercmp import rpmvercmp

    versions = sorted(
        {
            v
            for v in (
                t.removeprefix("emacs-")
                for t in feeds.github_tag_names("emacs-mirror/emacs")
            )
            if re.fullmatch(r"[0-9][0-9.]*", v)
        },
        key=cmp_to_key(rpmvercmp),
        reverse=True,
    )
    for version in versions:
        url = f"https://ftp.gnu.org/gnu/emacs/emacs-{version}.tar.xz"
        feeds.validate_url(url)
        req = urllib.request.Request(
            url, method="HEAD", headers=feeds._headers_for(url)
        )
        try:
            feeds._open(req)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                continue
            raise feeds.FeedError(
                f"emacs-pgtk: HTTP {exc.code} probing {url}") from exc
        spec.set_version(version)
        return
    raise feeds.FeedError("emacs-pgtk: no tag carries a published tarball")


def custom_noto_color_emoji(spec: SpecFile, pkg_dir: Path) -> None:
    # googlefonts/noto-emoji attaches no font assets to its releases and its
    # tag trees carry no built font — the official current build lives in the
    # google/fonts repo (ofl/notocoloremoji). Version is the date of the
    # newest commit that touched the font file, and the same commit is
    # pinned into the spec's noto_commit global so Source0/Source1 always
    # fetch exactly the shipped build.
    font_path = "ofl/notocoloremoji/NotoColorEmoji-Regular.ttf"
    commit = feeds.fetch_json(
        "https://api.github.com/repos/google/fonts/commits?path="
        + font_path
        + "&per_page=1"
    )[0]
    spec.set_global("noto_commit", commit["sha"][:12])
    spec.set_version(commit["commit"]["committer"]["date"][:10].replace("-", ""))


def custom_private_internet_access(spec: SpecFile, pkg_dir: Path) -> None:
    # PIA publishes no version feed at all: no GitHub release assets and a 403
    # on the installers directory listing — the AUR piavpn-bin maintainer does
    # the discovery work, so mirror their pins: pkgver for the version, the
    # PKGBUILD's build_number for the pia_build global the Source URL
    # interpolates.
    pkgtxt = feeds.fetch_text(
        "https://aur.archlinux.org/cgit/aur.git/plain/PKGBUILD?h=piavpn-bin")
    spec.set_version(feeds.find_group(r"(?m)^pkgver=(\S+)", pkgtxt))
    spec.set_global("pia_build", feeds.find_group(r"(?m)^build_number=(\S+)", pkgtxt))


def custom_mullvad_vpn(spec: SpecFile, pkg_dir: Path) -> None:
    # mullvad.net's Linux download page is server-rendered and links the
    # current x86_64 RPM on their GitHub releases — the same page upstream's
    # own install instructions point at.
    html = feeds.fetch_text("https://mullvad.net/en/download/vpn/linux")
    spec.set_version(feeds.find_group(r"MullvadVPN-([0-9.]+)_x86_64\.rpm", html))


def _proton_rpm_version(ghrepo: str, rpm_name: str, srcarch: str, spec: SpecFile) -> None:
    # ProtonVPN/<ghrepo> tags newest-first, HEAD-probing the official
    # repo.protonvpn.com RPM URL so a tag whose RPM build has not landed yet
    # (Proton's RPM builds can lag their tags) never bumps the spec. The
    # official repo 403s the sweeper's default UA and answers the browser one.
    import urllib.error

    fc = spec.get_global("pv_fc") or "44"
    rel = spec.get_global("pv_rel") or "1"
    tags = [t.removeprefix("v") for t in feeds.github_tag_names(f"ProtonVPN/{ghrepo}")]
    for version in tags:
        if not re.fullmatch(r"[0-9][0-9.]*", version):
            continue
        url = (
            f"https://repo.protonvpn.com/fedora-{fc}-stable/"
            f"{rpm_name}/{rpm_name}-{version}-{rel}.fc{fc}.{srcarch}.rpm"
        )
        feeds.validate_url(url)
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        try:
            feeds._open(req)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                continue
            raise feeds.FeedError(
                f"{rpm_name}: HTTP {exc.code} probing {url}") from exc
        spec.set_version(version)
        return
    raise feeds.FeedError(f"{rpm_name}: no tag carries a published RPM yet")


def custom_python3_proton_core(spec: SpecFile, pkg_dir: Path) -> None:
    _proton_rpm_version("python-proton-core", "python3-proton-core", "noarch", spec)


def custom_python3_proton_keyring_linux(spec: SpecFile, pkg_dir: Path) -> None:
    _proton_rpm_version("python-proton-keyring-linux",
                        "python3-proton-keyring-linux", "noarch", spec)


def custom_python3_proton_vpn_api_core(spec: SpecFile, pkg_dir: Path) -> None:
    _proton_rpm_version("python-proton-vpn-api-core",
                        "python3-proton-vpn-api-core", "x86_64", spec)


def custom_proton_vpn_daemon(spec: SpecFile, pkg_dir: Path) -> None:
    _proton_rpm_version("proton-vpn-daemon", "proton-vpn-daemon", "noarch", spec)


def custom_proton_vpn_gtk_app(spec: SpecFile, pkg_dir: Path) -> None:
    _proton_rpm_version("proton-vpn-gtk-app", "proton-vpn-gtk-app", "noarch", spec)
