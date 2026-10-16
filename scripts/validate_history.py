"""Offline capture → history SQLite → independent read-only handle; no HTTP."""
import json
import tempfile
from pathlib import Path

from perpetual_alpha.historical.store import HistoricalStore
from perpetual_alpha.journal import digest


def main():
    root = Path(__file__).resolve().parents[1]
    sample = json.loads((root / "examples/history/testnet-par-capture.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as folder:
        store = HistoricalStore(Path(folder) / "history.sqlite3")
        meta = sample["metadata"]
        assert digest(meta["payload"]) == meta["payload_digest"]
        store.record_metadata("captured-meta", meta["payload"], meta["received_ms"])
        for captured in sample["candles"]:
            assert digest(captured["records"]) == captured["sample_digest"]
            store.record_candles("captured:" + captured["symbol"], captured["symbol"], captured["interval"],
                                 captured["requested_start_ms"], captured["requested_end_ms"], captured["records"],
                                 captured["received_ms"])
        reader = HistoricalStore(store.path, read_only=True)
        asof = store.clock()
        metadata = reader.read_metadata(asof, max_age_ms=0)
        for captured in sample["candles"]:
            first, last = captured["records"][0]["t"], captured["records"][-1]["T"] + 1
            result = reader.read_candles(captured["symbol"], captured["interval"], first, last, asof,
                                        snapshot_seq=metadata["snapshot_seq"])
            assert result["status"] == "ready" and len(result["records"]) == len(captured["records"])
            assert reader.read_candles(captured["symbol"], captured["interval"], first, last,
                                       captured["received_ms"] - 1)["status"] == "missing"
        print("Offline public BTC/ETH capture, versioned history and read-only WAL queries verified")


if __name__ == "__main__":
    main()
