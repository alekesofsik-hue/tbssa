import logging
import warnings


def setup_logging() -> logging.Logger:
    # Default warnings off for noisy deps (optional)
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="paramiko")

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("tbssa")
    log.setLevel(logging.INFO)

    for noisy in ["telegram", "telegram.ext", "telegram.request", "httpx", "httpcore", "paramiko"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return log

