import gzip
import os
import subprocess
import time

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError

from config import AppConfig
from db_common import (
    DbConnectionInfo,
    decode_subprocess_output,
    list_databases,
    prompt_connection_info,
    remove_if_exists,
    write_log_entry,
)
from exception import (
    CompressionError,
    InputValidationError,
    MysqldumpError,
    S3UploadError,
)
from utils import parse_db_selection, timestamp_for_filename


def _prompt_db_selection(dbs: list[str]) -> list[str]:
    print("\n----DB 목록----")
    print("0. 전체 선택")
    for i, name in enumerate(dbs, start=1):
        print(f"{i}. {name}")

    while True:
        raw = input(
            "백업할 DB 번호를 입력하세요 (쉼표로 구분, 전체 선택은 0만 단독 입력): "
        )
        try:
            indices = parse_db_selection(raw, len(dbs))
        except InputValidationError as e:
            print(f"[입력 오류] {e.message}")
            continue
        return [dbs[i] for i in indices]


def _run_mysqldump_to_gzip(db_name: str, conn_info: DbConnectionInfo, config: AppConfig) -> str:
    gz_path = os.path.join(os.getcwd(), f"{db_name}_{timestamp_for_filename()}.sql.gz")

    cmd = [
        config.mysqldump_path,
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--databases",
        db_name,
        "-h",
        conn_info.host,
        "-P",
        str(conn_info.port),
        "-u",
        conn_info.user,
    ]

    env = os.environ.copy()
    env["MYSQL_PWD"] = conn_info.password

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except FileNotFoundError as e:
        raise MysqldumpError(
            f"mysqldump 실행 파일을 찾을 수 없습니다: {config.mysqldump_path}", cause=e
        ) from e

    try:
        with gzip.open(gz_path, "wb") as gz_out:
            assert proc.stdout is not None
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                gz_out.write(chunk)
    except OSError as e:
        proc.kill()
        proc.wait()
        remove_if_exists(gz_path)
        raise CompressionError(f"압축 중 오류가 발생했습니다: {e}", cause=e) from e

    stderr_output = proc.stderr.read() if proc.stderr else b""
    returncode = proc.wait()

    if returncode != 0:
        remove_if_exists(gz_path)
        raise MysqldumpError(
            f"mysqldump 실행에 실패했습니다 (exit={returncode}): "
            f"{decode_subprocess_output(stderr_output).strip()}"
        )

    return gz_path


def _build_s3_key(local_path: str, config: AppConfig) -> str:
    filename = os.path.basename(local_path)
    if config.s3_key_prefix:
        return f"{config.s3_key_prefix}/{filename}"
    return filename


def _upload_to_s3(local_path: str, config: AppConfig, s3_client) -> None:
    key = _build_s3_key(local_path, config)
    try:
        s3_client.upload_file(local_path, config.s3_bucket, key)
    except (BotoCoreError, ClientError, S3UploadFailedError) as e:
        raise S3UploadError(f"S3 업로드에 실패했습니다: {key} ({e})", cause=e) from e


def _backup_one_db(db_name: str, conn_info: DbConnectionInfo, config: AppConfig, s3_client) -> None:
    start = time.monotonic()
    gz_path: str | None = None
    error_message: str | None = None
    success = False

    try:
        gz_path = _run_mysqldump_to_gzip(db_name, conn_info, config)
        _upload_to_s3(gz_path, config, s3_client)
        success = True
    except (MysqldumpError, CompressionError, S3UploadError) as e:
        error_message = e.message
        raise
    finally:
        elapsed = time.monotonic() - start
        file_size = os.path.getsize(gz_path) if gz_path and os.path.exists(gz_path) else 0
        write_log_entry(
            config,
            operation_type="BACKUP",
            db_name=db_name,
            file_size=file_size,
            elapsed_seconds=elapsed,
            success=success,
            error_message=error_message,
        )
        if success and gz_path:
            remove_if_exists(gz_path)


def run(config: AppConfig) -> None:
    conn_info = prompt_connection_info()
    try:
        dbs = list_databases(conn_info)
        if not dbs:
            print("백업 가능한 사용자 DB가 없습니다.")
            return
        selected = _prompt_db_selection(dbs)

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
            region_name=config.aws_region,
        )

        for db_name in selected:
            print(f"[{db_name}] 백업을 시작합니다...")
            _backup_one_db(db_name, conn_info, config, s3_client)
            print(f"[{db_name}] 백업이 완료되었습니다.")
    finally:
        conn_info.clear()
        del conn_info
