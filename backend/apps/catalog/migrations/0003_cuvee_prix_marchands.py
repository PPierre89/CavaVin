from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0002_cuvee_wineapi_enrichment'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuvee',
            name='prix_marchands',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Prix marchands wineapi [{marchand, prix, devise, url}].',
            ),
        ),
    ]
