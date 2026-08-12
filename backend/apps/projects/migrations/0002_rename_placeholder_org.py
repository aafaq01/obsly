"""Retire the placeholder organisation name.

"Acme" was a stand-in invented while there was nothing else to call the tenant, and it stuck.
A fake company name in the corner of every page is worse than a plain one: it reads as
somebody else's account, and the first thing it makes a person ask is what it is and why it is
there — which is exactly what happened.

Only the name that was never chosen is touched. An organisation somebody renamed keeps its
name, because a migration that overwrites a real decision is worse than the placeholder.
"""

from django.db import migrations

PLACEHOLDER = "Acme"
REPLACEMENT = "My organization"


def rename(apps, schema_editor):  # type: ignore[no-untyped-def]
    Organization = apps.get_model("projects", "Organization")
    Organization.objects.filter(name=PLACEHOLDER, slug="acme").update(name=REPLACEMENT)


def restore(apps, schema_editor):  # type: ignore[no-untyped-def]
    Organization = apps.get_model("projects", "Organization")
    Organization.objects.filter(name=REPLACEMENT, slug="acme").update(name=PLACEHOLDER)


class Migration(migrations.Migration):
    dependencies = [("projects", "0001_initial")]

    operations = [migrations.RunPython(rename, restore)]
