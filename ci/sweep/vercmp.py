"""Pure-Python port of rpm's version comparison (lib/rpmvercmp.c), including
the tilde (~ = pre-release, sorts below everything) and caret (^ = snapshot,
sorts above everything) extensions the snapshot sweeper versions rely on.

The sweeper needs rpmdev-vercmp semantics (rc 11/12 in the old rhai scripts);
this module returns -1/0/1 instead and vercmp_rc() wraps it into rpmdev's
exit-code convention. Pure Python so the sweep has no rpm host dependency.
"""

from __future__ import annotations

import re

_SEGMENT = re.compile(r"[a-zA-Z0-9]+")


def rpmvercmp(a: str, b: str) -> int:
    """Return -1, 0 or 1 comparing a against b the way rpm does."""
    if a == b:
        return 0
    one, two = a, b
    oi = ti = 0
    while oi < len(one) or ti < len(two):
        # skip any non-alphanumeric characters (except ~ and ^)
        while oi < len(one) and not (one[oi].isalnum() or one[oi] in "~^"):
            oi += 1
        while ti < len(two) and not (two[ti].isalnum() or two[ti] in "~^"):
            ti += 1

        if oi < len(one) and one[oi] == "~" or ti < len(two) and two[ti] == "~":
            # tilde sorts before everything, even the end of the string
            if oi >= len(one) or one[oi] != "~":
                return 1
            if ti >= len(two) or two[ti] != "~":
                return -1
            oi += 1
            ti += 1
            continue

        if oi < len(one) and one[oi] == "^" or ti < len(two) and two[ti] == "^":
            # caret sorts after everything: snapshot > release
            if oi >= len(one) or one[oi] != "^":
                return -1
            if ti >= len(two) or two[ti] != "^":
                return 1
            oi += 1
            ti += 1
            continue

        if oi >= len(one) and ti >= len(two):
            return 0
        if oi >= len(one):
            return -1
        if ti >= len(two):
            return 1

        # digit segments beat alpha segments
        one_num = one[oi].isdigit()
        two_num = two[ti].isdigit()
        if one_num and not two_num:
            return 1
        if not one_num and two_num:
            return -1

        m1 = _SEGMENT.match(one, oi)
        m2 = _SEGMENT.match(two, ti)
        seg1, seg2 = m1.group(0), m2.group(0)
        oi, ti = m1.end(), m2.end()
        if one_num:
            seg1, seg2 = seg1.lstrip("0"), seg2.lstrip("0")
            if len(seg1) != len(seg2):
                return 1 if len(seg1) > len(seg2) else -1
        if seg1 != seg2:
            return 1 if seg1 > seg2 else -1
    return 0


def vercmp_rc(old: str, new: str) -> int:
    """rpmdev-vercmp's exit code convention (what the rhai scripts shelled
    out to): 0 = equal, 11 = first argument newer, 12 = second argument
    newer, anything else = error (kept for the hyprland port's checks)."""
    rc = rpmvercmp(old, new)
    if rc == 0:
        return 0
    return 11 if rc > 0 else 12
