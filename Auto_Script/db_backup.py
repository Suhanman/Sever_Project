import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime

import boto3
import pymysql
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError

from config import AppConfig
from exception import (
    CompressionError,
    DbConnectionError,
    InputValidationError,
    LogWriteError,
    MysqldumpError,
    S3UploadError,
)
from utils import (
    format_bytes,
    parse_db_selection,
    read_masked_password,
    timestamp_for_filename,
)

SYSTEM_SCHEMAS = {"information_schema", "performance_schema", "mysql", "sys"}


@dataclass
class DbConnectionInfo:
    host: str
    port: int
    user: str
    password: str

    def clear(self) -> None:
        # Python 문자열은 불변이라 메모리 완전 제로화는 보장할 수 없다.
        # 참조를 끊어 GC 대상이 되게 하는 최선의 완화책만 적용한다.
        self.password = ""


def _prompt_connection_info() -> DbConnectionInfo:
    host = input("DB Host/IP: ").strip()
    port_raw = input("DB Port: ").strip()
    try:
        port = int(port_raw)
    except ValueError as e:
        raise InputValidationError(f"포트는 숫자여야 합니다: {port_raw}") from e
    user = input("DB User: ").strip()
    password = read_masked_password("DB Password: ")
    return DbConnectionInfo(host=host, port=port, user=user, password=password)


def _list_databases(conn_info: DbConnectionInfo) -> list[str]:
    try:
        connection = pymysql.connect(
            host=conn_info.host,
            port=conn_info.port,
            user=conn_info.user,
            password=conn_info.password,
            connect_timeout=10,
        )
    except Exception as e:
        raise DbConnectionError(f"DB 연결에 실패했습니다: {e}", cause=e) from e

    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            names = [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()

    return sorted(name for name in names if name not in SYSTEM_SCHEMAS)


def _prompt_db_selection(dbs: list[str]) -> list[str]:
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
    import gzip

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
        _remove_if_exists(gz_path)
        raise CompressionError(f"압축 중 오류가 발생했습니다: {e}", cause=e) from e

    stderr_output = proc.stderr.read() if proc.stderr else b""
    returncode = proc.wait()

    if returncode != 0:
        _remove_if_exists(gz_path)
        raise MysqldumpError(
            f"mysqldump 실행에 실패했습니다 (exit={returncode}): "
            f"{_decode_subprocess_output(stderr_output).strip()}"
        )

    return gz_path


def _remove_if_exists(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def _decode_subprocess_output(raw: bytes) -> str:
    # mysqldump는 UTF-8로 출력하지만, Windows 콘솔용 mysqldump.exe는 OS 메시지
    # 일부를 시스템 ANSI 코드페이지로 섞어 낼 수 있다. UTF-8로 먼저 시도하고
    # 실패하면 로케일 기본 인코딩으로 재시도한다.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import locale

        return raw.decode(locale.getpreferredencoding(False), errors="replace")


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


def _write_log_entry(
    config: AppConfig,
    *,
    db_name: str,
    file_size: int,
    elapsed_seconds: float,
    success: bool,
    error_message: str | None,
) -> None:
    try:
        os.makedirs(config.log_dir, exist_ok=True)
        log_path = os.path.join(config.log_dir, "dbmg.log")
        line = (
            f"{datetime.now().isoformat()} | "
            f"db={db_name} | size={format_bytes(file_size)} | "
            f"elapsed={elapsed_seconds:.2f}s | "
            f"status={'SUCCESS' if success else 'FAILURE'} | "
            f"error={error_message or '-'}\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        raise LogWriteError(f"로그 파일 쓰기에 실패했습니다: {config.log_dir} ({e})", cause=e) from e


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
        _write_log_entry(
            config,
            db_name=db_name,
            file_size=file_size,
            elapsed_seconds=elapsed,
            success=success,
            error_message=error_message,
        )
        if success and gz_path:
            _remove_if_exists(gz_path)


def run(config: AppConfig) -> None:
    conn_info = _prompt_connection_info()
    try:
        dbs = _list_databases(conn_info)
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
