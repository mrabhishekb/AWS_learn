"""
Glue Job: Bronze → Silver (Statistics / Trending Videos)
────────────────────────────────────────────────────────
Reads raw video statistics DIRECTLY from S3 (no Glue catalog / crawler).
Supports Kaggle CSV and YouTube API JSON, then writes Parquet to Silver.

S3 layout (single bucket, folders = layers):
  Bronze stats:
    s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics/
  Bronze reference (NOT read here — json_to_parquet Lambda):
    s3://youtube-analytics-data-ap-south-01/bronzeLayer/youtube/raw_statistics_reference_data/
  Silver statistics:
    s3://youtube-analytics-data-ap-south-01/silverLayer/youtube/statistics/region=us/*.parquet
  Silver reference:
    s3://youtube-analytics-data-ap-south-01/silverLayer/youtube/reference_data/region=us/*.parquet

Job Parameters:
    --JOB_NAME              — Glue job name (auto-set)
    --s3_bucket             — optional, default youtube-analytics-data-ap-south-01
    --silver_database       — Silver Glue catalog database (e.g. youtube_db)
    --silver_table          — Silver statistics table (e.g. clean_statistics)
    --bronze_layer_prefix   — optional, default bronzeLayer/youtube/raw_statistics
    --silver_layer_prefix   — optional, default silverLayer
"""

import sys
from datetime import datetime, timezone

from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.dynamicframe import DynamicFrame

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, LongType, BooleanType
from pyspark.sql.window import Window

# ── Job Setup ────────────────────────────────────────────────────────────────
DEFAULT_BUCKET = "youtube-analytics-data-ap-south-01"
required_args = [
    "JOB_NAME",
    "silver_database",
    "silver_table",
]
optional_args = ["s3_bucket", "bronze_layer_prefix", "silver_layer_prefix"]

args = getResolvedOptions(sys.argv, required_args)
for opt in optional_args:
    try:
        args.update(getResolvedOptions(sys.argv, [opt]))
    except Exception:
        pass

sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)
logger = glueContext.get_logger()

# ── Config ───────────────────────────────────────────────────────────────────
BUCKET = args.get("s3_bucket", DEFAULT_BUCKET)
SILVER_DB = args["silver_database"]
SILVER_TABLE = args["silver_table"]
BRONZE_PREFIX = args.get(
    "bronze_layer_prefix", "bronzeLayer/youtube/raw_statistics"
).strip().strip("/")
SILVER_PREFIX = args.get("silver_layer_prefix", "silverLayer").strip().strip("/")

BRONZE_STATS_PATH = f"s3://{BUCKET}/{BRONZE_PREFIX}"
# Statistics only — reference data is written by json_to_parquet Lambda
SILVER_STATS_PATH = f"s3://{BUCKET}/{SILVER_PREFIX}/youtube/statistics/"

REGIONS = ["us", "gb", "ca", "de", "fr", "in", "jp", "kr", "mx", "ru"]

logger.info(f"Bronze (S3 direct): {BRONZE_STATS_PATH}")
logger.info(f"Silver statistics: {SILVER_STATS_PATH}")
logger.info(f"Silver catalog: {SILVER_DB}.{SILVER_TABLE}")


def pick_col(columns, *candidates):
    for name in candidates:
        if name in columns:
            return name
    return None


def col_or_null(columns, *candidates, cast=None, alias=None):
    name = pick_col(columns, *candidates)
    if name is None:
        c = F.lit(None)
        if cast is not None:
            c = c.cast(cast)
        return c.alias(alias) if alias else c
    expr = f"`{name}`" if "." in name else name
    c = F.col(expr)
    if cast is not None:
        c = c.cast(cast)
    return c.alias(alias) if alias else c


