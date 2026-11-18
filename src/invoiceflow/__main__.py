"""``python -m invoiceflow``: the command line (``cli.main``)."""

import sys

from invoiceflow.cli import main

if __name__ == "__main__":
    sys.exit(main())
