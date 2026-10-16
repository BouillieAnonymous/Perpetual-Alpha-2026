"""Explicit public wallet read-only capture/query. Does not load .env or signing settings."""
import argparse
from uuid import uuid4

from ..journal import canonical
from ..runtime.process_lock import ProcessLock
from .capture import AccountCapture
from .client import AccountInfoClient
from .store import AccountStore


def main():
    parser = argparse.ArgumentParser(description="Public testnet/par account evidence; no trading")
    parser.add_argument("command", choices=("capture", "query"))
    parser.add_argument("--wallet", required=True, help="Explicit team public wallet, never a private key")
    parser.add_argument("--database", required=True)
    parser.add_argument("--asof-ms", type=int)
    parser.add_argument("--max-age-ms", type=int, default=90000, help="Read-view freshness only; not a risk threshold")
    parser.add_argument("--details", action="store_true", help="Print account details locally; default only shows summary")
    args = parser.parse_args()
    if args.command == "capture":
        if args.asof_ms is not None:
            parser.error("Capture cannot override receive/availability time")
        with ProcessLock(args.database + ".writer.lock"):
            store = AccountStore(args.database)
            bundle, _ = AccountCapture(AccountInfoClient()).capture(args.wallet, str(uuid4()))
            store.save(bundle)
    else:
        store = AccountStore(args.database, read_only=True)
    read = store.latest(args.wallet, args.asof_ms if args.asof_ms is not None else store.clock(), max_age_ms=args.max_age_ms)
    if args.details:
        print(canonical(read))
    else:
        snapshot = read["snapshot"]
        print(canonical({"mode": "read-only-account", "status": read["status"], "risk_ready": False,
                         "capture_id": snapshot["capture_id"] if snapshot else None,
                         "account_mode": snapshot["account_mode"] if snapshot else None,
                         "consistency": snapshot["consistency"] if snapshot else None,
                         "position_count": len(snapshot["positions"]) if snapshot else None,
                         "order_count": len(snapshot["open_orders"]) if snapshot else None,
                         "buying_power_assets": len(snapshot["buying_power"]) if snapshot else 0,
                         "issues": snapshot["issues"] if snapshot else []}))
    return 0 if read["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
