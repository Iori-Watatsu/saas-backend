from django.conf import settings
from django_tenants.utils import get_tenant_model, get_public_schema_name

class HybridTenantRouter:
    # Database router for hybrid multi-tenant databases based on tenant type

    @staticmethod
    def _get_tenant():
        # Obtain current tenant
        from django.utils.deprecation import MiddlewareMixin

        request = getattr(settings, 'CURRENT_REQUEST', None)
        if request and hasattr(request, 'tenant'):
            return request.tenant

        from django_tenants.utils import get_tenant
        return get_tenant()

    def db_for_read(self, model):
        return self._route_tenant_db(model)

    def db_for_write(self, model):
        return self._route_tenant_db(model)

    def _route_tenant_db(self, model):
        # Route model to appropriate database
        tenant = self._get_tenant()

        # Default schema for shared apps(public)
        if model._meta.app_label in settings.SHARED_APPS:
            return 'default'

        # No tenant uses public schema
        if not tenant:
            return 'default'

        # Individual databases for Premium tenants
        if tenant.tenant_type == 'premium':
            return f"tenant_{tenant.subdomain}"

        # Standard tenants use default database schema
        return 'default'

    # Control database migrations
    @staticmethod
    def allow_migrate(db, app_label, model_name=None):
        # Default for shared apps
        if app_label in settings.SHARED_APPS:
            return db == 'default'

        # Default for both standard & premium tenants
        if app_label in settings.TENANT_APPS:
            if db.startwith('tenant_'):
                return True # For premium clients databses

            elif db == 'default':
                return True

        return False

    def allow_relation(self, obj1, obj2):
        # Get database for each object, allow relation within same database
        db1 = self._route_tenant_db(obj1.__class__)
        db2 = self._route_tenant_db(obj2.__class__)

        return db1 == db2