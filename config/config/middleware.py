from django.utils.deprecation import MiddlewareMixin
from django_tenants.utils import get_tenant_model
from .database_connections import DatabaseConnectionManager

# Tenant type based database connections for Middleware setup
class TenantDatabaseMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # Get tenant from domain or subdomain
        tenant = self.get_tenant_from_request(request)

        if tenant:
            request.tenant = tenant

            # Premium tenant database connection
            if tenant.tenant_type == 'premium':
                DatabaseConnectionManager.add_tenant_database(tenant)
            from django_tenants.utils import set_tenant
            set_tenant(tenant)

        return None

    # Extract tenant from request hostname
    @staticmethod
    def get_tenant_from_request(request):
        global tenantModel
        hostname = request.get_host().split(':')[0]
        domain_parts = hostname.split('.')

        # Localshost development
        if len(domain_parts) <= 2:
            # Use default tenant
            return None

        # Extract subdomain
        subdomain = domain_parts[0]

        try:
            tenantModel = get_tenant_model()
            tenant = tenantModel.objects.get(
                subdomain=subdomain,
                status='active',
            )
            return tenant
        except tenantModel.DoesNotExist:
            #Check custom domain
            tenant = tenantModel.objects.filter(
                custom_domain=hostname,
                status='active',
            ).first
            return tenant