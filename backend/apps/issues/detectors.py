"""Performance detectors.

The difference between a tracing tool and an observability tool. A waterfall shows a human 25
identical queries and waits for them to notice; a detector notices, and files it as an issue
with the same triage workflow as an error — assign it, resolve it, have it reopen when it comes
back.

Every detector here answers three questions, and a detector that cannot answer all three is not
worth firing:

  what is wrong          the offending span
  how bad is it          time that would be saved, not just a count
  where do I look        the transaction it happened in

Thresholds are deliberately conservative. A detector that fires on a healthy application
teaches people to ignore it, and an ignored detector is worse than none — it costs the same
attention and returns nothing.
"""

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.tracing.models import Span, Transaction

logger = logging.getLogger(__name__)

# A handful of repeats is a loop somebody wrote on purpose. Ten identical statements in one
# request is a query inside a loop over a result set.
N_PLUS_ONE_MIN_REPEATS = 10

# And it must actually cost something. Ten repeats of a 0.1ms cached read is not a problem
# worth a human's morning.
N_PLUS_ONE_MIN_TOTAL_MS = 50.0

SLOW_QUERY_MS = 1000.0


@dataclass(frozen=True)
class Finding:
    issue_type: str
    title: str
    culprit: str
    fingerprint: str
    evidence: dict[str, Any]


def detect(transaction: Transaction, spans: list[Span]) -> list[Finding]:
    """Run every detector over one transaction's spans."""
    if not spans:
        return []

    findings = _detect_n_plus_one(transaction, spans)
    findings.extend(_detect_slow_queries(transaction, spans))
    return findings


def _detect_n_plus_one(transaction: Transaction, spans: list[Span]) -> list[Finding]:
    """The same statement, many times, inside one request.

    Grouped by description because the statement is already parameterised — two executions with
    different bind values are the same query, which is exactly the pattern being looked for.
    """
    by_statement: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        if span.op == "db.query" and span.description:
            by_statement[span.description].append(span)

    findings = []
    for statement, group in by_statement.items():
        total = sum(span.duration_ms for span in group)
        if len(group) < N_PLUS_ONE_MIN_REPEATS or total < N_PLUS_ONE_MIN_TOTAL_MS:
            continue

        # One of these queries is legitimate; the other N-1 are the bug. Reporting the whole
        # total would overstate the saving and lose trust the first time somebody checks.
        wasted = total - (total / len(group))

        findings.append(
            Finding(
                issue_type="n_plus_one_queries",
                title=f"N+1 Queries: {_shorten(statement)}",
                culprit=transaction.name,
                fingerprint=_fingerprint("n_plus_one_queries", transaction.name, statement),
                evidence={
                    "description": statement,
                    "op": "db.query",
                    "repeat_count": len(group),
                    "total_ms": round(total, 1),
                    "wasted_ms": round(wasted, 1),
                    "transaction": transaction.name,
                    "trace_id": transaction.trace_id,
                },
            )
        )
    return findings


def _detect_slow_queries(transaction: Transaction, spans: list[Span]) -> list[Finding]:
    """One query, slow enough that the request cannot be fast whatever else improves."""
    findings = []
    for span in spans:
        if span.op != "db.query" or span.duration_ms < SLOW_QUERY_MS or not span.description:
            continue

        findings.append(
            Finding(
                issue_type="slow_db_query",
                title=f"Slow DB Query: {_shorten(span.description)}",
                culprit=transaction.name,
                fingerprint=_fingerprint("slow_db_query", transaction.name, span.description),
                evidence={
                    "description": span.description,
                    "op": "db.query",
                    "repeat_count": 1,
                    "total_ms": round(span.duration_ms, 1),
                    "wasted_ms": round(span.duration_ms, 1),
                    "transaction": transaction.name,
                    "trace_id": transaction.trace_id,
                },
            )
        )
    return findings


def _fingerprint(issue_type: str, transaction_name: str, description: str) -> str:
    """The same slow query on two endpoints is two problems: different callers, different fix.

    The statement is already parameterised, so bind values cannot split one issue into
    thousands.
    """
    parts = [issue_type, transaction_name, description]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _shorten(statement: str, limit: int = 120) -> str:
    collapsed = " ".join(statement.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


def enabled() -> bool:
    return bool(getattr(settings, "OBSLY_PERFORMANCE_DETECTORS", True))