def read_bronze_json():
    """Read YouTube API JSON snapshots directly from S3 (Hive partitions)."""
    pattern = f"{BRONZE_STATS_PATH}/region=*/date=*/hour=*/*.json"
    logger.info(f"Reading API JSON: {pattern}")
    try:
        return (
            spark.read.option("basePath", BRONZE_STATS_PATH)
            .option("recursiveFileLookup", "false")
            .json(pattern)
        )
    except Exception as e:
        logger.warn(f"No API JSON read (or empty): {e}")
        return None


def read_bronze_csv():
    """Read Kaggle CSV files directly from S3 (shallow or date/hour depth)."""
    frames = []
    patterns = [
        f"{BRONZE_STATS_PATH}/region=*/*videos.csv",
        f"{BRONZE_STATS_PATH}/region=*/date=*/hour=*/*videos.csv",
    ]
    for pattern in patterns:
        logger.info(f"Reading CSV: {pattern}")
        try:
            part = (
                spark.read.option("header", True)
                .option("basePath", BRONZE_STATS_PATH)
                .csv(pattern)
            )
            if part.head(1):
                frames.append(part)
        except Exception as e:
            logger.warn(f"CSV pattern skipped ({pattern}): {e}")
    if not frames:
        return None
    out = frames[0]
    for other in frames[1:]:
        out = out.unionByName(other, allowMissingColumns=True)
    return out


def normalize_api(df):
    """Flatten YouTube API ListResponse rows to the silver schema."""
    columns = set(df.columns)
    logger.info(f"API JSON columns: {sorted(columns)}")

    if "items" in columns:
        logger.info("Exploding items[]...")
        keep = [c for c in ("region", "date", "hour") if c in columns]
        df = df.withColumn("item", F.explode_outer("items")).select(
            F.col("item.*"), *[F.col(c) for c in keep]
        )
        columns = set(df.columns)

    title = col_or_null(columns, "snippet.title", "snippet__title", alias="title", cast=StringType())
    if pick_col(columns, "snippet.title", "snippet__title") is None and "snippet" in columns:
        title = F.col("snippet.title").cast(StringType()).alias("title")

    channel_title = col_or_null(
        columns, "snippet.channelTitle", "snippet__channelTitle",
        alias="channel_title", cast=StringType(),
    )
    if pick_col(columns, "snippet.channelTitle", "snippet__channelTitle") is None and "snippet" in columns:
        channel_title = F.col("snippet.channelTitle").cast(StringType()).alias("channel_title")

    category_id = col_or_null(
        columns, "snippet.categoryId", "snippet__categoryId",
        alias="category_id", cast=LongType(),
    )
    if pick_col(columns, "snippet.categoryId", "snippet__categoryId") is None and "snippet" in columns:
        category_id = F.col("snippet.categoryId").cast(LongType()).alias("category_id")

    publish_time = col_or_null(
        columns, "snippet.publishedAt", "snippet__publishedAt",
        alias="publish_time", cast=StringType(),
    )
    if pick_col(columns, "snippet.publishedAt", "snippet__publishedAt") is None and "snippet" in columns:
        publish_time = F.col("snippet.publishedAt").cast(StringType()).alias("publish_time")

    tags = col_or_null(columns, "snippet.tags", "snippet__tags", alias="tags", cast=StringType())
    if pick_col(columns, "snippet.tags", "snippet__tags") is None and "snippet" in columns:
        tags = F.concat_ws("|", F.col("snippet.tags")).alias("tags")

    views = col_or_null(
        columns, "statistics.viewCount", "statistics__viewCount",
        alias="views", cast=LongType(),
    )
    if pick_col(columns, "statistics.viewCount", "statistics__viewCount") is None and "statistics" in columns:
        views = F.col("statistics.viewCount").cast(LongType()).alias("views")

    likes = col_or_null(
        columns, "statistics.likeCount", "statistics__likeCount",
        alias="likes", cast=LongType(),
    )
    if pick_col(columns, "statistics.likeCount", "statistics__likeCount") is None and "statistics" in columns:
        likes = F.col("statistics.likeCount").cast(LongType()).alias("likes")

    if pick_col(columns, "statistics.dislikeCount", "statistics__dislikeCount"):
        dislikes = col_or_null(
            columns, "statistics.dislikeCount", "statistics__dislikeCount",
            alias="dislikes", cast=LongType(),
        )
    elif "statistics" in columns:
        dislikes = F.coalesce(
            F.col("statistics.dislikeCount").cast(LongType()), F.lit(0)
        ).alias("dislikes")
    else:
        dislikes = F.lit(0).cast(LongType()).alias("dislikes")

    comment_count = col_or_null(
        columns, "statistics.commentCount", "statistics__commentCount",
        alias="comment_count", cast=LongType(),
    )
    if pick_col(columns, "statistics.commentCount", "statistics__commentCount") is None and "statistics" in columns:
        comment_count = F.col("statistics.commentCount").cast(LongType()).alias("comment_count")

    thumbnail_link = col_or_null(
        columns,
        "snippet.thumbnails.default.url",
        "snippet__thumbnails__default__url",
        alias="thumbnail_link",
        cast=StringType(),
    )
    if (
        pick_col(columns, "snippet.thumbnails.default.url", "snippet__thumbnails__default__url") is None
        and "snippet" in columns
    ):
        thumbnail_link = F.col("snippet.thumbnails.default.url").cast(StringType()).alias("thumbnail_link")

    description = col_or_null(
        columns, "snippet.description", "snippet__description",
        alias="description", cast=StringType(),
    )
    if pick_col(columns, "snippet.description", "snippet__description") is None and "snippet" in columns:
        description = F.col("snippet.description").cast(StringType()).alias("description")

    trending_date = (
        F.col("date").cast(StringType()).alias("trending_date")
        if "date" in columns
        else F.lit(datetime.now(timezone.utc).strftime("%Y-%m-%d")).alias("trending_date")
    )

    select_exprs = [
        F.col("id").cast(StringType()).alias("video_id"),
        trending_date,
        title,
        channel_title,
        category_id,
        publish_time,
        tags,
        views,
        likes,
        dislikes,
        comment_count,
        thumbnail_link,
        F.lit(False).alias("comments_disabled"),
        F.lit(False).alias("ratings_disabled"),
        F.lit(False).alias("video_error_or_removed"),
        description,
        F.col("region").cast(StringType()),
    ]
    if "hour" in columns:
        select_exprs.append(F.col("hour").cast(StringType()).alias("hour"))

    return df.select(*select_exprs)


