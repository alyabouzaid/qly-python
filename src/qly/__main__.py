"""Allow ``python -m qly`` as an alias for the ``qly`` CLI."""

import sys

from .cli import main

sys.exit(main())
