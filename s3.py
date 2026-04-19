import csv
import io

import boto3
from dotenv import load_dotenv
import os

load_dotenv()

BUCKET = "mrabhishekbbucket"
KEY = "test/data_1.csv"

s3_client = boto3.client("s3", region_name="us-east-1", aws_access_key_id=os.environ.get('aws_access_key_id'), aws_secret_access_key=os.environ.get('aws_secret_access_key'))

response = s3_client.get_object(Bucket=BUCKET, Key=KEY)
raw = response["Body"].read().decode("utf-8-sig")
reader = csv.DictReader(io.StringIO(raw))

if reader.fieldnames is None or "Customer" not in reader.fieldnames:
    raise ValueError(
        f'Expected a "Customer" column. Found columns: {reader.fieldnames!r}'
    )

customers = {row["Customer"].strip() for row in reader if row.get("Customer")}
num_customers = len(customers)

print(f"Unique customers (Customer column): {num_customers}")
