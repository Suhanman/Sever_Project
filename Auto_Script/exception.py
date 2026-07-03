from enum import Enum, auto


class RecoveryAction(Enum):
    RETRY_INPUT = auto()   # 해당 입력 단계만 재입력
    RETRY = auto()         # 동일 작업 재시도 (횟수 제한)
    RESTART = auto()       # 현재 작업 중단, 메인 메뉴로
    EXIT = auto()          # 오류 원인 표시 후 아무 키 누르면 즉시 종료


class DbmgError(Exception):
    recovery_action: RecoveryAction = RecoveryAction.RESTART

    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause


class DbConnectionError(DbmgError):
    recovery_action = RecoveryAction.RESTART


class InputValidationError(DbmgError):
    recovery_action = RecoveryAction.RETRY_INPUT


class MysqldumpError(DbmgError):
    recovery_action = RecoveryAction.RESTART


class CompressionError(DbmgError):
    recovery_action = RecoveryAction.RETRY_INPUT


class S3UploadError(DbmgError):
    recovery_action = RecoveryAction.RETRY_INPUT


class LogWriteError(DbmgError):
    recovery_action = RecoveryAction.EXIT
