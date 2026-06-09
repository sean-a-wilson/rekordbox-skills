#!/usr/bin/env python3
"""Render a dedupe manifest for review.

Two views, both read-only:

  * default (overview) -- THREE tables, one per group kind, each listing EVERY
    copy in the group with its quality and the playlists it lives in:
      1. Exact dupes          -- same recording for sure; decided in BULK.
      2. Looks like the same  -- almost certainly one take; reviewed per group.
      3. Different versions    -- genuinely different takes; reviewed per group.

  * --group N -- a detail card for a single group, for the one-at-a-time review
    of tables 2 & 3: every copy side by side with Time/BPM/bitrate, the match
    type + version note, the recommended keeper, the current decision, and the
    exact decide.py commands to confirm or change it.

Usage:
    python3 show_manifest.py manifest.json                # three overview tables
    python3 show_manifest.py manifest.json --group 3      # one group's card
    python3 show_manifest.py manifest.json --format tsv   # tab-separated overview
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _mmss(seconds: int) -> str:
    if not seconds:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _bpm(b) -> str:
    return f"{b:.1f}" if b else "?"


def track_label(t: dict) -> str:
    """e.g. 'G.Q. - Disco Nights (Rock Freak) [wav 1411k · 5:51 · 122.8]'."""
    return (f"{t['artist']} - {t['title']} "
            f"[{t['ext']} {t['bitrate']}k · {_mmss(t.get('length', 0))} "
            f"· {_bpm(t.get('bpm', 0))}]")


def winner_of(group: dict) -> dict:
    wid = group.get("winner_content_id")
    for m in group["members"]:
        if m["content_id"] == wid:
            return m
    return group["members"][0]


def losers_of(group: dict) -> list[dict]:
    wid = group.get("winner_content_id")
    return [m for m in group["members"] if m["content_id"] != wid]


TYPE_LABELS = {
    "exact": "Exact dupes",
    "looks_same": "Looks like the same",
    "different_versions": "Different versions",
}
TYPE_ORDER = ["exact", "looks_same", "different_versions"]


def group_type(group: dict) -> str:
    mt = group.get("match_type", "exact")
    # Back-compat: an old manifest may still say "version_variant".
    if mt == "version_variant":
        return "different_versions"
    return mt if mt in TYPE_LABELS else "exact"


def quality_cell(t: dict) -> str:
    """'aiff 1411k · 4:43 · 113.0' -- the per-file quality summary."""
    return (f"{t['ext']} {t['bitrate']}k · {_mmss(t.get('length', 0))} "
            f"· {_bpm(t.get('bpm', 0))}")


def lives_in(member: dict) -> str:
    """Every non-backup playlist this one copy lives in, with a count."""
    paths = sorted({mm["playlist_path"] for mm in member.get("memberships", [])
                    if not mm.get("excluded")})
    if not paths:
        return "(no non-backup playlist)"
    return f"({len(paths)}) " + "; ".join(paths)


def keep_cell(group: dict, member: dict) -> str:
    """How this copy is currently treated: KEEP (decided keeper), rec (the
    recommended keeper, not yet decided), or blank (a loser / undecided copy)."""
    cid = member["content_id"]
    wid = group.get("winner_content_id")
    rec = group.get("recommended_winner_content_id")
    dec = group.get("decision")
    if dec == "keep_all":
        return "keep"
    if dec == "collapse":
        return "KEEP" if cid == wid else ""
    return "rec" if cid == rec else ""  # pending


def decision_label(group: dict) -> str:
    d = group.get("decision", "collapse")
    if d == "pending":
        return "PENDING"
    if d == "keep_all":
        return "keep both"
    scope = group.get("scope")
    if scope not in ("target_only", "everywhere"):
        return "collapse (SCOPE?)"
    return f"collapse ({scope})"


def affected_playlists(group: dict) -> list[str]:
    """Playlists this group's current decision touches -- exactly the playlists
    in its computed actions. Empty for keep_all/pending (nothing decided yet).
    Keyed by full folder PATH (names are not unique)."""
    return sorted({a["playlist_path"] for a in group.get("actions", [])})


def live_summary(manifest: dict) -> dict:
    """Recompute the headline counts from the CURRENT group decisions, not the
    detect-time `summary` block (which decide.py never updates -- so it goes
    stale the moment a decision is recorded)."""
    groups = manifest.get("groups", [])
    removals = adds = 0
    for g in groups:
        for a in g.get("actions", []):
            if a.get("type") == "remove":
                removals += 1
            elif a.get("type") == "add":
                adds += 1
    by_type = {t: sum(1 for g in groups if group_type(g) == t) for t in TYPE_ORDER}
    return {
        "duplicate_groups": len(groups),
        "exact_groups": by_type["exact"],
        "looks_same_groups": by_type["looks_same"],
        "different_version_groups": by_type["different_versions"],
        "pending_groups": sum(1 for g in groups if g.get("decision") == "pending"),
        "scope_unset_groups": sum(1 for g in groups
                                  if g.get("decision") == "collapse"
                                  and g.get("scope") not in ("target_only", "everywhere")),
        "losing_tracks": sum(len(losers_of(g)) for g in groups),
        "membership_removals": removals,
        "winner_adds": adds,
        "protected_playlists_skipped": len(manifest.get("protected_playlists_skipped", [])),
    }


def print_protected(manifest: dict) -> None:
    protected = manifest.get("protected_playlists_skipped", [])
    print(f"Protected (left untouched): {len(protected)} Backup playlist(s)")
    for p in protected:
        print(f"  - {p}")


HEADERS = ["#", "Keep", "Artist - Title", "Quality (fmt · time · bpm)",
           "Lives in (non-backup playlists)", "Note"]


def _type_rows(manifest: dict, mt: str):
    """One row per COPY for every group of kind `mt`, in global-# order. The #,
    title and note appear on the group's first copy and are blank on the rest so
    each group reads as a block while still showing every file."""
    out = []
    for i, g in enumerate(manifest["groups"], 1):
        if group_type(g) != mt:
            continue
        members = g["members"]
        note = g.get("version_note", "") or ""
        for j, m in enumerate(members):
            first = j == 0
            out.append((
                str(i) if first else "",
                keep_cell(g, m),
                f"{m['artist']} - {m['title']}" if first else "",
                quality_cell(m),
                lives_in(m),
                (note if first else ""),
            ))
    return out


def _print_table(rows_data, fmt: str) -> None:
    if fmt == "tsv":
        print("\t".join(HEADERS))
        for r in rows_data:
            print("\t".join(r))
        return
    print("| " + " | ".join(HEADERS) + " |")
    print("|" + "|".join(["---"] * len(HEADERS)) + "|")
    for r in rows_data:
        cells = [c.replace("|", "\\|") for c in r]
        print("| " + " | ".join(cells) + " |")


def print_overview(manifest: dict, fmt: str) -> None:
    pl = manifest.get("playlist", {})
    s = live_summary(manifest)

    print(f"Playlist: {pl.get('path')}")
    print(
        f"Groups: {s['duplicate_groups']} "
        f"({s['exact_groups']} exact, {s['looks_same_groups']} look the same, "
        f"{s['different_version_groups']} different versions) | "
        f"Pending: {s['pending_groups']} | Need scope: {s['scope_unset_groups']} | "
        f"Removals: {s['membership_removals']} | Winner adds: {s['winner_adds']} | "
        f"Protected skipped: {s['protected_playlists_skipped']}"
    )

    blurbs = {
        "exact": "Same recording for sure. Decide the whole table at once: "
                 "replace all (keep the lossless/higher-bitrate copy) or keep all.",
        "looks_same": "Almost certainly the same take (matching length + BPM, just "
                      "tagged/encoded differently). Review one at a time.",
        "different_versions": "Genuinely different takes (different length/BPM or "
                              "distinct version tags). Review one at a time; keep "
                              "both is common here.",
    }

    total_shown = 0
    for ti, mt in enumerate(TYPE_ORDER, 1):
        data = _type_rows(manifest, mt)
        count = sum(1 for g in manifest["groups"] if group_type(g) == mt)
        print(f"\n### Table {ti} - {TYPE_LABELS[mt]} ({count} group(s))")
        print(blurbs[mt])
        print()
        if not data:
            print("_none_")
            continue
        _print_table(data, fmt)
        total_shown += count

    print(f"\nShown: {total_shown} of {s['duplicate_groups']} groups "
          f"(complete -- nothing truncated). 'Keep' column: KEEP = decided keeper, "
          f"rec = recommended keeper (not yet decided).")

    protected = manifest.get("protected_playlists_skipped", [])
    if protected:
        print(f"\nProtected (left untouched): {len(protected)} Backup playlist(s) -- "
              f"never modified. Run with --protected to list them.")
    if s["exact_groups"]:
        print(f"\nTable 1: decide all {s['exact_groups']} exact group(s) at once -- "
              f"replace all: decide.py <manifest> --all-exact --scope everywhere "
              f"(or --scope target-only); keep all: --all-exact --keep-both.")
    if s["pending_groups"]:
        print(f"\n{s['pending_groups']} group(s) in tables 2 & 3 are PENDING -- review "
              f"each (show_manifest.py --group N) and record a keeper + scope with "
              f"decide.py. Apply refuses until none are pending.")
    if s["scope_unset_groups"]:
        print(f"\n{s['scope_unset_groups']} collapse group(s) still have no scope. "
              f"Scope is never assumed -- choose 'everywhere' or 'target-only'. "
              f"Apply refuses until set.")


def print_card(manifest: dict, n: int) -> None:
    groups = manifest.get("groups", [])
    if not (1 <= n <= len(groups)):
        raise SystemExit(f"--group {n} out of range (1..{len(groups)}).")
    g = groups[n - 1]
    rec = g.get("recommended_winner_content_id")
    wid = g.get("winner_content_id")
    typ = TYPE_LABELS[group_type(g)].upper()

    print(f"Group {n} of {len(groups)} -- {typ}"
          + (f"  ({g['version_note']})" if g.get("version_note") else ""))
    print(f"Confidence: {g['confidence']}   Decision: {decision_label(g)}")
    print()
    for m in g["members"]:
        tags = []
        if m["content_id"] == rec:
            tags.append("recommended")
        if m["content_id"] == wid and g.get("decision") == "collapse":
            tags.append("KEEPING")
        tag = f"  <- {', '.join(tags)}" if tags else ""
        print(f"  {track_label(m)}{tag}")
        # Show where this copy lives (non-protected playlists), so the
        # scope choice is informed.
        live = sorted({mm["playlist_path"] for mm in m.get("memberships", [])
                       if not mm.get("excluded")})
        if live:
            print(f"        in: {'; '.join(live)}")
    print()

    aff = affected_playlists(g)
    scope = g.get("scope")
    if g.get("decision") == "collapse" and scope in ("target_only", "everywhere"):
        print(f"Current plan: keep id {wid}, scope {scope}.")
        print(f"  Affected playlists: {', '.join(aff) if aff else '(none)'}")
    elif g.get("decision") == "collapse":
        print(f"Current plan: keep id {wid}, but SCOPE IS UNSET -- choose below "
              f"before apply (apply refuses until then).")
    elif g.get("decision") == "keep_all":
        print("Current plan: keep all copies (no change).")
    else:
        print("Current plan: PENDING -- choose below before apply.")

    print()
    print("To decide this group (--scope is required -- ask the user, no default):")
    ids = [m["content_id"] for m in g["members"]]
    print(f"  Keep one (everywhere):         decide.py <manifest> --group {n} --keep <id> --scope everywhere")
    print(f"  Keep one (this playlist only): decide.py <manifest> --group {n} --keep <id> --scope target-only")
    print(f"  Keep both:                     decide.py <manifest> --group {n} --keep-both")
    print(f"  copy ids: {', '.join(ids)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a dedupe manifest for review.")
    ap.add_argument("manifest", help="Path to manifest.json from detect_duplicates.")
    ap.add_argument("--group", type=int, help="Show a single group's detail card (1-based).")
    ap.add_argument("--protected", action="store_true",
                    help="List the protected (Backup) playlists left untouched.")
    ap.add_argument("--format", choices=["md", "tsv"], default="md")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if args.protected:
        print_protected(manifest)
    elif args.group is not None:
        print_card(manifest, args.group)
    else:
        print_overview(manifest, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
