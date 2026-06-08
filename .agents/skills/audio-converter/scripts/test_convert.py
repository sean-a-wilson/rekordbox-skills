#!/usr/bin/env python3
"""Dependency-free checks for the conversion policy (no pytest, no afconvert).

Exercises decide_conversion -- the single function that decides whether a given
source -> target conversion is allowed. Run after touching the policy:

    python3 test_convert.py

The cases pin down the contract:
  * the four allowed conversions return "ok";
  * lossy -> lossless (AAC -> WAV/AIFF) is refused;
  * same-format conversions (WAV->WAV, AIFF->AIFF, AAC->AAC) are refused;
  * aif/m4a aliases behave like aiff/aac;
  * unknown source/target formats are rejected.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from convert import decide_conversion, parse_bitrate, family  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _failures.append(msg)


def test_allowed_conversions() -> None:
    for src, target in [("wav", "aiff"), ("aiff", "wav"),
                        ("wav", "aac"), ("aiff", "aac")]:
        check(decide_conversion(src, target) == "ok",
              f"{src} -> {target} should be allowed")
    # extension aliases / leading dots
    check(decide_conversion(".aif", "wav") == "ok", "aif -> wav should be allowed")


def test_lossy_to_lossless_blocked() -> None:
    for src in ("aac", "m4a"):
        for target in ("wav", "aiff"):
            verdict = decide_conversion(src, target)
            check(verdict != "ok" and "lossy" in verdict,
                  f"{src} -> {target} must be refused as lossy->lossless")


def test_same_format_blocked() -> None:
    for src, target in [("wav", "wav"), ("aiff", "aiff"), ("aif", "aiff"),
                        ("aac", "aac"), ("m4a", "aac")]:
        verdict = decide_conversion(src, target)
        check(verdict != "ok" and "same-format" in verdict,
              f"{src} -> {target} must be refused as same-format")


def test_unknown_formats_rejected() -> None:
    check(decide_conversion("flac", "wav").startswith("unsupported"),
          "flac source should be rejected (not supported by this skill)")
    check(decide_conversion("mp3", "aac").startswith("unsupported"),
          "mp3 source should be rejected (deferred)")
    check(decide_conversion("wav", "flac").startswith("unknown target"),
          "flac target should be rejected")


def test_family_aliases() -> None:
    check(family(".AIF") == "aiff", "AIF should map to aiff family")
    check(family("m4a") == "aac", "m4a should map to aac family")
    check(family("wav") == "wav", "wav maps to itself")


def test_bitrate_parsing() -> None:
    check(parse_bitrate("320k") == 320000, "320k -> 320000")
    check(parse_bitrate("256K") == 256000, "256K -> 256000")
    check(parse_bitrate("320000") == 320000, "raw bps passes through")


def main() -> int:
    test_allowed_conversions()
    test_lossy_to_lossless_blocked()
    test_same_format_blocked()
    test_unknown_formats_rejected()
    test_family_aliases()
    test_bitrate_parsing()

    if _failures:
        print(f"FAILED ({len(_failures)}):")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("OK: all conversion-policy checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
