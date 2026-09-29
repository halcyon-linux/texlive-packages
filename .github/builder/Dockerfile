FROM registry.fedoraproject.org/fedora-minimal:44

# Build context: the repository root (docker build -f .github/builder/Dockerfile .)
# Rebuilt automatically by .github/workflows/builder-docker.yml whenever
# anything under .github/builder/ changes; copr-build's validate job waits
# for this image's run of the same commit before submitting anything.
#
# The post-anda image: CI runs the version sweeper (ci/sweep/sweep.py),
# generates SRPMs (spectool + rpmbuild -bs) and drives Copr (copr-cli) in
# here. mock is included for LOCAL builds against a `copr-cli mock-config`
# buildroot (see AGENTS.md); CI itself never builds in a chroot. The stock
# /etc/yum.repos.d/fedora*.repo keep providing Fedora 44 + updates.

RUN dnf up -y && \
    dnf swap -y systemd-standalone-sysusers systemd && \
    dnf in -y \
        copr-cli python3 \
        rpmdevtools rpm-build \
        mock \
        git-core jq gh && \
    dnf clean packages dbcache && \
    # mock unmounts chroot mounts via /usr/bin/umount, which Fedora ships
    # setuid-root; on some host kernels (CachyOS 7.x) the setuid loader
    # fails at loader time — mock always runs as root here, so drop the bit
    chmod u-s /usr/bin/umount

CMD ["/bin/bash"]
