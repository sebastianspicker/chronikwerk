#!/usr/bin/env python3
"""Build the GitHub Pages demo from maintained source and shipped admin assets."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_SOURCE = REPO_ROOT / "docs" / "demo"
ADMIN_ASSETS = REPO_ROOT / "src" / "chronikwerk" / "static" / "admin"


def build(output: Path) -> None:
    """Create a self-contained static site without modifying its source inputs."""
    if output.exists():
        shutil.rmtree(output)
    assets = output / "assets"
    assets.mkdir(parents=True)
    shutil.copy2(DEMO_SOURCE / "index.html", output / "index.html")
    shutil.copy2(DEMO_SOURCE / "demo.css", assets / "demo.css")
    shutil.copy2(DEMO_SOURCE / "demo.js", assets / "demo.js")
    shutil.copy2(ADMIN_ASSETS / "admin.css", assets / "admin.css")
    shutil.copy2(ADMIN_ASSETS / "chronikwerk-mark.svg", assets / "chronikwerk-mark.svg")
    (output / ".nojekyll").touch()


def main() -> None:
    """Parse the output location and build the static demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "build" / "static-demo")
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == "__main__":
    main()
