from django.core.management.base import BaseCommand
from django_tenants.utils import tenant_context
from prompt_toolkit import choice

from tenant.models import Tenant, Domain
from config.database_connections import DatabaseConnectionManager

class Command(BaseCommand):
    help = 'Create a new premium tenant with seperate database'

    def add_arguments(self, parser):
        parser.add_argument('subdomain', type=str, help='Tenant subdomain')
        parser.add_argument('name', type=str, help='Tenant name')
        parser.add_argument('plan', type=str, choices=['professional', 'enterprise'], help='Premium plan (professional or enterprise)')

    # Create tenant record in public schema
    def handle(self, auto_create_schema=None, is_superuser=None, *args, **options):
        tenant = Tenant(
            subdomain=options['subdomain'],
            name=options['name'],
            plan=options['plan'],
            tenant_type='premium',
            schema_name=options['subdomain'], # For consistency
            auto_create_schema=False # Avoids creating schema in default DB
        )
        tenant.save()

        self.stdout.write(self.style.SUCCESS(
                              f'Tenant record created:{tenant.name}'
                          ))

        # Premium tenant database creation
        success = DatabaseConnectionManager.create_tenant_database(tenant)

        if not success:
            self.stdout.write(self.style.ERROR(
                                  f'Tenant databse creation failed {tenant.subdomain}'
                              ))
            return

        self.stdout.write(self.style.SUCCESS(
                              f'Created database: {tenant.database_name}'
                          ))

        # Run tenant database mirgrations
        DatabaseConnectionManager.run_migrations_for_tenant(tenant)

        self.stdout.write(self.style.SUCCESS(
                              f'Ran tenant migrations {tenant.subdomain}'
                          ))

        # Domain record creation
        domain = Domain(
            domain=f"{options['subdomain']}.example.com",
            tenant=tenant,
            is_primary=True
        )
        domain.save()

        # Tenant database damin user creation
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Use tenant context with specific databse
        with tenant_context(tenant):
            # Use tenant databse connection
            user = User.objects.create_user(
                email=f"admin@{options['subdomain']}.com",
                password='changePasswd',
                first_name='Admin',
                last_name='User',
                is_staff=True,
                is_superuser=True
            )

        self.stdout.write(self.style.SUCCESS(
                              f'Admin user created: {user.email}'
                          ))

        self.stdout.write(self.style.SUCCESS(
                              f'Premium tenant created successfully "{options["name"]}" '
                              f'with separate databsse'
                          ))