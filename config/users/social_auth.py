from django.contrib.auth import get_user_model
from django.db import transaction, connections
from django.utils import timezone
from django_tenants.utils import tenant_context
from psycopg2 import sql
import requests
import logging

from .authentication import TenantAuthenticationBackend

logger = logging.getLogger(__name__)

# OAuth2 with tenant isolation social auth (Google, github, etc)
class SocialAuthenticationBackend(TenantAuthenticationBackend):
    def authenticate_social(self, request, provider, access_token, tenant=None):
        UserModel = get_user_model()

        tenant = tenant or getattr(request, 'tenant', None)
        if not tenant:
            logger.warning('No tenant found for social authentication')
            return None

        if tenant.status != 'active':
            logger.warning(f'Tenant {tenant.subdomain} is not active')
            return None

        user_info = self._get_user_info_from_provider(provider, access_token)
        if not user_info:
            logger.warning(f'Failed to get user info from {provider}')
            return None

        email = user_info.get('email')
        if not email:
            logger.warning(f'No email returned from {provider}')
            return None

        try:
            if tenant.tenant_type == 'standard':
                with tenant_context(tenant):
                    return self._get_or_create_social_user(UserModel, email, user_info, tenant, provider)

            else:
                return self._authenticate_premium_social(UserModel, email, user_info, tenant, provider)

        except Exception as e:
            logger.exception(f'Social authentication error for tenant{tenant.subdomain}: {str(e)}')
            return None

    def _authenticate_premium_social(self, UserModel, email, user_info, tenant, provider):
        db_alias = tenant.database_name

        try:
            # Prevent sql injection
            with connections[db_alias].cursor() as cursor:
                cursor.execute(
                    sql.SQL("SET search_path TO {}").format(
                        sql.Identifier(tenant.schema_name)
                    )
                )

            with transaction.atomic(using=db_alias):
                try:
                    user = UserModel.objects.using(db_alias).get(email=email, tenant=tenant)
                    user = self._update_social_user(user, user_info, provider, db_alias)

                except UserModel.DoesNotExist:
                    user = self._create_social_user(UserModel, email, user_info, tenant, provider, db_alias)

                return user

        except Exception as e:
            logger.error(f'Premium tenant social auth error: {str(e)}')
            return None

    def _get_user_info_from_provider(self, provider, access_token):
        endpoints = {
            'google': 'https://www.googleapis.com/oauth2/v3/userinfo',
            'facebook': 'https://graph.facebook.com/v12.0/me?fields=email,name',
            'microsoft': 'https://graph.microsoft.com/v1.0/me',
        }

        if provider not in endpoints:
            logger.warning(f'Unsupported social provider: {provider}')
            return None

        headers = {'Authorization': f'Bearer {access_token}'}

        try:
            response = requests.get(endpoints[provider], headers=headers, timeout=10)

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(
                    f'Failed to fetch user info from {provider}: '
                    f'Status {response.status_code}'
                )

        except requests.RequestException as e:
            logger.error(f'Error fetching user info from {provider}: {str(e)}')

        return None

    def _get_or_create_social_user(self, UserModel, email, user_info, tenant, provider):
        try:
            user = UserModel.objects.get(email=email, tenant=tenant)

            if not hasattr(user, 'social_auth') or user.social_auth is None:
                user.social_auth = {}

            user.social_auth[provider] = {
                'provider_id': user_info.get('sub') or user_info.get('id'),
                'email': email,
                'last_login': timezone.now().isoformat()
            }

            user.last_login = timezone.now()
            user.save(update_fields=['social_auth', 'last_login'])

            logger.info(f'Social login successful for {email}')
            return user

        except UserModel.DoesNotExist:
            return self._create_new_social_user(UserModel, email, user_info, tenant, provider)

    def _create_new_social_user(self, UserModel, email, user_info, tenant, provider, db_alias=None):
        full_name = user_info.get('name', '')
        name_parts = full_name.split() if full_name else []

        first_name = (
            user_info.get('given_name') or
            (name_parts[0] if name_parts else '')
        )
        last_name = (
            user_info.get('family_name') or
            (' '.join(name_parts[1:]) if len(name_parts) > 1 else '')
        )

        user = UserModel(
            email=email,
            first_name=first_name[:100],
            last_name=last_name[:100],
            tenant=tenant,
            role='member',
            status='active',
            email_verified=True,
            social_auth={
                provider: {
                    'provider_id': user_info.get('sub') or user_info.get('id'),
                    'email': email,
                    'created_at': timezone.now().isoformat()
                }
            },
            last_login=timezone.now()
        )

        if db_alias:
            user.save(using=db_alias)
        else:
            user.save()

        logger.info(f'Created new social user {email} via {provider}')
        return user

    def _update_social_user(self, user, user_info, provider, db_alias=None):
        if not hasattr(user, 'social_auth') or user.social_auth is None:
            user.social_auth = {}

        user.social_auth[provider] = {
            'provider_id': user_info.get('sub') or user_info.get('id'),
            'email': user_info.get('email'),
            'last_login': timezone.now().isoformat()
        }

        user.last_login = timezone.now()

        if db_alias:
            user.save(using=db_alias, update_fields=['social_auth', 'last_login'])
        else:
            user.save(update_fields=['social_auth', 'last_login'])

        logger.info(f'Updated social auth for user {user.email}')
        return user

    def _create_social_user(self, UserModel, email, user_info, tenant, provider, db_alias=None):
        return self._create_new_social_user(
            UserModel, email, user_info, tenant, provider, db_alias
        )