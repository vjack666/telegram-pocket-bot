import logging


class _CleanConsoleStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            from src.core.console_hub import clear_countdown_line_if_active

            clear_countdown_line_if_active()
        except Exception:
            pass
        super().emit(record)


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[_CleanConsoleStreamHandler()],
        force=True,
    )
    # Keep app logs visible but silence noisy Telethon connection chatter.
    logging.getLogger("telethon").setLevel(logging.WARNING)
