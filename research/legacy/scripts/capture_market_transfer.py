"""Bounded PUBLIC mainnet/PAR data for transfer research; no account or exchange routes.

History captures are retrospective. A short concurrent book probe retains each
request/receive time and the server book time; it is not a long-running service.
"""
import argparse
import gzip
import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from perpetual_alpha.historical.client import NoRedirect

HOUR = 3600000
ORIGINS = {"external": "https://api.hyperliquid.xyz/info",
           "par": "https://api.hyperliquid-testnet.xyz/info"}


def request(venue, payload):
    if venue not in ORIGINS or payload.get("type") not in {"meta", "candleSnapshot", "l2Book"}:
        raise ValueError("Public fixed-source market data only")
    start, tick = time.time_ns() // 1000000, time.monotonic_ns()
    result = {"venue": venue, "request": payload, "request_started_ms": start, "error": None}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(ORIGINS[venue], json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
        with opener.open(req, timeout=10) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        received = time.time_ns() // 1000000
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Response budget exceeded")
        data = json.loads(raw)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result.update(payload=data, received_ms=received, available_ms=time.time_ns() // 1000000,
                      available_kind="decoded-response-ready-not-durable-commit-time",
                      round_trip_ms=(time.monotonic_ns() - tick) / 1e6,
                      payload_sha256=digest)
    except Exception as error:
        result.update(error=type(error).__name__, received_ms=time.time_ns() // 1000000,
                      round_trip_ms=(time.monotonic_ns() - tick) / 1e6)
    return result


def append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, allow_nan=False) + "\n")


def capture_history(start, end, path, *, venue="external", interval="1h"):
    step = HOUR if interval == "1h" else 60000
    if start % step or end % step or not 0 < end - start <= 5000 * step:
        raise ValueError("Aligned public window <=5000 hours required")
    done = set()
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            done = {json.dumps(r["request"], sort_keys=True) for r in map(json.loads, stream)
                    if not r.get("error")}
    requests = [{"type": "meta", **({"dex": "par"} if venue == "par" else {})}]
    for symbol in ("BTC", "ETH"):
        for left in range(start, end, 480 * step):
            requests.append({"type": "candleSnapshot", "req": {
                "coin": "par:" + symbol if venue == "par" else symbol, "interval": interval,
                "startTime": left, "endTime": min(end, left + 480 * step) - 1}})
    for payload in requests:
        if json.dumps(payload, sort_keys=True) in done:
            continue
        row = request(venue, payload)
        append(path, row)
        print(payload.get("req", {}).get("coin", "meta"), payload["type"],
              row["error"] or len(row["payload"]), flush=True)
        time.sleep(1)


def probe(seconds, interval, path):
    if not 10 <= seconds <= 300 or not 2 <= interval <= 30:
        raise ValueError("Probe must last 10–300s at 2–30s intervals")
    if path.exists():
        raise FileExistsError("New capture path required; don't mix independent probe domains")
    session = "transfer-" + str(time.time_ns())
    stop = time.monotonic() + seconds
    with ThreadPoolExecutor(max_workers=4) as pool:
        index = 0
        while time.monotonic() < stop:
            started = time.monotonic()
            pending = [pool.submit(request, venue, {"type": "l2Book", "coin":
                       "par:" + symbol if venue == "par" else symbol})
                       for symbol in ("BTC", "ETH") for venue in ("external", "par")]
            for future in pending:
                row = future.result()
                row.update(session_id=session, round=index)
                append(path, row)
            index += 1
            if index % 6 == 0:
                print("concurrent public book rounds", index, flush=True)
            time.sleep(max(0, min(stop - time.monotonic(), interval - (time.monotonic() - started))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    history = sub.add_parser("history")
    history.add_argument("--start-ms", type=int, required=True)
    history.add_argument("--end-ms", type=int, required=True)
    history.add_argument("--output", type=Path, required=True)
    history.add_argument("--venue", choices=("external", "par"), default="external")
    history.add_argument("--interval", choices=("1m", "1h"), default="1h")
    live = sub.add_parser("probe")
    live.add_argument("--seconds", type=int, default=120)
    live.add_argument("--interval", type=int, default=5)
    live.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "history":
        capture_history(args.start_ms, args.end_ms, args.output, venue=args.venue, interval=args.interval)
    else:
        probe(args.seconds, args.interval, args.output)
