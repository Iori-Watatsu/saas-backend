from django.utils.deprecation import MiddlewareMixin
from django_tenants.utils import get_tenant_model
from .database_connections import DatabaseConnectionManager
import logging
from django.http import JsonResponse
from users.api_auth import APIKeyAuthenticationBackend, DEPRECATED_VERSIONS

logger = logging.getLogger(__name__)

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

# Middleware to authenticate API requests using API keys
class APIKeyAuthenticationMiddleware(MiddlewareMixin):
    EXEMPT_PATHS = [
        '/health/',
        '/api/docs/',
        '/api/schema/',
    ]

    # Process incoming request and authenticate
    def process_request(self, request):
        if any(request.path.startswith(path) for path in self.EXEMPT_PATHS):
            return None

        # Process only API routes
        if not request.path.startswith('/api/'):
            return None

        # Check API key in header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        if not auth_header.startswith('Bearer '):
            return JsonResponse(
                {'error': 'Missing or invalid Authorization header'},
                status=401
            )

        api_key = auth_header[7:] # Remove 'Bearer ' prefix

        # Authenticate API key
        backend = APIKeyAuthenticationBackend
        user = backend.authenticate_api_key(request, api_key)


        if not user:
            return JsonResponse(
                {'error': 'Invalid or expired API key'},
                status=401
            )

        # Attach user to request
        request.user = user

        # Warning header if using deprecated version
        if hasattr(request, 'api_version') and request.api_version in DEPRECATED_VERSIONS:
            deprecation = DEPRECATED_VERSIONS[request.api_version]
            request.deprecated_version_warning = (
                f"API version {request.api_version} in deprecated. "
                f"Sunset date: {deprecation['sunset_date']}. "
                f"Please migrate to {deprecation['migrate_to']}"
            )

        return None

    # Add version info to response headers
    def process_response(self, request, response):
        if hasattr(request, 'api_version'):
            response['X-API-Version'] = request.api_version

        if hasattr(request, 'deprecated_version_warning'):
            response['X-API-Deprecation-Warning'] = request.deprecated_version_warning

        if hasattr(request, 'tenant'):
            response['X-Tenant-ID'] = str(request.tenant.id)

        return response

# Ensure all data access respects tenant isolation, Validates tenant_id in requests
class TenantIsolationMiddleware(MiddlewareMixin):
    # Validate tenant isolation
    def process_view(self, request, view_func, view_args, view_kwargs):
        # Skip non-API routes
        if not request.path.startswith('/api/'):
            return None

        # Skip unauthenticated requests (already handled by auth middleware)
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return None

        # Extract tenant from request (from authenticated API key)
        if not hasattr(request, 'tenant'):
            return JsonResponse(
                {'error': 'Tenant information missing'},
                status=500
            )

        # Optionally validate tenant_id from request header matches authenticated tenant
        header_tenant_id = request.META.get('HTTP_X_TENANT_ID')
        if header_tenant_id:
            if header_tenant_id != str(request.tenant.id):
                logger.warning(
                    f'Tenant mismatch: header={header_tenant_id}, '
                    f'authenticated={request.tenant.id}, user={request.user.email}'
                )
                return JsonResponse(
                    {'error': 'Tenant mismatch'},
                    status=403
                )

        return None