"""Web vitals, aggregated the way the standard defines them.

Three decisions that are not arbitrary:

**p75, not the mean.** Core Web Vitals are specified at the 75th percentile — the score is
"three quarters of your users had at least this experience". A mean is a number nobody
measured, and on a distribution with a slow tail it reads as healthy while a quarter of visits
are not.

**Fixed thresholds.** good / needs-improvement / poor come from the Web Vitals definition, not
from this project's opinion. A tool that invents its own bands cannot be compared against
anything else anyone reads.

**Per page, not just per site.** A site-wide LCP hides the one route that is ruining it, which
is the only actionable form of the number.
"""

from typing import Any

from django.db.models import Count, F, FloatField, Func

from apps.api.performance import Percentile
from apps.api.timewindow import resolve
from apps.tracing.models import Transaction

# (key, label, what it measures, good below, poor above). The middle band is everything
# between — "needs improvement" is defined by the two edges, not by a third number.
VITALS: tuple[tuple[str, str, str, float, float], ...] = (
    ("lcp", "LCP", "Largest Contentful Paint — when the main content finished drawing", 2500, 4000),
    (
        "inp",
        "INP",
        "Interaction to Next Paint — how long the page takes to answer a click",
        200,
        500,
    ),
    ("cls", "CLS", "Cumulative Layout Shift — how much the page moved under the reader", 0.1, 0.25),
    ("fcp", "FCP", "First Contentful Paint — when anything at all appeared", 1800, 3000),
    ("ttfb", "TTFB", "Time to First Byte — how long the server took to start answering", 800, 1800),
)

# CLS is a unitless ratio; everything else is milliseconds. Formatting a 0.08 layout shift as
# "0.08ms" is the kind of wrong that makes a whole page untrustworthy.
UNITLESS = frozenset({"cls"})


class JsonNumber(Func):
    """Read one measurement's value out of the JSON column as a number.

    The cast is in the template rather than a wrapping Cast(): `->>` yields text, so without
    it every comparison and percentile would be lexicographic — which sorts 1000 before 90.
    """

    function = ""
    template = "((%(expressions)s -> %(name)s ->> 'value')::double precision)"
    output_field = FloatField()

    def __init__(self, field: str, name: str, **extra: Any) -> None:
        super().__init__(F(field), name=f"'{name}'", **extra)


def rating(key: str, value: float | None) -> str:
    if value is None:
        return "none"
    _, _, _, good, poor = next(vital for vital in VITALS if vital[0] == key)
    if value <= good:
        return "good"
    return "poor" if value > poor else "needs-improvement"


def summary(project_id: int, period: str) -> dict[str, Any]:
    """Site-wide p75 per vital, plus the pages carrying the worst of each."""
    win = resolve(period)
    # Browser transactions only. A backend request has no layout shift, and averaging the two
    # populations would produce a number describing neither.
    pageloads = Transaction.objects.filter(
        project_id=project_id, timestamp__gte=win.since, op="pageload"
    )

    aggregates: dict[str, Any] = {}
    for key, *_ in VITALS:
        present = pageloads.filter(**{f"measurements__{key}__isnull": False})
        aggregates[key] = present.aggregate(
            p75=Percentile(JsonNumber("measurements", key), 0.75),
            samples=Count("id"),
        )

    return {
        "period": win.period,
        "pageloads": pageloads.count(),
        "vitals": [
            {
                "key": key,
                "label": label,
                "explains": explains,
                "value": _round(key, aggregates[key]["p75"]),
                "samples": aggregates[key]["samples"],
                "rating": rating(key, aggregates[key]["p75"]),
                "good_below": good,
                "poor_above": poor,
                "unit": "" if key in UNITLESS else "millisecond",
            }
            for key, label, explains, good, poor in VITALS
        ],
        "pages": _pages(pageloads),
    }


def _pages(pageloads: Any) -> list[dict[str, Any]]:
    """The routes worth opening: slowest p75 LCP first.

    Ordered by LCP because it is the vital most often fixable by changing one page, and a list
    ordered by traffic just repeats the traffic report.
    """
    rows = (
        pageloads.filter(measurements__lcp__isnull=False)
        .values("name")
        .annotate(
            count=Count("id"),
            lcp=Percentile(JsonNumber("measurements", "lcp"), 0.75),
            cls=Percentile(JsonNumber("measurements", "cls"), 0.75),
            inp=Percentile(JsonNumber("measurements", "inp"), 0.75),
        )
        .order_by(F("lcp").desc(nulls_last=True))[:20]
    )

    return [
        {
            "name": row["name"],
            "count": row["count"],
            "lcp": _round("lcp", row["lcp"]),
            "cls": _round("cls", row["cls"]),
            "inp": _round("inp", row["inp"]),
            "rating": rating("lcp", row["lcp"]),
        }
        for row in rows
    ]


def _round(key: str, value: float | None) -> float | None:
    if value is None:
        return None
    # A layout shift of 0.083 rounded to 0 would read as perfect. Three decimals for the ratio,
    # whole milliseconds for everything else.
    return round(value, 3) if key in UNITLESS else round(value, 1)
