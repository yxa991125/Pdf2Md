"""Skill entrypoint, usable without installing the benchmark repository."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pdf2md_p2.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