def normalize_csv(df):
    """Cast Kaggle CSV columns to the silver schema."""
    columns = set(df.columns)
    logger.info(f"CSV columns: {sorted(columns)}")

    select_exprs = [
        F.col("video_id").cast(StringType()),
        F.col("trending_date").cast(StringType()),
        F.col("title").cast(StringType()),
        F.col("channel_title").cast(StringType()),
        F.col("category_id").cast(LongType()),
        F.col("publish_time").cast(StringType()),
        F.col("tags").cast(StringType()),
        F.col("views").cast(LongType()),
        F.col("likes").cast(LongType()),
        F.col("dislikes").cast(LongType()),
        F.col("comment_count").cast(LongType()),
        F.col("thumbnail_link").cast(StringType()),
        F.col("comments_disabled").cast(BooleanType()),
        F.col("ratings_disabled").cast(BooleanType()),
        F.col("video_error_or_removed").cast(BooleanType()),
        F.col("description").cast(StringType()),
        F.col("region").cast(StringType()),
    ]
    if "date" in columns:
        select_exprs.append(F.col("date").cast(StringType()))
    if "hour" in columns:
        select_exprs.append(F.col("hour").cast(StringType()))
    return df.select(*select_exprs)


# ── Step 1: Read Bronze directly from S3 ─────────────────────────────────────
logger.info("Reading Bronze statistics from S3 (bypassing catalog)...")

frames = []
df_json = read_bronze_json()
if df_json is not None and df_json.head(1):
    frames.append(normalize_api(df_json))

df_csv = read_bronze_csv()
if df_csv is not None and df_csv.head(1):
    frames.append(normalize_csv(df_csv))

