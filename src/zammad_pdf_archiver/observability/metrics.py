from __future__ import annotations

from prometheus_client import Counter, Histogram

processed_total = Counter(
    "processed_total",
    "Number of successfully processed tickets.",
)
skipped_total = Counter(
    "skipped_total",
    "Number of skipped ticket processing attempts.",
    labelnames=("reason",),
)
failed_total = Counter(
    "failed_total",
    "Number of failed ticket processing attempts.",
)

render_seconds = Histogram(
    "render_seconds",
    "Seconds spent rendering the PDF.",
)
sign_seconds = Histogram(
    "sign_seconds",
    "Seconds spent signing the PDF.",
)
total_seconds = Histogram(
    "total_seconds",
    "Seconds spent processing a ticket end-to-end.",
)
