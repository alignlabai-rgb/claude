"""Optional, explicit resource containment for child executions this package owns (see CONTAINMENT.md).

Nothing in this package runs at import time and nothing here is applied unless a caller passes a ResourceEnvelope to run_contained (or to the optional
`envelope=` / `--envelope-*` options of render and mux). Existing defaults, plans and receipts are unchanged when no envelope is given.
"""

from .contained import RECEIPT_SCHEMA, run_contained, validate_hello  # noqa: F401
from .envelope import ResourceEnvelope, add_cli_args, envelope_from_args, unsupported_limits  # noqa: F401
