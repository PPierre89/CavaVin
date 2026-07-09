from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0003_cuvee_prix_marchands'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuvee',
            name='wineapi_detail',
            field=models.JSONField(
                blank=True,
                help_text='Réponse brute du dernier GET /wines/{id} wineapi.io.',
                null=True,
            ),
        ),
    ]
