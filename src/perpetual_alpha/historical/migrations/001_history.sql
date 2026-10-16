CREATE TABLE history_meta (id INTEGER PRIMARY KEY CHECK(id=1), last_recorded_ms INTEGER NOT NULL);
INSERT INTO history_meta VALUES(1,0);
CREATE TABLE observations (
 seq INTEGER PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('metadata','candles')), request TEXT NOT NULL,
 received_ms INTEGER NOT NULL, available_ms INTEGER NOT NULL,
 raw_digest TEXT NOT NULL, raw TEXT NOT NULL, summary TEXT NOT NULL);
CREATE INDEX observations_visible ON observations(available_ms,seq);
CREATE TABLE metadata_versions (
 seq INTEGER PRIMARY KEY REFERENCES observations(seq), payload TEXT NOT NULL);
CREATE TABLE candle_versions (
 revision_id INTEGER PRIMARY KEY, seq INTEGER NOT NULL REFERENCES observations(seq),
 symbol TEXT NOT NULL, interval TEXT NOT NULL, open_ms INTEGER NOT NULL,
 close_ms INTEGER NOT NULL, digest TEXT NOT NULL, valid INTEGER NOT NULL,
 reason TEXT, payload TEXT,
 UNIQUE(seq,symbol,interval,open_ms));
CREATE INDEX candles_visible ON candle_versions(symbol,interval,open_ms,seq);
