# AGENTS.md

RPM package repo for **TeX Live rolling groups** — one of the six halcyon
group repositories (base-pkgs, cli-tools, applications, fonts,
texlive-packages, linux-p03; each has its own GitHub repo under
[halcyon-linux](https://github.com/orgs/halcyon-linux/repositories) and its
own Copr project under `aahsnr-work`). Built on Fedora Copr
([aahsnr-work/texlive-packages](https://copr.fedorainfracloud.org/coprs/aahsnr-work/texlive-packages/),
chroots fedora-44-x86_64 + fedora-45-x86_64) in CI. Consumers:

```
dnf copr enable aahsnr-work/texlive-packages fedora-44
```

The Arch-style group split of TeX Live scheme-full: the texmf-dist data
tree only, no docs — plus `texlive-bin`, upstream's prebuilt x86_64-linux
engine bundle from the same dated snapshot. Groups and engines install
under one self-contained `/usr/lib/texlive/<year>/` root that kpathsea
resolves relative to the binary (SELFAUTOPARENT), so nothing here
interacts with Fedora's texlive packaging (different paths, different
package names).

**Markdown discipline**: do NOT read, use, or act on `TODO.md`, `notes/` or
any other markdown file unless the user explicitly instructs you to utilize
that particular markdown file for the task at hand. Code, the registry
(`ci/packages.toml`), and the workflows are the source of truth.

## Commands

There is no test suite; verification = the Copr build going green.

```bash
python3 ci/matrix.py --list          # registry + build plan (validate job)

# force a roll locally (fetches the tlpdb; writes only changed specs)
python3 tools/texlive-splitter/roll.py [--snapshot YYYYMMDD]

# a mock buildroot identical to Copr's
copr-cli mock-config aahsnr-work/texlive-packages fedora-44-x86_64 > /tmp/copr.cfg
mock -r /tmp/copr.cfg <srpm>
```

## Non-obvious rules

- **All 20 specs are GENERATED** (18 `texlive-<group>` collection groups +
  `texlive-bin` + `texlive-meta`) — rewritten wholesale per tlnet snapshot
  by the biweekly roll (`.github/workflows/texlive-update.yml`, Wednesdays
  04:17 UTC on even ISO weeks; manual dispatch always) running
  `tools/texlive-splitter/roll.py` against the newest
  `texlive.info/tlnet-archive` daily snapshot. **Never hand-edit them.**
  They carry no `[pkg.updates]` tables and are never swept by update.yml.
- Group specs have **no URL `Source` entries** — their `%build` wgets each
  member tarball from the dated snapshot (`wget` is a BuildRequire; the
  network is on in Copr builds). spectool fetches nothing for them.
  `texlive-bin` fetches `archive/x86_64-linux.tar.xz` + `tlpkg/texlive.tlpdb.xz`
  from the same snapshot the same way.
- Inter-group Requires are version-pinned to the snapshot so dnf keeps all
  installed groups on one snapshot; `texlive-meta` derives its Requires
  from the surviving groups and pins `texlive-bin` too; `texlive-bin`
  pins `texlive-basic` (its data floor). Everything installs under
  `/usr/lib/texlive/<year>/` — the `<year>` and the tree layout come from
  the `%_tl_root` macro in the generated specs.
- **`EXCLUDED_GROUPS` in roll.py** keeps the trimmed groups (the language
  packs, fontsextra, games, music, context) from ever being re-added by a
  roll. Extend that frozenset, never the spec set, to drop more.
- Specs are written only when their bytes change (a no-move snapshot is a
  no-op roll); a bad roll never replaces the published set — revert the
  roll commit to go back.
- Batches here are trivial (every package is batch 5 — one wave), but the
  registry stays the build selection: a group without a
  `ci/packages.toml` entry is never built. `registry_sync` in roll.py
  rewrites the texlive entries per snapshot; everything else in
  `ci/packages.toml` is hand-off-limits.
- Push cascades are gated on the CASCADE_ENABLED repository variable
  (kill-switch; `workflow_dispatch` bypasses it). A push to `main`
  rebuilds the changed groups (one wave).
- `repo/` carries the consumer drop-ins for **all six** group repos —
  repoclosure installs all of them and checks THIS repo's project against
  the union, exactly what a halcyon-image consumer sees.
- **CI authentication**: the `COPR_CLICONF` GitHub secret drives every
  copr-cli step. The Copr API token expires — a wave of 401s means:
  regenerate at <https://copr.fedorainfracloud.org/api/>, re-set the
  secret, re-run.
