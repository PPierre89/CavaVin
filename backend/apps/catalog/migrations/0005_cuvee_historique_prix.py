from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_cuvee_wineapi_detail'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuvee',
            name='historique_prix',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Historique de prix [{date, prix_min, prix_max, devise}].',
            ),
        ),
    ]
