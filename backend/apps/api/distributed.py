"""One request, however many services served it.

A trace has always been able to span services — the id propagates, and every SDK continues it.
What did not exist was the reading: `TraceDetailView` returned one `Transaction` and its spans,
so the browser's page load and the four backend transactions it caused were five separate rows
in a list, and the waterfall the product is built on stopped at the first hop.

This assembles them. Every transaction sharing a `trace_id`, from every project allowed to join,
arranged by `parent_span_id` into the tree the request actually was.

Two rules decide what is allowed in:

1. **Same organization.** A trace id is a random 128-bit number; two organizations sharing one
   is not a thing that happens, but a query that would return another tenant's row if it did is
   a query written wrong.
2. **Both ends opted in.** `trace_sharing` is off by default. A project that has not turned it
   on is never pulled into another project's waterfall, and never contributes rows to one.
"""

from typing import Any

from apps.projects.models import Project
from apps.tracing.models import Transaction


def joinable_project_ids(project: Project) -> list[int]:
    """Which projects this one may show alongside itself.

    Always includes itself: a trace opened in a project shows that project's rows whether or not
    anybody enabled anything. Sharing widens the view; it is not a prerequisite for having one.
    """
    if not project.trace_sharing:
        return [project.pk]

    shared = Project.objects.filter(
        organization_id=project.organization_id, trace_sharing=True
    ).values_list("pk", flat=True)
    return sorted({project.pk, *shared})


def transactions_in_trace(root: Transaction) -> list[Transaction]:
    """Every transaction in this trace the viewer is allowed to see, root first.

    Ordered by start time rather than by tree position: a caller starts before the service it
    calls, so chronological order already reads top-down, and it stays readable when a parent
    span id is missing because one hop was not instrumented.
    """
    rows = list(
        Transaction.objects.filter(
            trace_id=root.trace_id, project_id__in=joinable_project_ids(root.project)
        )
        .select_related("project")
        .prefetch_related("spans")
        .order_by("start_timestamp")[:50]
    )

    # The root is always present, even if a clock skew put it outside the window or the limit
    # above cut it: a trace detail page that does not contain the trace you opened is a bug
    # that reads as missing data.
    if all(row.pk != root.pk for row in rows):
        rows.insert(0, root)
    return rows


def build_tree(transactions: list[Transaction]) -> list[dict[str, Any]]:
    """Parent each transaction by the span that called it.

    An outbound request leaves an `http.client` span in the caller and sends its span id
    downstream, which the receiving SDK stores as the transaction's `parent_span_id`. So the
    edge already exists in the data; this only reads it.

    Anything whose parent is not present is returned at the top level rather than dropped —
    a partially instrumented estate is the normal state of one, and hiding the hops that do
    work would make the tool useless exactly when it is most needed.
    """
    # Every span id in the trace, mapped to the transaction it belongs to.
    #
    # Both kinds go in, and the second is the one that matters: a downstream service's
    # parent_span_id names the **client span** that called it, not the caller's transaction.
    # Indexing only transactions left every hop at the top level — which the tests did not
    # catch, because they built the parent link the way the tree wanted rather than the way the
    # SDK sends it. Found by running two real services.
    owner_of: dict[str, dict[str, Any]] = {}
    nodes: list[dict[str, Any]] = []

    for transaction in transactions:
        spans = sorted(transaction.spans.all(), key=lambda span: span.start_timestamp)
        node = {
            "id": str(transaction.pk),
            "project_id": transaction.project_id,
            "project_name": transaction.project.name,
            "name": transaction.name,
            "op": transaction.op,
            "status": transaction.status,
            "start_timestamp": transaction.start_timestamp,
            "timestamp": transaction.timestamp,
            "duration_ms": transaction.duration_ms,
            "span_id": transaction.span_id,
            "parent_span_id": transaction.parent_span_id,
            "environment": transaction.environment,
            "release": transaction.release,
            # len() over the prefetched rows, not .count(): a query per transaction here is the
            # N+1 this product exists to point at.
            "span_count": len(spans),
            # The work inside this hop. Carried per transaction rather than only for the one
            # that was opened, because "which service was slow" is answered by the row and
            # "why" is answered by what is under it — and the second question is the one that
            # sends somebody to another tool when the tool cannot answer it.
            "spans": spans,
            "depth": 0,
        }
        nodes.append(node)

        # Last one wins on a collision, which needs 64 bits to go wrong.
        owner_of[transaction.span_id] = node
        for span in spans:
            owner_of[span.span_id] = node

    _assign_depths(nodes, owner_of)
    return nodes


def _assign_depths(nodes: list[dict[str, Any]], owner_of: dict[str, dict[str, Any]]) -> None:
    """How far each transaction sits from the start of the trace, for indenting.

    Walked with a visited set rather than recursively: the parent ids come off the wire, and a
    malformed pair that points at each other must indent oddly, not hang the request.
    """
    for node in nodes:
        depth = 0
        seen = {node["span_id"]}
        current = node
        while True:
            parent = owner_of.get(current["parent_span_id"] or "")
            # `is not current` as well as the seen set: a transaction whose parent span is one
            # of its own spans would otherwise count itself as its own ancestor.
            if parent is None or parent is current or parent["span_id"] in seen:
                break
            seen.add(parent["span_id"])
            current = parent
            depth += 1
            if depth > 20:  # deeper than any real call chain; stop rather than trust the input
                break
        node["depth"] = depth


def services_in(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which projects took part, and how much of the time each one held.

    The first question about a slow distributed request is *which service*, and counting it
    here means the page can answer before anybody expands anything.
    """
    totals: dict[int, dict[str, Any]] = {}
    for node in nodes:
        entry = totals.setdefault(
            node["project_id"],
            {
                "project_id": node["project_id"],
                "project_name": node["project_name"],
                "transactions": 0,
                "duration_ms": 0.0,
            },
        )
        entry["transactions"] += 1
        entry["duration_ms"] += node["duration_ms"]

    return sorted(totals.values(), key=lambda row: -row["duration_ms"])
