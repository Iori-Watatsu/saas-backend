from functools import wraps
from django.http import JsonResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import BasePermission

# Only allow JWT auth
class IsJWTAuthenticated(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Reject if authenticated via API key
        if hasattr(request, 'api_key') and request.api_key:
            return False

        return True

# Only allow API key auth
class IsAPIKeyAuthenticated(BasePermission):
    def has_permission(self, request, view):
        # Must have API key attached
        if not hasattr(request, 'api_key') or not request.api_key:
            return False

        return request.api_key.is_valid()

# Allow both JWT and API key auth
class IsBothAuthMethodsAllowed(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # ALlow JWT or API key
        if hasattr(request, 'api_key') and request.api_key:
            return request.api_key.is_valid()

        return True

# Check if API key has required scope
class RequireAPIKeyScope(BasePermission):
    def has_permission(self, request, view):
        # Applies API key auth only
        if not hasattr(request, 'api_auth') or not request.api_key:
            return True

        # Get required scope from view
        required_scope = getattr(view, 'required_scope', None)
        if not required_scope:
            return True

        # Check if key has scope
        api_version = getattr(request, 'api_version', 'v1')
        return request.api_key.has_scope(required_scope, api_version)

# Check if API key supports the requested version
class RequireAPIVersion(BasePermission):
    def has_permission(self, request, view):
        if not hasattr(request, 'api_key') or not request.api_key:
            return True

        api_version = getattr(request, 'api_version', 'v1')
        return api_version in request.api_key.api_versions or not request.api_key.api_versions

# Decorator: Require JWT authentication only. Usage: @require_jwt_auth
def require_jwt_auth(view_func):
    @wraps(view_func)
    # Check if authenticated
    def wrapped_view(request, *args, **kwargs):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)

        # check if Not using API key
        if hasattr(request, 'api_key') and request.api_key:
            return JsonResponse(
                {'error': 'This endpoint requires JWT authentication, not API key'},
                status=403
            )

        return view_func(request, *args, **kwargs)

    return wrapped_view

#Decorator: Require API key authentication only. Usage: @require_api_key_auth
def require_api_key_auth(view_func):
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        # Check if has vlaid api key
        if not hasattr(request, 'api_key') or not request.api_key:
            return JsonResponse({'error': 'Invalid or expired API key'}, status=401)

        return view_func(request, *args, **kwargs)

    return wrapped_view

# Decorator: Require specific API key scope. Usage: @require_scope('users:read')
def require_scope(scope):
    def decorator(view_func):
        @wraps(view_func)
        # Check API key auth
        def wrapped_view(request, *args, **kwargs):
            if not hasattr(request, 'api_key') or not request.api_key:
                return JsonResponse(
                    {'error': f'API key with scope {scope} required'},
                    status=401
                )

            # check scope
            api_version = getattr(request, 'api_version', 'v1')
            if not request.api_key.has_scope(scope, api_version):
                return JsonResponse(
                    {
                        'error': f'API key missing required scope: {scope}',
                        'Available_scopes': request.api_key.get_effective_scopes(api_version)
                    },
                    status=403
                )

            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator

# Decorator: Allow both JWT and API key. Usage: @allow_both_auth
def allow_both_auth(view_func):
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401

        # Valid for both JWT and API key
        return view_func(request, *args, **kwargs)

    return wrapped_view

# Decorator: Log API key usage for audit trail. Usage: @audit_api_key_usage('user_created')
def audit_api_key_usage(action):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            import logging

            # Call view
            response = view_func(request, *args, ** kwargs)

            # Log if API key used
            if hasattr(request, 'api_key') and request.api_key:
                logger = logging.getLogger('users.api_auth')
                logger.info(
                    f'API Key Action: {action} | '
                    f'Key: {request.api_key.key_prefix}... | '
                    f'User: {request.api_key.user.email} | '
                    f'Tenant: {request.api_key.tenant.subdomain} | '
                    f'Status: {response.status_code}'
                )

            return response

        return wrapped_view

    return decorator

# Mixin: Enforce JWT authentication. Usage: class MyView(JWTRequiredMixin, APIView): ...
class JWTRequiredMixin:
    permission_classes = [IsJWTAuthenticated]

    def handle_no_permission(self):
        return Response(
            {'error': 'JWT authentication required'},
            status=status.HTTP_401_UNAUTHORIZED
        )

# Mixin: Enforce API key authentication. Usage: class MyView(APIKeyRequiredMixin, APIView): ...
class APIKeyRequiredMixin:
    permission_classes = [IsAPIKeyAuthenticated]

    def handle_no_permission(self):
        return Response(
            {'error': 'API key authentication require'},
            status=status.HTTP_401_UNAUTHORIZED
        )

# Mixin: Allow both JWT and API key. Usage: class MyView(BothAuthMethodsMixin, APIView): ...
class BothAuthMethodsMixin:
    permission_classes = [IsBothAuthMethodsAllowed]

# Mixin: Validate API key scopes. Usage:
    # class MyView(ScopeValidationMixin, APIView):
      #  required_scopes = ['users:read']
class ScopeValidationMixin:
    requied_scopes = []

    def check_permissions(self, request):
        super().check_permissions(request)

        # Check scopes only for API auth
        if not hasattr(request, 'api_key') or not request.api_key:
            return

        api_version = getattr(request, 'api_version', 'v1')

        for scope in self.requied_scopes:
            if not request.api_key.has_scope(scope, api_version):
                self.permission_denied(
                    request,
                    message=f'API key missing required scope: {scope}'
                )

from rest_framework.generics import (
    ListAPIView as DRFListAPIView,
    CreateAPIView as DRFCreateAPIView,
)

# List view that requires API key
class APIKeyListView(APIKeyRequiredMixin, DRFListAPIView):
    pass

# Create view that requires API key
class APIKeyCreateView(APIKeyRequiredMixin, DRFCreateAPIView):
    pass

# Create view that requires JWT
class JWTListView(JWTRequiredMixin, DRFListAPIView):
    pass

# Create view that requires JWT
class JWTCreateView(JWTRequiredMixin, DRFCreateAPIView):
    pass