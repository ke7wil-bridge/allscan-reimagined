#!/usr/bin/env python3
"""Patch nullable AllScan HTML helper arguments for PHP 8.1+ in the ASR copy."""

from __future__ import annotations

import argparse
from pathlib import Path
import tempfile


REPLACEMENTS = (
    ("if(strlen($val) > $size)", "if($val !== null && strlen($val) > $size)"),
    ("if(strlen($class))", "if($class !== null && strlen($class))"),
)


def patch_html(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    updated = source
    for legacy, guarded in REPLACEMENTS:
        if legacy not in updated and guarded not in updated:
            raise ValueError(f"Expected AllScan Html.php expression was not found: {legacy}")
        updated = updated.replace(legacy, guarded)
    if updated == source:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def self_test() -> None:
    fixture = """<?php
if(strlen($val) > $size) $size = strlen($val);
if(strlen($class)) $out .= $class;
if(strlen($class)) $more .= $class;
"""
    with tempfile.TemporaryDirectory(prefix="asr-html-nullability-") as directory:
        path = Path(directory) / "Html.php"
        path.write_text(fixture, encoding="utf-8")
        assert patch_html(path) is True
        result = path.read_text(encoding="utf-8")
        assert "if($val !== null && strlen($val) > $size)" in result
        assert result.count("if($class !== null && strlen($class))") == 2
        assert patch_html(path) is False
    print("AllScan Html.php nullable-argument patch self-test: ok")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.path is None or not args.path.is_file():
        parser.error("an existing Html.php path is required")
    patch_html(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
