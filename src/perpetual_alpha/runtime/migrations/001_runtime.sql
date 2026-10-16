CREATE TABLE runtime_meta (
    id INTEGER PRIMARY KEY CHECK(id=1), last_now_ms INTEGER NOT NULL);
INSERT INTO runtime_meta VALUES(1,0);
CREATE TABLE workers (
    worker_id TEXT PRIMARY KEY, principal TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
CREATE TABLE controls (
    scope TEXT PRIMARY KEY, producer_id TEXT NOT NULL, producer_version TEXT NOT NULL,
    epoch INTEGER NOT NULL CHECK(epoch>0), accepted_asof_ms INTEGER, consumed_asof_ms INTEGER);
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY, context_id TEXT NOT NULL UNIQUE,
    producer_id TEXT NOT NULL, producer_version TEXT NOT NULL, role TEXT NOT NULL,
    scope TEXT NOT NULL, control_epoch INTEGER NOT NULL, asof_ms INTEGER NOT NULL,
    deadline_ms INTEGER NOT NULL, status TEXT NOT NULL, worker_id TEXT,
    job TEXT NOT NULL, lease TEXT, manifest TEXT NOT NULL, manifest_digest TEXT NOT NULL,
    create_digest TEXT NOT NULL, schedule_group TEXT, schedule_tick INTEGER,
    UNIQUE(schedule_group,schedule_tick));
CREATE UNIQUE INDEX one_job_per_worker ON jobs(worker_id) WHERE status='running';
CREATE UNIQUE INDEX one_control_job ON jobs(scope) WHERE role='control' AND status='running';
CREATE INDEX queued_jobs ON jobs(producer_id,producer_version,role,status,asof_ms);
CREATE INDEX job_timers ON jobs(status,deadline_ms);
CREATE TABLE submissions (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), producer_id TEXT NOT NULL, producer_version TEXT NOT NULL,
    batch_id TEXT NOT NULL, payload_digest TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL,
    UNIQUE(producer_id,producer_version,batch_id));
CREATE TABLE submission_keys (
    producer_id TEXT NOT NULL, producer_version TEXT NOT NULL, submission_id TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES submissions(job_id), payload_digest TEXT NOT NULL,
    PRIMARY KEY(producer_id,producer_version,submission_id));
CREATE TABLE completions (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), producer_id TEXT NOT NULL, producer_version TEXT NOT NULL,
    completion_id TEXT NOT NULL, payload_digest TEXT NOT NULL, response TEXT NOT NULL,
    UNIQUE(producer_id,producer_version,completion_id));
CREATE TABLE claims (
    worker_id TEXT NOT NULL, request_id TEXT NOT NULL, principal_digest TEXT NOT NULL,
    response TEXT, PRIMARY KEY(worker_id,request_id));
CREATE TABLE processing (
    processing_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES submissions(job_id),
    scope TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL, record TEXT NOT NULL);
CREATE UNIQUE INDEX one_control_consumer ON processing(scope) WHERE role='control' AND status='running';
CREATE TABLE outbox (
    processing_id TEXT PRIMARY KEY REFERENCES processing(processing_id),
    status TEXT NOT NULL CHECK(status IN ('pending','running','done')), delivery_epoch INTEGER NOT NULL);
CREATE TABLE schedules (
    schedule_group TEXT PRIMARY KEY, config_digest TEXT NOT NULL,
    pending_tick INTEGER NOT NULL, last_job_tick INTEGER);
CREATE TABLE audit (
    sequence INTEGER PRIMARY KEY, observed_ms INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
