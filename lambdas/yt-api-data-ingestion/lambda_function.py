"""
Lambda: YouTube Data API Ingestion (Bronze Layer)
──────────────────────────────────────────────────
Triggered by EventBridge on a schedule (e.g., every 6 hours).
Pulls trending videos from the YouTube Data API for each configured region
and writes raw JSON responses to Bronze folders in a single S3 bucket.

Single-bucket layout (folders = layers):
  s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics/...
  s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics_reference_data/...
  s3://youtube-analytics-data-ap-south-01/silverLayer/...
  s3://youtube-analytics-data-ap-south-01/goldLayer/...

Environment Variables:
    YOUTUBE_API_KEY       — Google API key with YouTube Data API v3 enabled
    S3_BUCKET             — default youtube-analytics-data-ap-south-01
    BRONZE_STATS_PREFIX   — default bronzeLayer/youtube/raw_statistics
    BRONZE_REF_PREFIX     — default bronzeLayer/youtube/raw_statistics_reference_data
    YOUTUBE_REGIONS       — Comma-separated region codes (default: US,GB,CA,...)
    SNS_ALERT_TOPIC_ARN   — SNS topic for failure alerts
"""

import json
import os
import logging
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import boto3

# ── Logging ──────────────────────────────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── AWS Clients ──────────────────────────────────────────────────────────────
s3_client = boto3.client("s3")
sns_client = boto3.client("sns")

# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_BUCKET = "youtube-analytics-data-ap-south-01"
API_KEY = os.environ["YOUTUBE_API_KEY"]
BUCKET = os.environ.get("S3_BUCKET", DEFAULT_BUCKET).strip()
BRONZE_STATS_PREFIX = os.environ.get(
    "BRONZE_STATS_PREFIX", "bronzeLayer/youtube/raw_statistics"
).strip().strip("/")
BRONZE_REF_PREFIX = os.environ.get(
    "BRONZE_REF_PREFIX", "bronzeLayer/youtube/raw_statistics_reference_data"
).strip().strip("/")
REGIONS = os.environ.get("YOUTUBE_REGIONS", "US,GB,CA,DE,FR,IN,JP,KR,MX,RU").split(",")
SNS_TOPIC = os.environ.get("SNS_ALERT_TOPIC_ARN", "")
API_BASE = "https://www.googleapis.com/youtube/v3"
MAX_RESULTS = 50


def fetch_trending_videos(region_code: str) -> dict:
    """Call the YouTube Data API for trending videos in a region."""
    params = urlencode({
        "part": "snippet,statistics,contentDetails",
        "chart": "mostPopular",
        "regionCode": region_code,
        "maxResults": MAX_RESULTS,
        "key": API_KEY,
    })
    url = f"{API_BASE}/videos?{params}"

    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_video_categories(region_code: str) -> dict:
    """Fetch the video category mapping for a region."""
    params = urlencode({
        "part": "snippet",
        "regionCode": region_code,
        "key": API_KEY,
    })
    url = f"{API_BASE}/videoCategories?{params}"

    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_to_s3(data: dict, bucket: str, key: str) -> dict:
    """Write JSON data to S3 with metadata."""
    body = json.dumps(data, ensure_ascii=False, indent=2)
    response = s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
        Metadata={
            "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "youtube_data_api_v3",
        },
    )
    return response


def send_alert(subject: str, message: str):
    """Send failure alert via SNS."""
    if SNS_TOPIC:
        sns_client.publish(
            TopicArn=SNS_TOPIC,
            Subject=subject[:100],
            Message=message,
        )


def lambda_handler(event, context):
    """
    Iterate over regions, fetch trending videos + categories,
    write raw JSON into Bronze folders (single bucket).
    """
    now = datetime.now(timezone.utc)
    date_partition = now.strftime("%Y-%m-%d")
    hour_partition = now.strftime("%H")
    ingestion_id = now.strftime("%Y%m%d_%H%M%S")

    results = {"success": [], "failed": []}

    for region in REGIONS:
        # S3 partitions use lowercase (region=us); API needs uppercase (US)
        region = region.strip().lower()
        region_api = region.upper()
        logger.info("Processing region: %s", region)

        # ── Fetch trending videos ────────────────────────────────────────
        try:
            trending_data = fetch_trending_videos(region_api)
            video_count = len(trending_data.get("items", []))

            trending_data["_pipeline_metadata"] = {
                "ingestion_id": ingestion_id,
                "region": region,
                "ingestion_timestamp": now.isoformat(),
                "video_count": video_count,
                "source": "youtube_data_api_v3",
            }

            # s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics/...
            s3_key = (
                f"{BRONZE_STATS_PREFIX}/"
                f"region={region}/"
                f"date={date_partition}/"
                f"hour={hour_partition}/"
                f"{ingestion_id}.json"
            )
            write_to_s3(trending_data, BUCKET, s3_key)
            logger.info("  Wrote %s videos → s3://%s/%s", video_count, BUCKET, s3_key)

        except (HTTPError, URLError) as e:
            logger.error("  API error for %s trending: %s", region, e)
            results["failed"].append({"region": region, "type": "trending", "error": str(e)})
            continue
        except Exception as e:
            logger.error("  Unexpected error for %s trending: %s", region, e)
            results["failed"].append({"region": region, "type": "trending", "error": str(e)})
            continue

        # ── Fetch category reference data ────────────────────────────────
        try:
            category_data = fetch_video_categories(region_api)
            category_data["_pipeline_metadata"] = {
                "ingestion_id": ingestion_id,
                "region": region,
                "ingestion_timestamp": now.isoformat(),
                "source": "youtube_data_api_v3",
            }

            # s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics_reference_data/...
            ref_key = (
                f"{BRONZE_REF_PREFIX}/"
                f"region={region}/"
                f"date={date_partition}/"
                f"{region}_category_id.json"
            )
            write_to_s3(category_data, BUCKET, ref_key)
            logger.info("  Wrote categories → s3://%s/%s", BUCKET, ref_key)

        except (HTTPError, URLError) as e:
            logger.error("  API error for %s categories: %s", region, e)
            results["failed"].append({"region": region, "type": "categories", "error": str(e)})
            continue

        results["success"].append(region)

    summary = (
        f"Ingestion {ingestion_id} complete. "
        f"Success: {len(results['success'])}/{len(REGIONS)} regions. "
        f"Failed: {len(results['failed'])}."
    )
    logger.info(summary)

    if results["failed"]:
        send_alert(
            subject=f"[YT Pipeline] Ingestion partial failure — {ingestion_id}",
            message=json.dumps(results, indent=2),
        )

    return {
        "statusCode": 200,
        "ingestion_id": ingestion_id,
        "results": results,
    }
