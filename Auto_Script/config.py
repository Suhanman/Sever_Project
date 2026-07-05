import os
from dataclasses import dataclass

from dotenv import load_dotenv

REQUIRED_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_DEFAULT_REGION",
    "S3_BUCKET_NAME",
)


@dataclass(frozen=True)
class AppConfig:
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket: str
    s3_key_prefix: str
    mysqldump_path: str
    mysql_path: str
    log_dir: str


def load_config() -> AppConfig:
    load_dotenv()

    missing = [key for key in REQUIRED_KEYS if not os.environ.get(key)]
    if missing:
        raise EnvironmentError(
            f".env에 다음 값이 설정되어 있지 않습니다: {', '.join(missing)}"
        )

    return AppConfig(
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        aws_region=os.environ["AWS_DEFAULT_REGION"],
        s3_bucket=os.environ["S3_BUCKET_NAME"],
        s3_key_prefix=os.environ.get("S3_KEY_PREFIX", "").strip("/"),
        mysqldump_path=os.environ.get("MYSQLDUMP_PATH", "mysqldump"),
        mysql_path=os.environ.get("MYSQL_PATH", "mysql"),
        log_dir=os.environ.get("DBMG_LOG_DIR", "/var/log/dbmg"),
    )
