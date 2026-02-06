from django.core.management.base import BaseCommand
from django.db import connections
from tenant.models import Tenant

class Command(BaseCommand):
    help = 'Run all tenant database migrations'

    def handle(self, *args, **options):
        # Public schema migration
        self.stdout.write('Migrating public schema...!!!')
        from django.core.management import call_command
        call_command('migrate', verbosity=0)

        # Migrate default database schema tenants
        schema_tenants = Tenant.objects.filter(tenant_type='standard')
        for tenant in schema_tenants:
            self.stdout.write(f"Migrating schema tenant: {tenant.subdomain}")
            call_command('migrate_schemas',
                         schema_name=tenant.schema_name,
                         verbosity=0
                         )

        # Migrate premium tenants database
        db_tenants = Tenant.objects.filter(tenant_type='premium')
        for tenant in db_tenants:
            self.stdout.write(f"Migrating database tenant: {tenant.subdomain}")
            call_command('migrate',
                         database=f'tenant_{tenant.subdomain}',
                         verbosity=0
                         )

        self.stdout.write(self.style.SUCCESS(
                              f'Migrated {len(schema_tenants)} schema tenants successfully '
                              f'and {len(db_tenants)} database tenants'
                          ))