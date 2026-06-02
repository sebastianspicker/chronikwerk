from __future__ import annotations

from zammad_pdf_archiver.domain.package_version import get_package_version


def _read_version() -> str:
    return get_package_version("zammad-pdf-archiver", fallback="0.0.0")


__version__ = _read_version()
VERSION = __version__
