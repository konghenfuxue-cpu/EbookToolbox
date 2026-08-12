"""Compatibility entry point for running the application from the repository root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ebook_toolbox.app import main  # noqa: E402

if __name__ == "__main__":
    main()
