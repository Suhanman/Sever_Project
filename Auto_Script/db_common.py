import os
from dataclasses import dataclass
from datetime import datetime

import boto3
import pymysql

from config import AppConfig
from exception import DbConnectionError, InputValidationError, LogWriteError
from utils import format_bytes, read_masked_password

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


def prompt_connection_info() -> DbConnectionInfo:
    host = input("DB Host/IP: ").strip()
    port_raw = input("DB Port: ").strip()
    try:
        port = int(port_raw)
    except ValueError as e:
        raise InputValidationError(f"포트는 숫자여야 합니다: {port_raw}") from e
    user = input("DB User: ").strip()
    password = read_masked_password("DB Password: ")
    return DbConnectionInfo(host=host, port=port, user=user, password=password)


def list_databases(conn_info: DbConnectionInfo) -> list[str]:
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


def remove_if_exists(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def build_s3_client(config: AppConfig):
    return boto3.client(
        "s3",
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        region_name=config.aws_region,
    )


def decode_subprocess_output(raw: bytes) -> str:
    # mysqldump/mysql은 UTF-8로 출력하지만, Windows 콘솔용 실행 파일은 OS 메시지
    # 일부를 시스템 ANSI 코드페이지로 섞어 낼 수 있다. UTF-8로 먼저 시도하고
    # 실패하면 로케일 기본 인코딩으로 재시도한다.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        import locale

        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def write_log_entry(
    config: AppConfig,
    *,
    operation_type: str,
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
            f"type={operation_type} | "
            f"db={db_name} | size={format_bytes(file_size)} | "
            f"elapsed={elapsed_seconds:.2f}s | "
            f"status={'SUCCESS' if success else 'FAILURE'} | "
            f"error={error_message or '-'}\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        raise LogWriteError(f"로그 파일 쓰기에 실패했습니다: {config.log_dir} ({e})", cause=e) from e
