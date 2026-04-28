import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # Keep app logs visible but silence noisy Telethon connection chatter.
    logging.getLogger("telethon").setLevel(logging.WARNING)
