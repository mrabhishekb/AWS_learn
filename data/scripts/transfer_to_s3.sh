# Manual Kaggle → Bronze upload
# Single bucket: youtube-analytics-data-ap-south-01
#
# CSVs use region/date/hour depth (same as API JSON) so Glue creates one table.
#   s3://.../bronzeLayer/youtube/raw_statistics/region=xx/date=kaggle/hour=00/*videos.csv
#   s3://.../bronzeLayer/youtube/raw_statistics_reference_data/region=xx/*_category_id.json

BUCKET="${BUCKET:-youtube-analytics-data-ap-south-01}"
STATS_PREFIX="bronzeLayer/youtube/raw_statistics"
REF_PREFIX="bronzeLayer/youtube/raw_statistics_reference_data"

REGIONS=(ca de fr gb in jp kr mx ru us)

for region in "${REGIONS[@]}"; do
  upper=$(echo "$region" | tr '[:lower:]' '[:upper:]')
  csv="${upper}videos.csv"
  json="${upper}_category_id.json"

  if [[ -f "$csv" ]]; then
    aws s3 cp "$csv" "s3://${BUCKET}/${STATS_PREFIX}/region=${region}/date=kaggle/hour=00/"
  else
    echo "Skip missing: $csv"
  fi

  if [[ -f "$json" ]]; then
    aws s3 cp "$json" "s3://${BUCKET}/${REF_PREFIX}/region=${region}/"
  else
    echo "Skip missing: $json"
  fi
done
