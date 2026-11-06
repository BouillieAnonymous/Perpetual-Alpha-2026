"""Capture bounded PUBLIC testnet/par hourly candles/funding; never load credentials."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from perpetual_alpha.historical.client import PublicHistoryClient
from perpetual_alpha.historical.contracts import PRICE_UNITS

HOUR = 3600000


def capture(start, end, output):
    if start % HOUR or end % HOUR or not 0 < end - start <= 120 * 24 * HOUR:
        raise ValueError("Aligned window of at most 120 days required")
    output.parent.mkdir(parents=True, exist_ok=True)
    client = PublicHistoryClient()
    # JSONL permits resuming after interruption; never rewrite an observation vintage.
    done = set()
    if output.exists():
        with gzip.open(output, "rt", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("error") is None:
                    done.add(json.dumps(row["request"], sort_keys=True))
    requests = [{"type": "meta", "dex": "par"}]
    for symbol in PRICE_UNITS:
        for left in range(start, end, 480 * HOUR):
            right = min(end, left + 480 * HOUR)
            requests.extend([
                {"type": "candleSnapshot", "req": {"coin": symbol, "interval": "1h",
                  "startTime": left, "endTime": right - 1}},
                {"type": "fundingHistory", "coin": symbol, "startTime": left, "endTime": right - 1},
            ])
    for request in requests:
        if json.dumps(request, sort_keys=True) in done:
            continue
        row = {"request": request, "schema": "quant-public-observation/1", "error": None}
        try:
            payload, received = client._request(request)
            row.update(payload=payload, received_ms=received, available_ms=received,
                       availability_kind="response-receive-proxy-not-historical-availability",
                       payload_sha256=hashlib.sha256(json.dumps(
                           payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
            print(request.get("coin", request.get("req", {}).get("coin", "meta")),
                  request["type"], len(payload), flush=True)
        except Exception as error:
            row["error"] = type(error).__name__
            print(request["type"], row["error"], flush=True)
        with gzip.open(output, "at", encoding="utf-8") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture(args.start_ms, args.end_ms, args.output)
