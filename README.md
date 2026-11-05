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
  the published Copr repo against Fedora 44/45 (+ Terra and the
  lionheartp bootstrap repo).
- `builder-docker.yml` — builds this repo's own CI job image and pushes
  it to `ghcr.io/halcyon-linux/texlive-packages-builder:f44` (consumed by this
  repo's build and sweep jobs).
- `texlive-update.yml` — the biweekly roll (Wednesdays on even ISO weeks):
  rewrites all 20 specs from the newest tlnet snapshot and pushes, which
  cascades the batch-5 rebuild. Manual dispatch forces a snapshot.


## Running and cancelling workflows

### Run a workflow manually

In the GitHub UI: **Actions** → the workflow → **Run workflow** → branch `main` →
fill the inputs → **Run workflow**. From the CLI:

```bash
gh workflow run <file> -R halcyon-linux/texlive-packages --ref main -f <input>=<value>
```

Inputs (leave empty for the default):

- `copr-build.yml` — `only`: space-separated packages to build (empty = all), e.g. `-f only="<pkg> <pkg>"`. Bypasses the `CASCADE_ENABLED` gate.
- `repoclosure.yml`, `builder-docker.yml` — no inputs.
- `texlive-update.yml` — `snapshot`: `YYYYMMDD` to force a tlnet snapshot date (empty = newest). Manual dispatch runs even in off weeks.

### Force-cancel a workflow run

1. Find the run ID: `gh run list -R halcyon-linux/texlive-packages --workflow <file>`.
2. Cancel it: `gh run cancel <run-id> -R halcyon-linux/texlive-packages`, or **Cancel workflow**
   on the run page.
3. If it stays in *Cancelling* because a job or runner is hung, use **Force cancel**
   on the run page, or:

   ```bash
   gh api -X POST repos/halcyon-linux/texlive-packages/actions/runs/<run-id>/force-cancel
   ```

A force cancel ends the run without waiting for its jobs. It does not stop Copr
builds that were already submitted; cancel those on the Copr project page.

**Caution:** `texlive-update.yml` pushes the rolled specs before its final step dispatches `copr-build`. If it is cancelled after that push, dispatch `copr-build.yml` manually.

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
