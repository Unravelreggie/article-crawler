from io import BytesIO

import boto3
from botocore.exceptions import ClientError

from .config import settings


def client():
    cfg = settings()
    return boto3.client(
        "s3", endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name="us-east-1",
    )


def put_pdf(key: str, content: bytes) -> None:
    cfg = settings()
    s3 = client()
    try:
        s3.head_bucket(Bucket=cfg.s3_bucket)
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise
        try:
            s3.create_bucket(Bucket=cfg.s3_bucket)
        except ClientError as create_exc:
            if create_exc.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                raise
    s3.put_object(Bucket=cfg.s3_bucket, Key=key, Body=content, ContentType="application/pdf")


def get_pdf(key: str) -> bytes:
    return client().get_object(Bucket=settings().s3_bucket, Key=key)["Body"].read()
