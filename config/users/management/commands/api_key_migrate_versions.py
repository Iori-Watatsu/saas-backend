from django.core.management.base import BaseCommand
from users.api_auth import APIKey, API_VERSION_SCOPES
from django.utils import timezone
from datetime import timedelta

class MigrateVersionCommand(BaseCommand):
    help = 'Migrate API keys to support new version'

    def add_arguments(self, parser):
        parser.add_argument(
            '--add-version',
            type=str,
            help='Add support for a specific version (e.g., v3)'
        )
        parser.add_argument(
            '--from-version',
            type=str,
            help='Only migrate keys currently supporting this version'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes'
        )

    def handle(self, *args, **options):
        add_version = options.get('add_version')
        from_version = options.get('from_version')
        dry_run = options.get('dry_run')

        if not add_version:
            self.stdout.write(
                self.style.ERROR("Please specify --add-version")
            )
            return

        # Find keys to migrate
        query = APIKey.objects.filter(is_active=True)
        if from_version:
            query = query.filter(api_versions__contains=[from_version])

        keys = list(query)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                                  f"DRY RUN: Would add {add_version} to {len(keys)} keys"
                              ))
            for key in keys[:5]:
                self.stdout.write(f"  {key.name}: {key.api_versions} -> "
                                  f"{key.api_versions + [add_version]}")
        else:
            updated = 0
            for key in keys:
                if add_version not in key.api_versions:
                    key.api_versions.append(add_version)
                    key.save()
                    updated += 1

            self.stdout.write(self.style.SUCCESS(
                                  f"Updated {updated} keys to support {add_version}"
                              ))
