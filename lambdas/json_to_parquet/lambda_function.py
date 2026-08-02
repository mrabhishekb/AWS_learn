"""
Lambda: JSON reference (video categories) → Silver Layer (Parquet)
──────────────────────────────────────────────────────────────────
Triggered by S3 when new *_category_id.json lands under Bronze reference.

Reads the object from S3 (no Glue crawler / bronze catalog).
Writes ONLY to the Silver reference folder (separate from statistics).

Single-bucket layout (folders = layers):
  Bronze reference:
    s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics_reference_data/region=us/.../us_category_id.json
  Silver reference (this Lambda):
    s3://youtube-analytics-data-ap-south-01/silverLayer/youtube/reference_data/region=us/*.parquet
  Silver statistics (Glue job bronze_to_silver — separate folder):
    s3://youtube-analytics-data-ap-south-01/silverLayer/youtube/statistics/region=us/*.parquet

Environment:
  S3_BUCKET             — default youtube-analytics-data-ap-south-01
  BRONZE_REF_PREFIX     — default bronzeLayer/youtube/raw_statistics_reference_data
  SILVER_LAYER_PREFIX   — default silverLayer
  SILVER_REF_SUBPATH    — default youtube/reference_data
  GLUE_DB_SILVER        — default youtube_db
  GLUE_TABLE_REFERENCE  — default clean_reference_data
  SNS_ALERT_TOPIC_ARN   — optional
"""

import json
import os
import logging
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
import awswrangler as wr
import pandas as pd

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_BUCKET = "youtube-analytics-data-ap-south-01"
BUCKET = os.environ.get("S3_BUCKET", DEFAULT_BUCKET).strip()
BRONZE_REF_PREFIX = os.environ.get(
    "BRONZE_REF_PREFIX", "bronzeLayer/youtube/raw_statistics_reference_data"
).strip().strip("/")
SILVER_PREFIX = os.environ.get("SILVER_LAYER_PREFIX", "silverLayer").strip().strip("/")
# Keep reference SEPARATE from statistics under silverLayer/youtube/
SILVER_REF_SUBPATH = os.environ.get(
    "SILVER_REF_SUBPATH", "youtube/reference_data"
).strip().strip("/")
GLUE_DB = os.environ.get("GLUE_DB_SILVER", "youtube_db")
GLUE_TABLE = os.environ.get("GLUE_TABLE_REFERENCE", "clean_reference_data")
SNS_TOPIC = os.environ.get("SNS_ALERT_TOPIC_ARN", "")

# Never write under youtube/statistics/ — that path is owned by the Glue job
SILVER_REF_PATH = f"s3://{BUCKET}/{SILVER_PREFIX}/{SILVER_REF_SUBPATH}/"

s3_client = boto3.client("s3")
sns_client = boto3.client("sns")


def read_json_from_s3(bucket: str, key: str) -> dict:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response["Body"].read().decode("utf-8")
    return json.loads(content)


def validate_category_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("Empty DataFrame — no category items found")

    required_cols = {"id", "snippet.title"}
    actual_cols = set(df.columns)
    missing = required_cols - actual_cols
    if missing:
        logger.warning("Missing expected columns: %s. Available: %s", missing, actual_cols)

    before = len(df)
    if "id" in df.columns:
        df = df.drop_duplicates(subset=["id"], keep="last")
    after = len(df)
    if before != after:
        logger.info("  Removed %s duplicate categories", before - after)

    return df


def send_alert(subject: str, message: str):
    if SNS_TOPIC:
        sns_client.publish(TopicArn=SNS_TOPIC, Subject=subject[:100], Message=message)


def region_from_key(key: str) -> str:
    for part in key.split("/"):
        if part.startswith("region="):
            return part.split("=", 1)[1].strip().lower()
    return "unknown"


def is_bronze_reference_key(key: str) -> bool:
    """Only Bronze reference JSON — ignore statistics JSON/CSV."""
    if not key.startswith(f"{BRONZE_REF_PREFIX}/"):
        return False
    # Never process stats path or non-category files
    if "/raw_statistics/" in key and "raw_statistics_reference_data" not in key:
        return False
    base = key.rsplit("/", 1)[-1]
    return base.endswith("_category_id.json")


def iter_s3_records(event: dict) -> list[dict]:
    records = event.get("Records", [])
    if records:
        return records
    if "s3" in event and "bucket" in event["s3"]:
        return [event]
    return []


def lambda_handler(event, context):
    processed = []
    errors = []
    records = iter_s3_records(event)

    for record in records:
        key = None
        try:
            s3_info = record["s3"]
            bucket = s3_info["bucket"]["name"]
            key = unquote_plus(s3_info["object"]["key"])

            logger.info("Processing: s3://%s/%s", bucket, key)

            if bucket != BUCKET:
                logger.warning(
                    "Event bucket %s != S3_BUCKET %s — using event bucket",
                    bucket,
                    BUCKET,
                )

            if not is_bronze_reference_key(key):
                logger.info(
                    "Skip (not bronze reference under %s/*_category_id.json): %s",
                    BRONZE_REF_PREFIX,
                    key,
                )
                continue

            raw_data = read_json_from_s3(bucket, key)

            if "items" in raw_data and isinstance(raw_data["items"], list):
                df = pd.json_normalize(raw_data["items"])
            else:
                df = pd.json_normalize(raw_data)

            logger.info("  Raw shape: %s", df.shape)

            df = validate_category_data(df)

            df["_ingestion_timestamp"] = datetime.now(timezone.utc).isoformat()
            df["_source_file"] = key
            region = region_from_key(key)
            df["region"] = region

            logger.info("  Clean shape: %s, region: %s", df.shape, region)
            logger.info("  Writing Silver reference → %s", SILVER_REF_PATH)

            wr.s3.to_parquet(
                df=df,
                path=SILVER_REF_PATH,
                dataset=True,
                database=GLUE_DB,
                table=GLUE_TABLE,
                partition_cols=["region"],
                mode="overwrite_partitions",
                schema_evolution=True,
            )

            logger.info(
                "  Written under: %s (partition region=%s)", SILVER_REF_PATH, region
            )
            processed.append({"key": key, "region": region, "rows": len(df)})

        except Exception as e:
            logger.error("Error processing record: %s", e, exc_info=True)
            errors.append({"key": key or "unknown", "error": str(e)})

    if errors:
        send_alert(
            subject="[YT Pipeline] Silver reference transform failed",
            message=json.dumps(errors, indent=2),
        )

    return {
        "statusCode": 200,
        "processed": processed,
        "errors": errors,
        "silver_reference_path": SILVER_REF_PATH,
    }
