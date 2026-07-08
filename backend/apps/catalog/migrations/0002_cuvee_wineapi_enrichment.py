from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='cuvee',
            name='region',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='pays',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='classification',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='elaborate',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='corps',
            field=models.CharField(blank=True, default='', help_text='Body wineapi (ex: Full-bodied).', max_length=50),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='acidite',
            field=models.CharField(blank=True, default='', help_text='Acidity wineapi.', max_length=50),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='degre_alcool',
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=4, null=True),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='image_url',
            field=models.URLField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='lwin_code',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='note_moyenne',
            field=models.DecimalField(blank=True, decimal_places=1, help_text='Note communautaire /5.', max_digits=3, null=True),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='nb_notes',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='prix_min',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='prix_max',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='devise',
            field=models.CharField(blank=True, default='', max_length=8),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='accords',
            field=models.JSONField(blank=True, default=list, help_text='Accords mets-vins [{nom, emoji, confiance}].'),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='scores',
            field=models.JSONField(blank=True, default=list, help_text='Avis critiques [{reviewer, score, ...}].'),
        ),
        migrations.AddField(
            model_name='cuvee',
            name='enrichi_le',
            field=models.DateTimeField(blank=True, help_text='Dernière synchro wineapi.', null=True),
        ),
    ]
