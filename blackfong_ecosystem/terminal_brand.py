"""Terminal identity: no runtime initialization and no machine-output decoration."""
import os
import sys
from importlib.metadata import PackageNotFoundError, version

PRODUCT = 'O.B.E.O.S.'
TAGLINE = 'Offline-first intelligence environment'
DISTRIBUTION = 'blackfong_ecosystem'
SOURCE_VERSION = '0.1.0'
ACCENT = '121;224;205'


def product_version():
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        return SOURCE_VERSION


def banner_text():
    return f"{PRODUCT}  v{product_version()}\n{TAGLINE}\nBLACKFONG / PROJECTS"


def show_banner(force=False):
    # Both streams must be terminals for automatic decoration. Never write to stdout.
    if not force and not (sys.stdout.isatty() and sys.stderr.isatty()):
        return
    text = banner_text()
    if sys.stderr.isatty() and 'NO_COLOR' not in os.environ and os.environ.get('TERM') != 'dumb':
        text = f"\033[38;2;{ACCENT}m{text}\033[0m"
    print(text, file=sys.stderr)
