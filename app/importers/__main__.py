"""Allow `python -m app.importers` to run the importer"""

from app.importers.cli import main

raise SystemExit(main())
