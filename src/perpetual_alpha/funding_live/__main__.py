import sys

from .cli import main

try:
    raise SystemExit(main())
except Exception as exc:
    # Never render SDK exception bodies, env contents or signing material.
    print(type(exc).__name__ + ': ' + (str(exc) if isinstance(exc, ValueError) else 'operation failed'),
          file=sys.stderr)
    raise SystemExit(1) from None
