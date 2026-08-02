# Manual Kaggle → Bronze upload
# Single bucket: youtube-analytics-data-ap-south-01
# Layer folders: bronzeLayer / silverLayer / goldLayer
#
# IMPORTANT for Glue crawlers:
#   CSVs must use the SAME partition depth as API JSON (region/date/hour),
#   otherwise Glue creates one table per file/folder.
#
#   s3://.../bronzeLayer/youtube/raw_statistics/region=xx/date=kaggle/hour=00/*videos.csv
#   s3://.../bronzeLayer/youtube/raw_statistics_reference_data/region=xx/*_category_id.json

$Bucket = if ($env:BUCKET) { $env:BUCKET } else { "youtube-analytics-data-ap-south-01" }
$StatsPrefix = "bronzeLayer/youtube/raw_statistics"
$RefPrefix = "bronzeLayer/youtube/raw_statistics_reference_data"
$Regions = @("ca", "de", "fr", "gb", "in", "jp", "kr", "mx", "ru", "us")

foreach ($region in $Regions) {
    $upper = $region.ToUpper()
    $csv = "${upper}videos.csv"
    $json = "${upper}_category_id.json"

    if (Test-Path $csv) {
        # Match API JSON partition depth so the crawler creates ONE stats table
        aws s3 cp $csv "s3://$Bucket/$StatsPrefix/region=$region/date=kaggle/hour=00/"
    } else {
        Write-Host "Skip missing: $csv"
    }

    if (Test-Path $json) {
        aws s3 cp $json "s3://$Bucket/$RefPrefix/region=$region/"
    } else {
        Write-Host "Skip missing: $json"
    }
}
