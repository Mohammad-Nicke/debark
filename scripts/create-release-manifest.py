#!/usr/bin/env python3
"""Create the deterministic, signed-file manifest for an online source bundle."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPOSITORY = "https://github.com/Mohammad-Nicke/debark"
HEADER = "DEBARK-OFFICIAL-MANIFEST 1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="source package directory or standalone output directory")
    parser.add_argument("--root-kind", choices=("source", "standalone"), required=True)
    parser.add_argument("--archive", type=Path,
                        help="unsigned source archive; omit for a standalone directory")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    package = args.root.resolve()
    archive = args.archive.resolve() if args.archive else None
    if not re.fullmatch(r"[0-9a-f]{40,64}", args.commit):
        parser.error("--commit must be a full hexadecimal Git commit ID")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", args.version):
        parser.error("--version must be a numeric dotted version")
    if not package.is_dir() or (args.archive is not None and not archive.is_file()):
        parser.error("the distribution directory or archive is missing")

    files: list[tuple[str, str]] = []
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            parser.error(f"refusing a symbolic link in the package: {path}")
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative_name = path.relative_to(package).as_posix()
        if any(character.isspace() for character in relative_name):
            parser.error(f"manifest paths may not contain whitespace: {relative_name}")
        files.append((relative_name, sha256(path)))
    if not files:
        parser.error("the DebArk source package contains no files")

    archive_digest = sha256(archive) if archive else "none"
    lines = [
        HEADER,
        f"repository={REPOSITORY}",
        f"commit={args.commit}",
        f"version={args.version}",
        f"archive_sha256={archive_digest}",
        f"root_kind={args.root_kind}",
        "--files--",
        *(f"{digest}  {name}" for name, digest in files),
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
