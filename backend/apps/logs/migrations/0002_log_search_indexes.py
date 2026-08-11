from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Make substring search on log bodies index-backed.

    `body__icontains` is a sequential scan over every row in the window. On a table that grows
    by one row per log line that is the query that stops working first, and it stops working
    exactly when somebody is searching it during an incident.

    A trigram GIN index turns `%foo%` into an index lookup. It costs write throughput and disk,
    which is the right trade for a table that is written once and read under pressure.
    """

    dependencies = [("logs", "0001_initial")]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name="logrecord",
            index=GinIndex(
                fields=["body"],
                name="log_body_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        migrations.AddIndex(
            model_name="logrecord",
            index=GinIndex(
                fields=["logger"],
                name="log_logger_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
    ]
