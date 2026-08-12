"""Backfill first_release from each issue's earliest event.

Without this, every issue that existed before the column shows a blank origin, and the
Releases page reports "0 issues introduced" for every historical version. That is the
failure mode this project keeps arguing against: not missing data, but missing data that
reads as good news.

The value is recoverable — events carry `release` and are ordered — so leaving it blank
would be a choice, not a limitation.

Performance issues are the exception and stay blank: a detector files them from a transaction,
so they have no events to read a release from. New ones take it from the transaction directly;
historical ones predate the column and are left honestly empty rather than guessed at.
"""

from django.db import migrations
from django.db.models import CharField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce


def backfill(apps, schema_editor):  # type: ignore[no-untyped-def]
    Issue = apps.get_model("issues", "Issue")
    Event = apps.get_model("events", "Event")

    earliest = (
        Event.objects.filter(issue_id=OuterRef("pk"))
        .exclude(release="")
        .order_by("timestamp")
        .values("release")[:1]
    )
    # One UPDATE with a correlated subquery, not a loop: this runs against a table that may
    # already hold millions of rows, and a migration that takes the site down to add a
    # convenience column is not a convenience.
    #
    # Coalesce, not a second pass: the subquery yields NULL for an issue with no tagged event
    # — a performance issue has no events at all — and the column is NOT NULL, so writing the
    # NULL first and tidying afterwards fails on the write.
    Issue.objects.filter(first_release="").update(
        first_release=Coalesce(Subquery(earliest), Value("", output_field=CharField()))
    )


def noop(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Reversing drops derived data only. The events it came from are untouched."""


class Migration(migrations.Migration):
    dependencies = [
        ("issues", "0003_issue_first_release"),
        ("events", "0001_initial"),
    ]

    operations = [migrations.RunPython(backfill, noop)]
