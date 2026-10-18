"""Bounded public official history probe; no wallets, credentials or candles.

Captures fundingHistory and recentTrades from testnet/par. An unsigned HEAD of
the documented S3 asset-context path tests accessibility, not market coverage.
"""
import argparse
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from perpetual_alpha.historical.client import NoRedirect, PublicHistoryClient
from perpetual_alpha.historical.contracts import PRICE_UNITS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbols", nargs="+", default=["par:BTC", "par:ETH"])
    args = parser.parse_args()
    if not args.symbols or len(set(args.symbols)) != len(args.symbols) or set(args.symbols) - PRICE_UNITS.keys():
        parser.error("Select unique competition markets")
    if not 0 <= args.start_ms < args.end_ms or args.end_ms - args.start_ms > 14 * 86400000:
        raise ValueError("Explicit window of at most 14 days required")
    client = PublicHistoryClient()
    result = dict(schema="official-basis-evidence/0.1", endpoint="https://api.hyperliquid-testnet.xyz/info",
                  start_ms=args.start_ms, end_ms=args.end_ms, observations=[])
    for coin in args.symbols:
        for kind in ("fundingHistory", "recentTrades"):
            payload = dict(type=kind, coin=coin)
            if kind == "fundingHistory":
                payload.update(startTime=args.start_ms, endTime=args.end_ms)
            try:
                data, received = client._request(payload)
                raw_digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if kind == "recentTrades" and isinstance(data, list):
                    data = [{k: v for k, v in row.items() if k != "users"} for row in data]
                digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                result["observations"].append(dict(request=payload, received_ms=received, payload=data,
                                                   payload_sha256=digest, raw_payload_sha256=raw_digest,
                                                   omitted_fields=["users"] if kind == "recentTrades" else [], error=None))
                print(coin, kind, "records", len(data) if isinstance(data, list) else "unexpected_shape", flush=True)
            except Exception as error:
                result["observations"].append(dict(request=payload, error=type(error).__name__))
                print(coin, kind, type(error).__name__, flush=True)
    # No request-payer header or AWS credentials. A denial proves no coverage claim.
    date = datetime.fromtimestamp(args.start_ms / 1000, timezone.utc).strftime("%Y%m%d")
    url = f"https://hyperliquid-archive.s3.amazonaws.com/asset_ctxs/{date}.csv.lz4"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url, method="HEAD"), timeout=15) as response:
            result["archive_probe"] = dict(url=url, method="HEAD", status=response.status)
    except urllib.error.HTTPError as error:
        result["archive_probe"] = dict(url=url, method="HEAD", status=error.code)
    except Exception as error:
        result["archive_probe"] = dict(url=url, method="HEAD", error=type(error).__name__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
