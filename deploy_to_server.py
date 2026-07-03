"""Auto_Script 폴더를 실제 리눅스 서버로 scp를 통해 복사하는 배포 스크립트.

DBMG 패치 프로그램(Auto_Script/) 자체와는 별도의 독립 실행 스크립트다.
시스템에 설치된 ssh/scp(OpenSSH 클라이언트)를 그대로 호출한다 - 비밀번호 입력은
ssh/scp가 터미널에서 직접 처리한다.
"""

import os
import subprocess
import sys

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_SOURCE_DIR = os.path.join(SCRIPT_DIR, "Auto_Script")
SKIP_NAMES = {".env", "__pycache__"}
CONNECT_TIMEOUT = "15"

load_dotenv(os.path.join(SCRIPT_DIR, ".env"))


def main() -> None:
    if not os.path.isdir(LOCAL_SOURCE_DIR):
        print(f"Auto_Script 폴더를 찾을 수 없습니다: {LOCAL_SOURCE_DIR}")
        sys.exit(1)

    host = os.environ.get("DEPLOY_HOST") or input("서버 Host/IP: ").strip()
    port = os.environ.get("DEPLOY_PORT") or input("서버 Port (기본 22): ").strip() or "22"
    user = os.environ.get("DEPLOY_USER") or input("서버 User: ").strip()
    remote_base = os.environ.get("DEPLOY_PATH") or input(
        "서버에 저장할 경로 (예: /home/user/Auto_Script): "
    ).strip()

    sources = [
        os.path.join(LOCAL_SOURCE_DIR, name)
        for name in sorted(os.listdir(LOCAL_SOURCE_DIR))
        if name not in SKIP_NAMES
    ]
    if not sources:
        print("업로드할 파일이 없습니다.")
        sys.exit(1)

    destination = f"{user}@{host}"

    print(f"업로드를 시작합니다: {LOCAL_SOURCE_DIR} -> {destination}:{remote_base}")
    print("(대상 경로가 서버에 미리 존재해야 합니다.)")
    scp_cmd = [
        "scp", "-r", "-P", port, "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
        *sources, f"{destination}:{remote_base}",
    ]
    result = subprocess.run(scp_cmd)
    if result.returncode != 0:
        print("[오류] 업로드 중 문제가 발생했습니다.")
        sys.exit(1)

    print("업로드가 완료되었습니다.")


if __name__ == "__main__":
    main()
