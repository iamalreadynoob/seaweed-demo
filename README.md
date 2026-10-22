# SeaweedFS Demo

A local [SeaweedFS](https://github.com/seaweedfs/seaweedfs) cluster used as an
S3-compatible object store, with a Python client built on `boto3`.

## What is SeaweedFS?

SeaweedFS is an open-source (Apache 2.0) distributed storage system that ships
as a single `weed` binary. It provides:

- **Object storage** through an S3-compatible API.
- **A file system** through the filer, available over FUSE mounts, WebDAV,
  SFTP, HDFS and plain HTTP.
- **Table storage** with S3 Tables, an Iceberg REST catalog and Lance tables.

Its design is based on Facebook's Haystack paper. Files are packed into large
append-only *volumes*, and each volume server keeps a small in-memory index
entry per file. Reading any file takes about one disk seek, at roughly 40 bytes
of on-disk metadata per file. This makes SeaweedFS well suited to storing
**billions of small files**.

Other features include:

- Replication that is aware of racks and data centers.
- Erasure coding for warm data, run in the background so writes stay fast.
- Cloud tiering to AWS, GCS, Azure and similar providers.
- Active-active and active-passive sync between clusters.
- Pluggable filer metadata stores (LevelDB, PostgreSQL, MySQL, Redis, etcd and
  more).
- A broad S3 API: versioning, Object Lock, lifecycle rules, SSE-S3, SSE-KMS and
  SSE-C encryption, plus IAM and STS APIs.

## Architecture

[docker-compose.yaml](docker-compose.yaml) runs each SeaweedFS component as its
own service:

| Service      | Port(s)                    | Role                                                                                                   |
| ------------ | -------------------------- | ------------------------------------------------------------------------------------------------------ |
| `master`     | 9333 (HTTP), 19333 (gRPC)  | Tracks which volume lives on which server and assigns file IDs. Clients cache this, so it is not on the read path. |
| `volume`     | 8080, 18080                | Stores file data in volume files. Add volume servers to scale capacity.                                 |
| `filer`      | 8888, 18888                | Adds directories and file names on top of volumes. S3 buckets and keys are stored here.                |
| `s3`         | **8333**                   | S3 gateway that `boto3` connects to. Translates S3 calls into filer operations.                         |
| `webdav`     | 7333                       | WebDAV access to the same filer data.                                                                   |
| `prometheus` | 9000                       | Collects metrics from each service's `-metricsPort` (9324–9327).                                        |

The filer and S3 gateway keep no state of their own, so in production you can
run several of each behind a load balancer.

## Project files

| File                                     | Purpose                                                        |
| ---------------------------------------- | -------------------------------------------------------------- |
| [docker-compose.yaml](docker-compose.yaml) | Runs the SeaweedFS cluster.                                   |
| [s3.json](s3.json)                       | S3 identities: access keys, secret keys and allowed actions.  |
| [.env.example](.env.example)             | Template for the client settings read by `main.py`.            |
| [main.py](main.py)                       | Creates a `boto3` S3 client connected to SeaweedFS.            |

## Setup

Requirements: Docker with Compose, Python 3.12+ and
[uv](https://docs.astral.sh/uv/).

1. Install the Python dependencies:

   ```sh
   uv add boto3 python-dotenv
   ```

2. Create your `.env` from the template:

   ```sh
   cp .env.example .env
   ```

3. Start the cluster:

   ```sh
   docker compose up -d
   ```

4. Check the connection:

   ```sh
   uv run main.py
   ```

   On success it logs the endpoint and the number of buckets.

## S3 authentication

Without an identity config, the SeaweedFS S3 gateway accepts any credentials.
This project loads [s3.json](s3.json), which is mounted into the `s3` container
and passed with `-config=/etc/seaweedfs/s3.json`.

It defines two example identities:

| Identity   | Access key            | Secret key            | Actions                             |
| ---------- | --------------------- | --------------------- | ----------------------------------- |
| `admin`    | `admin_access_key`    | `admin_secret_key`    | Admin, Read, List, Tagging, Write   |
| `readonly` | `readonly_access_key` | `readonly_secret_key` | Read, List                          |

- An action can be limited to one bucket with a suffix, for example
  `"Read:my-bucket"`.
- The gateway re-reads `s3.json` on a HUP signal, so you can apply changes
  without a restart:

  ```sh
  docker compose kill -s HUP s3
  ```

- `SEAWEED_S3_ACCESS_KEY` and `SEAWEED_S3_SECRET_KEY` in `.env` must match one
  of the identities. Otherwise requests fail with `InvalidAccessKeyId` or
  `SignatureDoesNotMatch`.

> **Warning:** The keys in `s3.json` are placeholders. Do not commit real
> credentials. For real deployments, commit a template such as
> `s3.example.json` and keep the real file out of version control.

## Configuration

`main.py` reads these variables. It calls `load_dotenv(override=True)`, so
values in `.env` take precedence over variables already set in your shell.

| Variable                | Example                 | Description                          |
| ----------------------- | ----------------------- | ------------------------------------ |
| `SEAWEED_S3_ENDPOINT`   | `http://localhost:8333` | URL of the SeaweedFS S3 gateway.     |
| `SEAWEED_S3_ACCESS_KEY` | `admin_access_key`      | Access key of an identity in `s3.json`. |
| `SEAWEED_S3_SECRET_KEY` | `admin_secret_key`      | Secret key of that identity.         |
| `SEAWEED_S3_REGION`     | `us-east-1`             | Region used for request signing.     |

> **Warning:** `main.py` has no defaults for these variables. If `.env` is
> missing, `boto3` falls back to real AWS S3 and your machine's AWS
> credentials.

## Using SeaweedFS from Python

[main.py](main.py) is the entry point for talking to SeaweedFS with `boto3`.
`get_s3_client()` returns a standard `boto3` S3 client configured for
SeaweedFS:

- **Path-style addressing**: URLs look like `http://localhost:8333/bucket/key`,
  not `http://bucket.localhost:8333/key`.
- **SigV4 request signing.**

Because `boto3` connects lazily, `main()` calls `list_buckets()` once to
confirm the gateway is reachable.

Import the client to use it elsewhere:

```python
from main import get_s3_client

s3 = get_s3_client()

s3.create_bucket(Bucket="demo")
s3.put_object(Bucket="demo", Key="hello.txt", Body=b"hello seaweed")

body = s3.get_object(Bucket="demo", Key="hello.txt")["Body"].read()

s3.upload_file("local.pdf", "demo", "docs/local.pdf")
```

Other standard `boto3` S3 calls work the same way, including
`list_objects_v2`, `delete_object`, presigned URLs and multipart uploads.

## Known limitations

- **No data persistence.** No service mounts a volume for its data directory,
  so `docker compose down` deletes all stored data. To keep data, mount `/data`
  on the `master`, `volume` and `filer` services.
- **Prometheus config is missing.** The `prometheus` service mounts
  `./prometheus`, which is not in the repository, so the service does not
  start. It needs a `prometheus/prometheus.yml` that scrapes ports 9324–9327.
- **Simpler single-process option.** For local development, recent SeaweedFS
  releases also provide `weed mini -dir=./data`. It runs all components in one
  process and can take S3 credentials from `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY`.

## References

- [SeaweedFS on GitHub](https://github.com/seaweedfs/seaweedfs)
- [SeaweedFS wiki: Amazon S3 API](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API)
- [Vultr: How to Deploy SeaweedFS](https://docs.vultr.com/how-to-deploy-seaweedfs-an-open-source-s3-storage-alternative-to-minio)
