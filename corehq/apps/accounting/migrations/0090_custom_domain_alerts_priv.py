# Generated by Django 3.2.23 on 2024-01-01 12:03

from django.core.management import call_command
from django.db import migrations

from corehq.apps.accounting.models import SoftwarePlanEdition
from corehq.privileges import CUSTOM_DOMAIN_ALERTS
from corehq.util.django_migrations import skip_on_fresh_install


@skip_on_fresh_install
def _grandfather_privilege(apps, schema_editor):
    call_command('cchq_prbac_bootstrap')

    skip_editions = ','.join((
        SoftwarePlanEdition.PAUSED,
        SoftwarePlanEdition.COMMUNITY,
        SoftwarePlanEdition.STANDARD,
        SoftwarePlanEdition.PRO,
    ))
    call_command(
        'cchq_prbac_grandfather_privs',
        CUSTOM_DOMAIN_ALERTS,
        skip_edition=skip_editions,
        noinput=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0089_dedupe_priv'),
    ]

    operations = [
        migrations.RunPython(
            _grandfather_privilege,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
