"""서버 터미널에 앱의 진행 로그를 표시하는 설정."""

import logging


def configure_logging() -> None:
    application_logger = logging.getLogger("app")
    application_logger.setLevel(logging.INFO)
    application_logger.propagate = False
    if application_logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    application_logger.addHandler(handler)
