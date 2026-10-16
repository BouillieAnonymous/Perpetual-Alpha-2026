CREATE TABLE gateway_policies (
 producer_id TEXT NOT NULL,producer_version TEXT NOT NULL,role TEXT NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(producer_id,producer_version,role));
CREATE TABLE gateway_bindings (
 job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),payload TEXT NOT NULL);
CREATE TABLE gateway_datasets (
 job_id TEXT NOT NULL REFERENCES jobs(job_id),dataset_id TEXT NOT NULL,request TEXT NOT NULL,
 request_digest TEXT NOT NULL,response TEXT NOT NULL,response_digest TEXT NOT NULL,
 provenance TEXT NOT NULL,response_bytes INTEGER NOT NULL,
 PRIMARY KEY(job_id,dataset_id));
CREATE TABLE gateway_reads (
 job_id TEXT NOT NULL,attempt INTEGER NOT NULL,request_id TEXT NOT NULL,dataset_id TEXT NOT NULL,
 request_digest TEXT NOT NULL,
 PRIMARY KEY(job_id,attempt,request_id),
 FOREIGN KEY(job_id,dataset_id) REFERENCES gateway_datasets(job_id,dataset_id));
CREATE TABLE accepted_manifests (
 job_id TEXT PRIMARY KEY REFERENCES submissions(job_id),attempt INTEGER NOT NULL,
 payload TEXT NOT NULL,payload_digest TEXT NOT NULL);
