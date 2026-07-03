import sys
from datetime import datetime

from exception import InputValidationError


def read_masked_password(prompt: str = "DB Password: ") -> str:
    if sys.platform == "win32":
        return _read_masked_windows(prompt)
    return _read_masked_unix(prompt)


def _read_masked_windows(prompt: str) -> str:
    import msvcrt

    print(prompt, end="", flush=True)
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print()
            break
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x08",):
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        chars.append(ch)
        print("*", end="", flush=True)
    return "".join(chars)


def _read_masked_unix(prompt: str) -> str:
    import termios
    import tty

    print(prompt, end="", flush=True)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    chars: list[str] = []
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                print()
                break
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch in ("\x7f", "\x08"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            chars.append(ch)
            sys.stdout.write("*")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return "".join(chars)


def wait_for_keypress(message: str = "계속하려면 아무 키나 누르세요...") -> None:
    print(message, end="", flush=True)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.getch()
        print()
    else:
        input()


def timestamp_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def parse_db_selection(raw: str, db_count: int) -> list[int]:
    """콤마로 구분된 1-base 번호 문자열을 0-base 인덱스 리스트로 변환한다.
    "0" 단독 입력은 전체 선택을 의미한다. 잘못된 입력은 InputValidationError."""
    raw = raw.strip()
    if not raw:
        raise InputValidationError("입력이 없습니다.")

    tokens = [t.strip() for t in raw.split(",")]

    if "0" in tokens:
        if len(tokens) > 1:
            raise InputValidationError("0(전체)은 다른 번호와 함께 입력할 수 없습니다.")
        return list(range(db_count))

    try:
        indices = [int(t) for t in tokens]
    except ValueError as e:
        raise InputValidationError(f"숫자가 아닌 입력이 있습니다: {raw}") from e

    seen: set[int] = set()
    for idx in indices:
        if not (1 <= idx <= db_count):
            raise InputValidationError(f"범위를 벗어난 번호입니다: {idx}")
        if idx in seen:
            raise InputValidationError(f"중복된 번호입니다: {idx}")
        seen.add(idx)

    return [i - 1 for i in indices]
