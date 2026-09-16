"""Command line interface.

    python -m sqldoc query.sql
    python -m sqldoc reports/ --format json --out docs/
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from .analyze import analyse
from .render import to_json, to_markdown


def _collect(target: pathlib.Path) -> list[pathlib.Path]:
    if target.is_dir():
        return sorted(target.rglob("*.sql"))
    return [target]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sqldoc",
        description="Describe what a SQL report does, in plain language.",
    )
    ap.add_argument("path", help="A .sql file, or a folder to walk")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--out", help="Directory to write into (default: stdout)")
    args = ap.parse_args(argv)

    target = pathlib.Path(args.path)
    if not target.exists():
        print(f"sqldoc: no such path: {target}", file=sys.stderr)
        return 2

    files = _collect(target)
    if not files:
        print(f"sqldoc: no .sql files under {target}", file=sys.stderr)
        return 1

    out_dir = pathlib.Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for path in files:
        doc = analyse(path.read_text(encoding="utf-8"), name=path.stem)
        text = to_json(doc) if args.format == "json" else to_markdown(doc)

        if out_dir:
            suffix = ".json" if args.format == "json" else ".md"
            dest = out_dir / (path.stem + suffix)
            dest.write_text(text, encoding="utf-8")
            print(f"wrote {dest}")
        else:
            print(text)
            if len(files) > 1:
                print("\n" + "-" * 60 + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
