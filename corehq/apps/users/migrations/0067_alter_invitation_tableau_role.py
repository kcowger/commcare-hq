# Generated by Django 4.2.11 on 2024-05-23 21:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0066_add_profile_permissions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invitation',
            name='tableau_role',
            field=models.CharField(choices=[('Explorer', 'Explorer'), ('Explorer - Can Publish', 'ExplorerCanPublish'), ('Server Administrator', 'ServerAdministrator'), ('Site Administrator - Explorer', 'SiteAdministratorExplorer'), ('Site Administrator - Creator', 'SiteAdministratorCreator'), ('Unlicensed', 'Unlicensed'), ('Read Only', 'ReadOnly'), ('Viewer', 'Viewer')], max_length=32, null=True),
        ),
    ]
