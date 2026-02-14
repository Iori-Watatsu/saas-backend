from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db import transaction, connections
from django_tenants.utils import tenant_context
from .password_router import password_router
from psycopg2 import sql
import logging

logger = logging.getLogger(__name__)

# Custom tenant aware hashing backend
class TenantAuthenticationBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        tenant = getattr(request, 'tenant', None)

        if not tenant:
            logger.warning("No tenant found in request")
            return None

        if tenant.status != 'active':
            logger.warning(f'Tenant {tenant.subdomain} is not active')
            return None

        try:
            if tenant.tenant_type == 'standard':
                return self._authenticate_standard_tenant(tenant, username, password)
            else:
                return self._authenticate_premium_tenant(tenant, username, password)
        except Exception as e:
            logger.exception(f'Authentication error for tenant {tenant.subdomain}: {str(e)}')
            return None

    def _authenticate_standard_tenant(self, tenant, username, password):
        UserModel = get_user_model()

        with tenant_context(tenant):
            try:
                user = UserModel.objects.get(email=username, tenant=tenant)
            except UserModel.DoesNotExist:
                logger.debug(f'User {username} not found in tenant {tenant.subdomain}')

                # Dummy password verification preventing timing attacks
                password_router.set_tenant(tenant)
                password_router.verify_password(password, 'dummy_hash', tenant)
                return None
            return self._verify_user_password(user, password, tenant)

    def _authenticate_premium_tenant(self, tenant, username, password):
        UserModel = get_user_model()
        db_alias = tenant.database_name

        try:
            with connections[db_alias].cursor() as cursor:
                cursor.execute(
                    sql.SQL("SET search_path TO {}").format(
                        sql.Identifier(tenant.schema_name)
                    )
                )

            try:
                user = UserModel.objects.using(db_alias).get(email=username, tenant=tenant)
            except UserModel.DoesNotExist:
                logger.debug(f'User {username} not found in tenant {tenant.subdomain}')
                #prevent timing attacks
                password_router.set_tenant(tenant)
                password_router.verify_password(password, 'dummy_hash', tenant)
                return None

            return self._verify_user_password(user, password, tenant, db_alias=db_alias)

        except Exception as e:
            logger.error(f"Database error for premium tenant {tenant.subdomain}: {str(e)}")
            return None

    def _verify_user_password(self, user, password, tenant, db_alias=None):
        password_router.set_tenant(tenant)

        if not password_router.verify_password(password, user.password, tenant):
            logger.warning(f'Invalid password for user {user.email}')
            return None

        if user.is_password_expired():
            logger.warning(f'Password expired for user {user.email} in tenant {tenant.subdomain}')
            return None

        if password_router.needs_rehash(user.password, tenant):
            self._rehash_password(user, password, db_alias)

        return user

    def _rehash_password(self, user, password, db_alias=None):
        try:
            save_kwargs = {'update_fields': ['password', 'password_history']}

            if db_alias:
                save_kwargs['using'] = db_alias
            with transaction.atomic(using=db_alias):
                user.set_password(password)
                user.save(**save_kwargs)

                logger.info(f'Rehashed password for user {user.email}')
        except Exception as e:
            logger.error(f'Failed to rehash password for user {user.email}: {e}')

    def get_user(self, user_id):
        UserModel = get_user_model()

        try:
            user = UserModel.objects.get(pk=user_id)
            return user
        except UserModel.DoesNotExist:
            return None