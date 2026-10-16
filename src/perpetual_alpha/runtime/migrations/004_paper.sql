CREATE TABLE paper_configuration (id INTEGER PRIMARY KEY CHECK(id=1),scope TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL);
CREATE TABLE paper_meta (id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
CREATE TABLE paper_positions (symbol TEXT PRIMARY KEY,quantity TEXT NOT NULL,entry_price TEXT NOT NULL);
CREATE TABLE paper_quotes (symbol TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE paper_plans (
 processing_id TEXT PRIMARY KEY REFERENCES processing(processing_id),decision_id TEXT NOT NULL UNIQUE,
 delivery_epoch INTEGER NOT NULL,proposal TEXT NOT NULL,proposal_digest TEXT NOT NULL,
 status TEXT NOT NULL,reason TEXT NOT NULL,report TEXT,sequence INTEGER NOT NULL DEFAULT 0,created_ms INTEGER NOT NULL);
CREATE TABLE paper_intents (
 intent_id TEXT PRIMARY KEY,decision_id TEXT NOT NULL REFERENCES paper_plans(decision_id),
 symbol TEXT NOT NULL,leg INTEGER NOT NULL,quantity TEXT NOT NULL,filled TEXT NOT NULL,status TEXT NOT NULL,
 UNIQUE(decision_id,symbol,leg));
CREATE TABLE paper_fills (
 sequence INTEGER PRIMARY KEY,fill_id TEXT NOT NULL UNIQUE,intent_id TEXT NOT NULL REFERENCES paper_intents(intent_id),
 payload TEXT NOT NULL,digest TEXT NOT NULL);
CREATE TABLE paper_reports (
 decision_id TEXT NOT NULL REFERENCES paper_plans(decision_id),sequence INTEGER NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(decision_id,sequence));
CREATE TABLE paper_checks (sequence INTEGER PRIMARY KEY,decision_id TEXT NOT NULL,observed_ms INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE TABLE paper_operator_requests (request_id TEXT PRIMARY KEY,payload TEXT NOT NULL,response TEXT NOT NULL);
