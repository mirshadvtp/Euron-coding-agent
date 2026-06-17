"""Enable `python -m euron_agent ...` (used by the VS Code auto-start)."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
