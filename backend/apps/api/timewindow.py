"""Time windows and bucket granularity.

One place, because every page has a period picker and charts that disagree about what a bucket
covers are worse than no charts.

Granularity is derived from the window rather than fixed. An hour bucketed hourly is one bar,
which says nothing; five minutes bucketed hourly is the same. The bucket is chosen so a window
always has enough points to show a shape and few enough to draw.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.db.models import DateTimeField
from django.db.models.functions import Trunc

# Ordered shortest first. The labels are what the UI shows, so they double as the API contract.
PERIODS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

DEFAULT_PERIOD = "24h"

# (window is at most, bucket kind, bucket length). Only Django's own Trunc kinds, so the
# database does the bucketing and a percentile can be computed per bucket rather than averaged
# across buckets afterwards — an average of percentiles is not a percentile of anything.
_GRANULARITY: tuple[tuple[timedelta, str, timedelta], ...] = (
    (timedelta(minutes=5), "second", timedelta(seconds=1)),
    (timedelta(hours=6), "minute", timedelta(minutes=1)),
    (timedelta(days=7), "hour", timedelta(hours=1)),
    (timedelta(days=3650), "day", timedelta(days=1)),
)


@dataclass(frozen=True)
class Window:
    period: str
    since: datetime
    until: datetime
    minutes: float
    bucket: str
    bucket_seconds: int
    buckets: list[datetime]

    def truncate(self, field: str = "timestamp") -> Trunc:
        return Trunc(field, kind=self.bucket, output_field=DateTimeField())

    def zero_filled(self, counts: dict[datetime, int]) -> list[int]:
        return [counts.get(bucket, 0) for bucket in self.buckets]


def resolve(period: str | None) -> Window:
    """Unknown periods fall back to the default rather than erroring.

    A typo in a query string should show the usual window, not a 400 the user cannot act on.
    """
    key = period if period in PERIODS else DEFAULT_PERIOD
    span = PERIODS[key]

    bucket_kind, bucket_length = _granularity(span)
    until = datetime.now(tz=UTC)
    start = _floor(until - span, bucket_length)

    count = int((until - start) / bucket_length) + 1
    buckets = [start + bucket_length * offset for offset in range(count)]

    return Window(
        period=key,
        since=start,
        until=until,
        minutes=span.total_seconds() / 60,
        bucket=bucket_kind,
        bucket_seconds=int(bucket_length.total_seconds()),
        buckets=buckets,
    )


def _granularity(span: timedelta) -> tuple[str, timedelta]:
    for limit, kind, length in _GRANULARITY:
        if span <= limit:
            return kind, length
    _, kind, length = _GRANULARITY[-1]
    return kind, length


def _floor(moment: datetime, length: timedelta) -> datetime:
    """Align the first bucket to the grid the database will truncate to.

    Without this the first bucket is a partial one that the query never fills, and every chart
    opens with a phantom gap.
    """
    if length >= timedelta(days=1):
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if length >= timedelta(hours=1):
        return moment.replace(minute=0, second=0, microsecond=0)
    if length >= timedelta(minutes=1):
        return moment.replace(second=0, microsecond=0)
    return moment.replace(microsecond=0)