if not frames:
    logger.info("No Bronze statistics found under S3 path. Committing empty job.")
    job.commit()
    sys.exit(0)

df = frames[0]
for other in frames[1:]:
    df = df.unionByName(other, allowMissingColumns=True)

# Keep configured regions only
df = df.filter(F.lower(F.trim(F.col("region"))).isin(REGIONS))

initial_count = df.count()
logger.info(f"Bronze records read from S3: {initial_count}")

if initial_count == 0:
    logger.info("No records after region filter. Committing empty job.")
else:
    # ── Step 2: Data Cleansing ──────────────────────────────────────────────
    logger.info("Cleansing data...")

    df = df.filter(F.col("video_id").isNotNull())
    df = df.withColumn("region", F.lower(F.trim(F.col("region"))))

    df = df.withColumn(
        "trending_date_parsed",
        F.when(
            F.col("trending_date").rlike(r"^\d{2}\.\d{2}\.\d{2}$"),
            F.to_date(F.col("trending_date"), "yy.dd.MM"),
        ).otherwise(F.to_date(F.col("trending_date"))),
    )

    for col_name in ["views", "likes", "dislikes", "comment_count"]:
        df = df.withColumn(col_name, F.coalesce(F.col(col_name), F.lit(0)))

    df = df.withColumn(
        "like_ratio",
        F.when(
            F.col("views") > 0,
            F.round(F.col("likes") / F.col("views") * 100, 4),
        ).otherwise(0.0),
    )
    df = df.withColumn(
        "engagement_rate",
        F.when(
            F.col("views") > 0,
            F.round(
                (F.col("likes") + F.col("dislikes") + F.col("comment_count"))
                / F.col("views")
                * 100,
                4,
            ),
        ).otherwise(0.0),
    )

    df = df.withColumn("_processed_at", F.current_timestamp())
    df = df.withColumn("_job_name", F.lit(args["JOB_NAME"]))

    # ── Step 3: Deduplication ───────────────────────────────────────────────
    logger.info("Deduplicating...")

    window = Window.partitionBy("video_id", "region", "trending_date_parsed").orderBy(
        F.col("_processed_at").desc()
    )
    df = (
        df.withColumn("_row_num", F.row_number().over(window))
        .filter(F.col("_row_num") == 1)
        .drop("_row_num")
    )

    clean_count = df.count()
    logger.info(
        f"After cleansing & dedup: {clean_count} records "
        f"(removed {initial_count - clean_count})"
    )

    # ── Step 4: Data Quality Checks ─────────────────────────────────────────
    logger.info("Running data quality checks...")
    null_counts = {}
    for col_name in ["video_id", "title", "channel_title", "views"]:
        null_count = df.filter(F.col(col_name).isNull()).count()
        null_counts[col_name] = null_count
        if null_count > 0:
            logger.warn(f"  DQ WARNING: {col_name} has {null_count} null values")

    negative_views = df.filter(F.col("views") < 0).count()
    if negative_views > 0:
        logger.warn(f"  DQ WARNING: {negative_views} records with negative views")

    logger.info(f"  DQ check complete. Null counts: {null_counts}")

    # ── Step 5: Write Silver statistics (separate from reference_data) ──────
    logger.info(f"Writing Silver statistics → {SILVER_STATS_PATH}")

    dynamic_frame = DynamicFrame.fromDF(df, glueContext, "silver_statistics")

    sink = glueContext.getSink(
        connection_type="s3",
        path=SILVER_STATS_PATH,
        enableUpdateCatalog=True,
        updateBehavior="UPDATE_IN_DATABASE",
        partitionKeys=["region"],
    )
    sink.setCatalogInfo(catalogDatabase=SILVER_DB, catalogTableName=SILVER_TABLE)
    sink.setFormat("glueparquet", compression="snappy")
    sink.writeFrame(dynamic_frame)

    logger.info(f"Silver statistics write complete. {clean_count} records written.")

job.commit()
