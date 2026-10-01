# texlive-packages

TeX Live rolling groups (tlnet snapshots), without docs — plus the matching
engine bundle (`texlive-bin`: upstream's prebuilt x86_64-linux binaries from
the same snapshot, self-contained under `/usr/lib/texlive/<year>/`).
Fedora 44 (+45) RPMs built on
[Copr](https://copr.fedorainfracloud.org/coprs/aahsnr-work/texlive-packages/).

```
dnf copr enable aahsnr-work/texlive-packages fedora-44
```

One of the six halcyon group repositories: base-pkgs · cli-tools ·
applications · fonts · texlive-packages · linux-p03.

## CI

- `copr-build.yml` — a push to `main` rebuilds changed packages plus every
  higher batch, wave-by-wave. Gated on the `CASCADE_ENABLED` repository
  variable (kill-switch; `workflow_dispatch` bypasses it). PRs validate
  only.
- `repoclosure.yml` — nightly (05:43 UTC) + post-cascade closure check of

- `builder-docker.yml` — builds this repo's own CI job image and pushes
  it to `ghcr.io/halcyon-linux/texlive-packages-builder:f44` (consumed by this
  repo's build and sweep jobs).
  the published Copr repo against Fedora 44/45 (+ Terra and the
  lionheartp bootstrap repo).
- `texlive-update.yml` — the biweekly roll (Wednesdays on even ISO weeks):
  rewrites all 20 specs from the newest tlnet snapshot and pushes, which
  cascades the batch-5 rebuild. Manual dispatch forces a snapshot.


## Layout

```
ci/packages.toml    the registry: build selection + sweep-feed config
ci/matrix.py        batch/wave build plan (validate job runs it)
ci/sweep/           the version sweeper + custom feeds
pkgs/<pkg>/         spec + local sources
repo/               consumer .repo drop-ins (all six group repos)
templates/          starting points for new specs
```

See [AGENTS.md](AGENTS.md) for the conventions before touching specs.
