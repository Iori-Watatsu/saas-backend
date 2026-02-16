import hashlib
import logging
import secrets
from datetime import timedelta
from uuid import uuid4
from django.db import models
from django.db.models import CASCADE
from django.utils import timezone

logger = logging.getLogger(__name__)

API_VERSION_SCOPES = {
    'v1': {
        'users': ['read', 'write'],
        'projects': ['read'],
        'settings': ['read'],
    },
    'v2': {
        'users': ['read', 'write', 'delete'],
        'projects': ['read', 'write', 'delete'],
        'settings': ['read', 'write'],
        'webhooks': ['read', 'write'],
        'analytics': ['read'],
    },
    'v3': {
        'users': ['read', 'write', 'delete'],
        'projects': ['read', 'write', 'delete'],
        'settings': ['read', 'write'],
        'webhooks': ['read', 'write', 'manage'],
        'analytics': ['read', 'write'],
        'audit_logs': ['read'],
    },
}

# Map deprecated versions to their migration path
DEPRECATED_VERSIONS = {
    'v1': {
        'sunset_date': (timezone.now() + timedelta(days=100)).isoformat(),
        'migrate_to': 'v2',
        'breaking_changes': [
            'projects:write removed (use v2)',
            'analytics endpoints moved to separate endpoint'
        ]
    }
}

class APIKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    tenant = models.ForeignKey('tenant.Tenant', on_delete=models.CASCADE)
    user = models.ForeignKey('users.CustomUser', on_delete=CASCADE)
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=8, unique=True)
    key_hash = models.CharField(max_length=128)

    # API Version support
    api_versions = models.JSONField(default=list)

    # Permissions
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)

    # scopes
    scopes = models.JSONField(default=list) # ['users:read', 'projects:write']

    # Status
    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'api_keys'
        indexes = [
            models.Index(fields=['key_prefix']),
            models.Index(fields=['tenant', 'user']),
            models.Index(fields=['tenant', 'is_active'])
        ]

    def __str__(self):
        return f'{self.name} ({self.key_prefix}...)'

    def is_valid(self):
        if not self.is_active:
            return False

        if self.expires_at and timezone.now() > self.expires_at:
            return False

        return True

    def has_scope(self, scope, api_version=None):
        if scope not in self.scopes:
            return False

        # validate the scope exists in that version
        if api_version:
            return self._is_scope_valid_for_version(scope, api_version)

        return True

    # Check if scope exists in the specified API version"
    def _is_scope_valid_for_version(self, scope, api_version):
        resource, action = scope.split(':')

        version_resources = API_VERSION_SCOPES.get(api_version, {})
        if resource not in version_resources:
            return False

        valid_actions = version_resources[resource]

        # CHeack for exact action or wildcar
        return action in valid_actions or action == '*'

    def get_effective_scopes(self, api_version):
        version_resources = API_VERSION_SCOPES.get(api_version, {})
        effective_scopes = []

        for scope in self.scopes:
            if self._is_scope_valid_for_version(scope, api_version):
                effective_scopes.append(scope)

        return effective_scopes

