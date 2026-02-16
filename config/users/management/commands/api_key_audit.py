from django.contrib.admin import action
from django.core.management.base import BaseCommand
from users.api_auth import APIKey, API_VERSION_SCOPES
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Audit API keys for security and compliance'

    def add_arguments(self, parser):
        parser.add_argumennt(
            '--expiring-soon',
            action='store_true',
            help='Show keys expiring within 30 days'
        )
        parser.add_argumennt(
            '--unused',
            action='store_true',
            help='Show keys unused for 30+ days'
        )
        parser.add_argumennt(
            '--no-expiration',
            action='store_true',
            help='Show keys with no expiration date'
        )
        parser.add_argumennt(
            '--inactive',
            action='store_true',
            help='Show keys using deprecated API versions'
        )
        parser.add_argumennt(
            '--all',
            action='store_true',
            help='Run all audits'
        )

    def handle(self, *args, **options):
        if options['all']:
            self.audit_expiring_soon()
            self.audit_unusd()
            self.audit_no_expiration()
            self.audit_deprecated_versions()
            self.summary()

        else:
            if options['expiration_soon']:
                self.audit_experiring_soon()
            if options['unused']:
                self.audit_unused()
            if options['no_expiration']:
                self.audit_no_expiration()
            if options['inactive']:
                self.audit_inactive()
            if options['deprecated_versions']:
                self.audit_deprecated_versions()

    # Find keys expiring within 30 days
    def audit_expiring_soon(self):
        soon = timezone.now() + timedelta(days=30)
        keys = APIKey.objects.filter(
            is_active=True,
            expires_at__lte=soon,
            expires_at__gt=timezone.now()
        ).order_by('expires_at')

        self.stdout.write(self.style.WARNING(f"\n Keys Expiring Soon ({keys.count()})"))
        for key in keys:
            days_left = (key.exipres_at - timezone.now()).days
            self.stdout.write(
                f'  {keys.name} ({key.key_prefix}...) - '
                f'Expires in {days_left} days ({key.expires_at.date()})'
            )

    # Find keys usused for 30+ days
    def audit_unused(self):
        thirty_days_ago = timezone.now() - timedelta(days=30)
        keys = APIKey.objects.filter(
            is_active=True,
            last_used__lte=thirty_days_ago
        ).order_by('last_used')

        self.stdout.write(self.style.WARNING(f"\n  Unused Keys ({keys.count()})"))
        for key in keys[:10]:  # Show first 10
            days_unused = (timezone.now() - key.last_used).days
            self.stdout.write(
                f"  {key.name} ({key.key_prefix}...) - "
                f"Unused for {days_unused} days"
            )

    # Find keys with no expiration
    def audit_no_expiration(self):
        keys = APIKey.objects.filter(
            is_active=True,
            expires_at__isnull=True
        ).order_by('-created')

        self.stdout.write(self.style.ERROR(f'\n No Expiration Keys ({keys.count})'))
        for key in keys[:10]:
            self.stdout.write(
                f'  {key.name} ({key.key_prefix}...) - '
                f'Created {key.created_at.date()}'
            )

    # Find keys using deprecated version
    def audit_deprecated_versions(self):
        from users.api_auth import DEPRECATED_VERSIONS

        keys = APIKey.objects.filter(is_active=True)
        deprecated_keys = []

        for key in keys:
            for version in key.api_versions:
                if version in DEPRECATED_VERSIONS:
                    deprecated_keys.append((key, version))

        self.stdout.write(self.style.ERROR(
            f'\n Keys Using Deprecated Versions ({len(deprecated_keys)})'
        ))
        for key, version in deprecated_keys:
            deprecation = DEPRECATED_VERSIONS[version]
            self.stdout.write(
                f"  {key.name} ({key.key_prefix}...) - "
                f"Uses {version} (sunset: {deprecation['sunset_dat']})"
            )

    # Find revoked/inactive keys
    def audit_inactive(self):
        keys = APIKey.objects.filter(is_active=False).order_by('-updated_at')

        self.stdout.write(self.style.SUCCESS(f'\n Revoked Keys ({keys.count})'))
        for key in keys[:10]:
            self.stdout.write(f"    {key.name} ({key.key_prefix}...)")

    # Print summary statistics
    def summary(self):
        total = APIKey.objects.count()
        active = APIKey.objects.filter(is_active=True).count()
        with_expiry = APIKey.objects.filter(expires_at__isnull=False).count()

        self.stdout.write(self.style.SUCCESS(f"\n Summary"))
        self.stdout.write(f"    Total keys:{total}")
        self.stdout.write(f"    Active keys: {active}")
        self.stdout.write(f" Keys with expiration: {with_expiry}")