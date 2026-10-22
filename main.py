"""Connect to the SeaweedFS S3 gateway with boto3."""

import logging
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterator

import boto3
from botocore.client import BaseClient, ClientError, Config
from dotenv import load_dotenv

# Values in .env take precedence over variables already set in the environment.
load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The S3 gateway from docker-compose.yaml listens on port 8333. It runs without
# an identity config, so SeaweedFS accepts any access key / secret pair.
S3_ENDPOINT = os.getenv("SEAWEED_S3_ENDPOINT")
S3_ACCESS_KEY = os.getenv("SEAWEED_S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("SEAWEED_S3_SECRET_KEY")
S3_REGION = os.getenv("SEAWEED_S3_REGION")


def get_s3_client() -> BaseClient:
    """Create a boto3 S3 client pointed at SeaweedFS.

    Returns:
        A configured S3 client.
    """
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        # SeaweedFS expects path-style URLs (http://host:8333/bucket/key).
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


client = get_s3_client()

# boto3 connects lazily, so make one call to verify the gateway is reachable.
buckets = client.list_buckets().get("Buckets", [])
logger.info("Connected to %s (%d buckets)", S3_ENDPOINT, len(buckets))

def iter_objects(bucket: str) -> Iterator[dict[str, Any]]:
    """Yield every object in a bucket.

    ``list_objects_v2`` returns at most 1000 keys per call, so use a paginator.
    It lists recursively, like ``list_objects(..., recursive=True)`` in MinIO.

    Args:
        bucket: Name of the bucket to list.

    Yields:
        One dict per object, with ``Key``, ``Size``, ``LastModified`` and more.
    """
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        yield from page.get("Contents", [])


# Buckets
logger.info("\n## Buckets")

# S3 bucket names can't contain underscores, so "rock_bands" becomes "rock-bands".
BUCKET = "rock-bands"

# boto3 has no bucket_exists(): head_bucket raises a ClientError if it's missing.
try:
    client.head_bucket(Bucket=BUCKET)
except ClientError:
    client.create_bucket(Bucket=BUCKET)

for bucket in client.list_buckets()["Buckets"]:
    logger.info(f"{bucket['Name']:<40} {bucket['CreationDate']}")

# Objects
logger.info("\n## Objects")

OBJECTS_DIR = Path("objects")

# Load Objects
logger.info("\n### Load Objects")

for path in OBJECTS_DIR.rglob("*"):
    if path.is_file():
        object_name = path.relative_to(OBJECTS_DIR).as_posix()
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        client.upload_file(
            str(path),
            BUCKET,
            object_name,
            ExtraArgs={"ContentType": content_type},
        )
        logger.info(f"Uploaded {object_name} ({content_type})")

# List Objects
logger.info("\n### List Objects")

for obj in iter_objects(BUCKET):
    logger.info(f"{obj['Key']:<40} {obj['Size']:>10} B  {obj['LastModified']}")

# Show Metadata
logger.info("\n### Show Metadata")

stat = client.head_object(Bucket=BUCKET, Key="Korn.pdf")
logger.info(f"{stat['ContentLength']} B  {stat['ContentType']}  {stat['ETag']}")

# Save Objects
logger.info("\n### Save Objects")

DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="rock-bands-"))

for obj in iter_objects(BUCKET):
    target = DOWNLOAD_DIR / obj["Key"]
    # Unlike fget_object, download_file doesn't create parent directories.
    target.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(BUCKET, obj["Key"], str(target))

logger.info(f"Downloaded to {DOWNLOAD_DIR}")
for path in sorted(DOWNLOAD_DIR.rglob("*")):
    if path.is_file():
        logger.info(
            f"{path.relative_to(DOWNLOAD_DIR).as_posix():<40} "
            f"{path.stat().st_size:>10} B",
        )

# Cleanup
logger.info("\n## Cleanup")

# Remove Temp Directory
logger.info("\n### Remove Temp Directory")

shutil.rmtree(DOWNLOAD_DIR)
logger.info(f"Removed {DOWNLOAD_DIR}")

# Delete Objects
logger.info("\n### Delete Objects")

for obj in iter_objects(BUCKET):
    client.delete_object(Bucket=BUCKET, Key=obj["Key"])
    logger.info(f"Deleted {obj['Key']}")

# Delete Bucket
logger.info("\n### Delete Bucket")

client.delete_bucket(Bucket=BUCKET)
logger.info(f"Deleted bucket {BUCKET}")
