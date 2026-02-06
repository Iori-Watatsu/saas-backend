from contextlib import contextmanager
from django.db import connections
from .database_connections import DatabaseConnectionManager


# Premium tenant's databases context manager
@contextmanager
def premium_tenant_context(tenant):
    if tenant.tenant_type != 'premium':
        # Use default context for schema tenants
        from django_tenants.utils import tenant_context
        with tenant_context(tenant):
            yield
        return

    # Get premium tenant connections
    db_name = f"tenant_{tenant.subdomain}"

    if db_name not in connections.databases:
        DatabaseConnectionManager.add_tenant_database(tenant)

    # Swith to tenant database
    from django.db import connection

    old_db = connection.settings_dict['NAME']

    try:
        connection.settings_dict['NAME'] = db_name
        connection.close()
        connection.set_tenant(tenant)

        yield

    finally:
        connection.set_tenant(None)
        connection.settings_dict['NAME'] = old_db
        connection.close()