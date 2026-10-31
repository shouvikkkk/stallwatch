"""Allow ``python -m stallwatch check <trace-file>``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
