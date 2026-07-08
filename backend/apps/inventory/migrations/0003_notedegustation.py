import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
        ('inventory', '0002_bouteille_proprietaire'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NoteDegustation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('millesime', models.PositiveIntegerField(blank=True, help_text='Millésime dégusté (facultatif).', null=True)),
                ('note', models.DecimalField(decimal_places=1, help_text='Note personnelle de 0 à 5.', max_digits=2, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(5)])),
                ('commentaire', models.TextField(blank=True)),
                ('acidite', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(5)])),
                ('tanin', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(5)])),
                ('fruit', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MaxValueValidator(5)])),
                ('date_degustation', models.DateField(default=django.utils.timezone.localdate)),
                ('cree_le', models.DateTimeField(auto_now_add=True)),
                ('cuvee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='degustations', to='catalog.cuvee')),
                ('proprietaire', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='degustations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Note de dégustation',
                'verbose_name_plural': 'Notes de dégustation',
                'ordering': ['-date_degustation', '-cree_le'],
            },
        ),
    ]
