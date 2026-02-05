from django.conf import settings
from django.db import connections
from django.db.utils import ConnectionDoesNotExist
from pygments.lexer import default
from config.settings import TEMPLATES
from tenant.models import Tenant

class DatabaseConnectionManager:
    # Create new database for premium tenant
    @staticmethod
    def create_tenant_database(tenant):
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        if tenant.tenant_type != 'premium':
            return False

        # Get default database connections
        default_db = settings.DATABASES['default']

        try: # Make connections to new PostgreSQL database
            conn = psycopg2.connect(
                dbname= 'postgress', # Connect to sys database
                user=default_db['USER'],
                password=default_db['PASSWORD'],
                host=default_db['HOST'],
                port=default_db['PORT']
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cursor = conn.cursor()

            # Create database
            db_name = f"tenant_{tenant.subdomain}"
            cursor.execute(f"CREATE DATABASE {db_name} TEMPLATE template0 ENCODING 'UTF8';")
            cursor.execute(f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {default_db['USER']};")

            # Create tenant schema
            cursor.execute(f"\\c {db_name}")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

            cursor.close()
            conn.close()

            # Add database to Django connections
            DatabaseConnectionManager.add_tenat_database(tenant)
            return True

        except Exception as e:
            print(f"Error creating database for tenant {tenant.subdomain}: {e}")
            return False

    # Add tenat database to Django connections
    @staticmethod
    def add_tenant_database(tenant):
        if tenant.tenant_type != 'premium':
            return

        db_name = f"tenant_{tenant.subdomain}"

        # Database conf
        connections.databse[db_name] = {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': db_name,
            'USER': settings.DATABASES['default']['USER'],
            'PASSWORD': settings.DATABASES['default']['PASSWORD'],
            'HOST': settings.DATABASES['default']['HOST'],
            'PORT': settings.DATABASES['default']['PORT'],
            'CONN_MAX_AGE': 600,
            'OPTIONS': {
                'options': f'-c search_path={tenant.schema_name},public'
            } if tenant.schema_name else {}
        }

    # Get tenant databse connection
    @staticmethod
    def get_tenant_connnection(tenant):
        if tenant.tenant_type == 'standard':
            return connections['default']
        else:
            db_name = f"tenant_{tenant.subdomain}"
            if db_name not in connections.databases:
                DatabaseConnectionManager.add_tenant_database(tenant)
            return connections[db_name]

    # Migrate premium tenant databae
    @staticmethod
    def run_migrations_for_tenant(tenant):
        from django.core.management import call_command
        import os

        if tenant.tenant_type != 'premium':
            return

        # Set tenant database env variable
        os.environ['TENANT_DATABASE'] = f"tenant_{tenant.subdomain}"

        # Run migrations
        call_command(
            'migrate',
            database=f"tenant_{tenant.subdomain}",
            verbosity=0,
        )

        # Create superuser if necessary
        call_command(
            'create_tenant_superuser',
            tenant_id=str(tenant.id),
            database=f"tenant_{tenant.subdomain}"
        )