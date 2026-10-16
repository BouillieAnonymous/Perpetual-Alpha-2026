CREATE TABLE mock_configurations (
    configuration_id TEXT PRIMARY KEY, payload_digest TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE mock_plans (
    processing_id TEXT PRIMARY KEY REFERENCES processing(processing_id),
    configuration_id TEXT NOT NULL REFERENCES mock_configurations(configuration_id),
    decision_id TEXT NOT NULL UNIQUE, decision TEXT NOT NULL, inputs TEXT NOT NULL);
CREATE TABLE fixture_queries (
    job_id TEXT NOT NULL REFERENCES jobs(job_id), attempt INTEGER NOT NULL,
    request_id TEXT NOT NULL, request_digest TEXT NOT NULL, response TEXT NOT NULL,
    response_bytes INTEGER NOT NULL, PRIMARY KEY(job_id,attempt,request_id));
