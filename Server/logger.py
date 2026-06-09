import os, sys, logging
from loguru import logger


def _get_log_file():
    try:
        from config import log_file as lf
        return lf
    except Exception:
        return False


class InterceptHandler(logging.Handler):
    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame.f_back and depth > 0:
            frame = frame.f_back
            depth -= 1
        logger.bind(name=record.name).opt(depth=6, exception=record.exc_info).log(level, record.getMessage())


logger.remove()

logger.level('ERROR',   color='<red><bold>')
logger.level('WARNING', color='<yellow><bold>')
logger.level('INFO',    color='<cyan><bold>')


def _logbuffer_sink(message):
    try:
        from Server.utils.log_buffer import log_buffer
        record = message.record
        level_name = record['level'].name
        text = record['message']
        if level_name == 'ERROR':
            tag = '[ERROR] '
        elif level_name == 'WARNING':
            tag = '[WARN] '
        else:
            tag = ''
        log_buffer.write(tag + text)
    except Exception:
        pass


logger.add(
    sink=_logbuffer_sink,
    format="{message}",
    filter=lambda record: record['level'].no >= 20,
    level="INFO",
)

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

if _get_log_file():
    LOG_FILE = f"{project_root}/logs/server.txt"
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

    logger.add(
        sink=LOG_FILE,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} "
            "| {level: <8} "
            "| {line}:{file} "
            "- {message}"
        ),
        rotation="100 MB",
        retention="7 days",
        compression="zip",
        serialize=False,
        encoding="utf8",
        level="DEBUG",
    )
else:
    logger.add(
        sink=sys.stderr,
        format=(
            "<white>{time:YYYY-MM-DD HH:mm:ss.SSS}</white> "
            "| <level>{level: <8}</level> "
            "| <green><b>{line}</b></green>:"
            "<green><b><u>{file}</u></b></green> "
            "- <white>{message}</white>"
        ),
        level="INFO",
    )

logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
