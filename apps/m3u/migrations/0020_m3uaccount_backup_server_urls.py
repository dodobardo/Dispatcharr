from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('m3u', '0019_m3uaccountprofile_exp_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='m3uaccount',
            name='backup_server_urls',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Ordered list of backup server URLs for the same provider. Used when "
                    "the primary URL is unreachable during refresh, and tried per stream "
                    "before switching to another stream during playback."
                ),
            ),
        ),
    ]
