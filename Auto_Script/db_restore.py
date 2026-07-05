import gzip
import os
import re
import subprocess
import time

from botocore.exceptions import BotoCoreError, ClientError

from config import AppConfig
from db_common import (
    DbConnectionInfo,
    build_s3_client,
    decode_subprocess_output,
    prompt_connection_info,
    remove_if_exists,
    write_log_entry,
)
from exception import (
    CompressionError,
    InputValidationError,
    MysqlImportError,
    S3DownloadError,
)

_FILENAME_RE = re.compile(r"^(?P<db>.+)_\d{8}_\d{6}\.sql\.gz$")

_RECENT_BACKUPS_LIMIT = 5


def _extract_db_name(filename: str) -> str:
    m = _FILENAME_RE.match(filename)
    return m.group("db") if m else "unknown"


def _list_recent_backups(s3_client, config: AppConfig) -> dict[int, str]:
    prefix = f"{config.s3_key_prefix}/" if config.s3_key_prefix else ""

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        objects = []
        for page in paginator.paginate(Bucket=config.s3_bucket, Prefix=prefix):
            objects.extend(page.get("Contents", []))
    except (BotoCoreError, ClientError) as e:
        raise S3DownloadError(f"S3 목록 조회에 실패했습니다: {e}", cause=e) from e

    candidates = [obj for obj in objects if obj["Key"].endswith(".sql.gz")]
    candidates.sort(key=lambda obj: obj["LastModified"], reverse=True)
    top = candidates[:_RECENT_BACKUPS_LIMIT]

    return {i: obj["Key"] for i, obj in enumerate(top, start=1)}


def _prompt_backup_selection(candidates: dict[int, str]) -> str:
    print("\n----복구 가능한 백업 목록 (최신순)----")
    for num, key in candidates.items():
        print(f"{num}. {os.path.basename(key)}")

    while True:
        raw = input("복구할 백업 번호를 입력하세요: ").strip()
        try:
            num = int(raw)
        except ValueError:
            print(f"[입력 오류] 숫자가 아닌 입력입니다: {raw}")
            continue
        if num not in candidates:
            print(f"[입력 오류] 범위를 벗어난 번호입니다: {num}")
            continue
        return candidates[num]


def _download_from_s3(key: str, config: AppConfig, s3_client) -> str:
    local_path = os.path.join(os.getcwd(), os.path.basename(key))
    try:
        s3_client.download_file(config.s3_bucket, key, local_path)
    except (BotoCoreError, ClientError) as e:
        remove_if_exists(local_path)
        raise S3DownloadError(f"S3 다운로드에 실패했습니다: {key} ({e})", cause=e) from e
    return local_path


def _run_mysql_import_from_gzip(gz_path: str, conn_info: DbConnectionInfo, config: AppConfig) -> None:
    cmd = [
        config.mysql_path,
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
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except FileNotFoundError as e:
        raise MysqlImportError(
            f"mysql 실행 파일을 찾을 수 없습니다: {config.mysql_path}", cause=e
        ) from e

    try:
        with gzip.open(gz_path, "rb") as gz_in:
            assert proc.stdin is not None
            for chunk in iter(lambda: gz_in.read(1024 * 1024), b""):
                proc.stdin.write(chunk)
            proc.stdin.close()
    except OSError as e:
        proc.kill()
        proc.wait()
        raise CompressionError(f"압축 해제 중 오류가 발생했습니다: {e}", cause=e) from e

    stderr_output = proc.stderr.read() if proc.stderr else b""
    returncode = proc.wait()

    if returncode != 0:
        raise MysqlImportError(
            f"mysql 실행에 실패했습니다 (exit={returncode}): "
            f"{decode_subprocess_output(stderr_output).strip()}"
        )


def _restore_selected_backup(key: str, config: AppConfig, s3_client) -> None:
    start = time.monotonic()
    gz_path: str | None = None
    conn_info: DbConnectionInfo | None = None
    error_message: str | None = None
    success = False

    try:
        gz_path = _download_from_s3(key, config, s3_client)
        conn_info = prompt_connection_info()
        _run_mysql_import_from_gzip(gz_path, conn_info, config)
        success = True
    except (S3DownloadError, MysqlImportError, CompressionError, InputValidationError) as e:
        error_message = e.message
        raise
    finally:
        elapsed = time.monotonic() - start
        file_size = os.path.getsize(gz_path) if gz_path and os.path.exists(gz_path) else 0
        write_log_entry(
            config,
            operation_type="RESTORE",
            db_name=_extract_db_name(os.path.basename(key)),
            file_size=file_size,
            elapsed_seconds=elapsed,
            success=success,
            error_message=error_message,
        )
        if success and gz_path:
            remove_if_exists(gz_path)
        if conn_info:
            conn_info.clear()


def run(config: AppConfig) -> None:
    s3_client = build_s3_client(config)
    candidates = _list_recent_backups(s3_client, config)
    print("\n========================")
    if not candidates:
        print("복구 가능한 백업이 없습니다.")
        return

    selected_key = _prompt_backup_selection(candidates)
    print(f"\n[{os.path.basename(selected_key)}] 복구를 시작합니다...")
    _restore_selected_backup(selected_key, config, s3_client)
    print(f"\n[{os.path.basename(selected_key)}] 복구가 완료되었습니다.")