# Authenticates requests using API keys in format: sk_{prefix}_{secret}
class APIKeyAuthenticationBackend:
    def authenticate_api_key(self, request, api_key):
        if not api_key or not api_key.startswith('sk_'):
            logger.debug('Invalid API key format')
            return None

        try:
            parts = api_key.split('_')
            if len(parts) != 3:
                logger.debug('Invalid API key structure')
                return None

            prefix = parts[1]
            secret = parts[2]

            # get api by prefix
            api_key_obj = APIKey.objects.select_related('tenant', 'user').get(key_prefix=prefix, is_active=True)

            # Check expiration
            if not api_key_obj.is_valid():
                logger.warning(f'API key {prefix} is expired or inactive')
                return None

            # Verify secret
            if not self._verify_secret(secret, api_key_obj.key_hash):
                logger.warning(f'Invalid secret for API key {prefix}')
                return None

            # API version request extraction
            api_version = self._extract_version(request.path)

            # Check version support
            if not self._extract_version_support(api_key_obj, api_version):
                logger.warning(f'API key {prefix} does not support API version {api_version}')
                return None

            # Update last used timestamp
            api_key_obj.last_used = timezone.now()
            api_key_obj.save(update_fields=['last_used'])

            # Sets tenant on request
            request.tenant = api_key_obj.tenant
            request.api_key = api_key_obj # Store API key for permission checks
            request.api_version = api_version

            # Check required API key permissions
            if not self._check_permissions(request, api_key_obj):
                logger.warning(f'API key {prefix} lacks permissions for {request.method} {request.path}')
                return None

            logger.info(f'API key authentication successful for user {api_key_obj.user.email} on {api_version}')
            return api_key_obj.user

        except APIKey.DoesNotExist:
            logger.debug(f'API key with prefix {prefix} not found')
            return None

        except Exception as e:
            logger.error(f'API key authentication error: {str(e)}')
            return None

    def _extract_version(self, path):
        path_parts = path.strip('/').split('/')

        if len(path_parts) >= 2 and path_parts[0] == 'api':
            version_part = path_parts[1]
            # Check how version the looks (v1, v2, etc.)
            if version_part.startswith('v') and version_part[1:].isdigit():
                return version_part

        # Default version if not specified
        return  'v1'

    def _check_version_support(self, api_key, api_version):
        if not api_key.api_versions:
            return True

        is_supported = api_version in api_key.api_versions

        # Check if version is deprecated
        if api_version in DEPRECATED_VERSIONS:
            deprecation = DEPRECATED_VERSIONS[api_version]
            logger.warning(
                f'API key {api_key.key_prefix} using deprecated version {api_version}. '
                f"Sunset: {deprecation['sunset_date']}, migrate to {deprecation['migrate_to']}"
            )

        return is_supported

    def _verify_secret(self, secret, key_hash):
        hashed_secret = hashlib.sha3_512(secret.encode()).hexdigest()

        # user constant-time comparison to prevent timing attacks
        import hmac
        return hmac.compare_digest(hashed_secret, key_hash)

    def _check_permissions(self, request, api_key, api_version):
        method = request.method

        # MAp HTTP methods to permissions
        method_permissions = {
            'GET': 'can_read',
            'HEAD': 'can_read',
            'OPTIONS': 'can_read',
            'POST': 'can_write',
            'PUT': 'can_write',
            'PATCH': 'can_write',
            'DELETE': 'can_delete',
        }

        # Check permission
        required_permission = method_permissions.get(method)
        if required_permission and not getattr(api_key, required_permission, False):
            return False

        # Check defined scopes
        if api_key.scopes:
            resource = self._extract_resource(request.path)

            if resource:
                action = 'read' if method in ['GET', 'HEAD', 'OPTIONS'] else 'write'

                if method == 'DELETE':
                    action = 'delete'

                required_scope = f'{resource}:{action}'

                # Check if API has the requied scope for this version
                if not api_key.has_scope(required_scope, api_version):
                    # Check wildcard scope
                    wildcard_scope = f'{resource}:*'

                    if not api_key.has_scope(wildcard_scope, api_version):
                        logger.debug(
                            f'API key missing required scope: {required_scope} '
                            f'(available: {api_key.get_effective_scopes(api_version)})'
                        )
                        return False
        return True

    def _extract_resource(self, path):
        path_parts = path.strip('/').split('/')

        if len(path_parts) >= 3 and path_parts[0] == 'api' and path_parts[1].startswith('v'):
            return path_parts[2]

        return None

    @staticmethod
    def create_api_key(tenant, user, name, scopes=None, api_versions=None,
                       expires_in_days=None, can_read=True, can_write=False, can_delete=False):

        # Generate prefix and secret
        prefix = secrets.token_hex(4) # 8 chars
        secret = secrets.token_hex(32) # 64 chars
        full_key = f'sk_{prefix}_{secret}'

        # hash secret
        key_hash = hashlib.sha512(secret.encode()).hexdigest()

        # Set expiration
        expires_at = None
        if expires_in_days:
            expires_at = timezone.now() + timedelta(days=expires_in_days)

        # Default to all current versions if not specified
        if api_versions is None:
            api_versions = list(API_VERSION_SCOPES.keys())

        # Validate scopes exist
        if scopes:
            valid_scopes = APIKeyAuthenticationBackend._validate_scopes(scopes, api_versions)

            if not valid_scopes:
                logger.error(f'No valid scopes provided for versions {api_versions}')
                return None

        # Create API key
        api_key = APIKey.objects.create(
            tenant=tenant,
            user=user,
            name=name,
            key_prefix=prefix,
            key_hash=key_hash,
            scopes=scopes or [],
            api_versions=api_versions,
            expires_at=expires_at,
            can_read=can_read,
            can_write=can_write,
            can_delete=can_delete
        )

        logger.info(
            f"Created API key '{name}' for user {user.email} in tenant {tenant.subdomain} "
            f"with version {api_versions}"
        )

        return {
            'id': str(api_key.id),
            'name': api_key.name,
            'api_key': full_key,  # Only returned once!
            'prefix': prefix,
            'scopes': scopes or [],
            'api_versions': api_versions,
            'expires_at': expires_at.isoformat()  if expires_at else None,
            'can_read': can_read,
            'can_write': can_write,
            'can_delete': can_delete,
            'created_at': api_key.created_at.isoformat()
        }

    @staticmethod
    def _validate_scopes(scopes, api_versions):
        for scope in scopes:
            parts = scope.split(':')

            if len(parts) != 2:
                logger.error(f'Invalid scope format: {scope}')
                return False

            resource, action = parts
            found = False

            # Check if scope exists  in one version
            for version in api_versions:
                version_resources = API_VERSION_SCOPES.get(version, {})
                if resource in version_resources:
                    valid_actions = version_resources[resource]
                    if action in valid_actions or action == '*':
                        found = True
                        break

            if not found:
                logger.error(
                    f'Scope {scope} not found in any of the versions {api_versions}'
                )
                return False

        return True

    # Deactive and API key
    @staticmethod
    def revoke_api_key(api_key_id):
        try:
            api_key = APIKey.objects.get(id=api_key_id)
            api_key.is_active = False
            api_key.save(update_fields=['is_active'])

            logger.info(f"Revoked API key '{api_key.name}' ({api_key.key_prefix})")
            return True

        except APIKey.DoesNotExist:
            logger.warning(f'API key {api_key_id} not found')
            return False

        except Exception as e:
            logger.error(f'Error revoking API key: {str(e)}')
            return False

    @staticmethod
    def list_user_api_keys(user):
        return APIKey.objects.filter(user=user).order_by('-created_at')