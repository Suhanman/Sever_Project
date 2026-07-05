import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import db_backup
import db_restore
from config import AppConfig, load_config
from exception import DbmgError, RecoveryAction
from utils import wait_for_keypress

MENU: dict[str, tuple[str, "callable"]] = {
    "1": ("DB -> S3 백업 (MySQL)", db_backup.run),
    "2": ("S3 -> DB 복구 (MySQL)", db_restore.run),
}

MAX_RETRIES = 3


def print_menu() -> None:
    print("\n=== 서버 자동화 스크립트 ===")
    for key, (label, _) in MENU.items():
        print(f"{key}. {label}")
    print("q. 종료")


def run_tool_with_recovery(handler, config: AppConfig) -> None:
    attempts = 0
    while True:
        try:
            handler(config)
            return
        except DbmgError as err:
            print(f"\n[오류] {err.message}")
            if err.cause:
                print(f"  (원인: {err.cause})")

            if err.recovery_action == RecoveryAction.RETRY:
                attempts += 1
                if attempts >= MAX_RETRIES:
                    print("최대 재시도 횟수를 초과했습니다. 메인 메뉴로 돌아갑니다.")
                    return
                print("작업을 재시도합니다...")
                continue

            if err.recovery_action == RecoveryAction.EXIT:
                wait_for_keypress()
                sys.exit(1)

            # RESTART, 그리고 여기까지 올라온 RETRY_INPUT은
            # main.py 입장에서 되감을 특정 입력 단계가 없으므로 동일하게 처리한다.
            print("메인 메뉴로 돌아갑니다.")
            return
        except KeyboardInterrupt:
            print("\n중단되었습니다. 메인 메뉴로 돌아갑니다.")
            return


def main() -> None:
    try:
        config = load_config()
    except EnvironmentError as e:
        print(f"[설정 오류] {e}")
        sys.exit(1)

    while True:
        print_menu()
        choice = input("번호 선택: ").strip()
        if choice.lower() == "q":
            print("종료합니다.")
            break

        entry = MENU.get(choice)
        if entry is None:
            print("잘못된 번호입니다.")
            continue

        _, handler = entry
        run_tool_with_recovery(handler, config)


if __name__ == "__main__":
    main()
