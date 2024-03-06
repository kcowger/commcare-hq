# Generated by Django 3.2.23 on 2024-02-16 21:11

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cleanup', '0015_deletedcouchdoc_unique_id_and_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeletedSQLDoc',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('doc_id', models.CharField(max_length=126)),
                ('object_class_path', models.CharField(max_length=255)),
                ('domain', models.CharField(max_length=255)),
                ('deleted_on', models.DateTimeField(db_index=True)),
                ('deleted_by', models.CharField(max_length=126, null=True)),
            ],
            options={
                'db_table': 'cleanup_deletedsqldoc',
            },
        ),
        migrations.AddConstraint(
            model_name='deletedsqldoc',
            constraint=models.UniqueConstraint(fields=('doc_id', 'object_class_path'),
                                               name='deletedsqldoc_unique_id_and_type'),
        ),
    ]
