import re
import unicodedata


def parse_duration(text: str) -> int | None:
    """일본어 소요시간을 분으로 변환한다. 인식할 수 없으면 None."""
    text = unicodedata.normalize("NFKC", text).split("(")[0]
    hours = re.search(r"(\d+)\s*時間", text)
    minutes = re.search(r"(\d+)\s*分", text)
    if not hours and not minutes:
        return None
    return (int(hours.group(1)) * 60 if hours else 0) + (
        int(minutes.group(1)) if minutes else 0
    )


def parse_fee(text: str) -> int | None:
    """일본어 운임을 엔 단위 정수로 변환한다. 인식할 수 없으면 None."""
    text = unicodedata.normalize("NFKC", text)
    match = re.search(r"([\d,]+)\s*円", text)
    return int(match.group(1).replace(",", "")) if match else None
