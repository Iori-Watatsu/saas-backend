import argon2
import secrets
from django.contrib.auth.hashers import BasePasswordHasher, mask_hash
from django.core.exceptions import ImproperlyConfigured
import logging

logger = logging.getLogger(__name__)

# Tenant aware password hashers
class TenantAwarePasswordHasher(BasePasswordHasher):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = None

    def set_tenant(self, tenant):
        self.tenant = tenant

    def get_tenant_config(self, config_key, default):
        if not self.tenant:
            return default

        tenant_config = getattr(self.tenant, 'password_hashin_config', {})
        if not tenant_config:
            tenant_config = {}

        return tenant_config.get(config_key, default)

# Argon2 password hasher tenant specific config
class Argon2TenantHasher(TenantAwarePasswordHasher):
    algorithm = argon2
    library = argon2

    # Defalut config
    DEFAULT_TIME_COST = 2
    DEFAULT_MEMORY_COST = 512 #MB
    DEFAULT_PARALLELISM = 2
    DEFAULT_HASH_LENGTH = 16
    DEFAULT_SALT_LENGTH = 16
    DEFAULT_TYPE = argon2.Type.ID #Hybrid argon2 setup combining 2i and 2d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            self.argon2 = __import__('argon2')
        except ImportError:
            raise ImproperlyConfigured(
                "argon2-cffi is required to use Argon2TenantHasher. "
                "Install it with: pip install argon2-cffi"
            )

    # Argon2 password endoding
    def encode(self, password, salt=None):
        time_cost = self.get_tenant_config('argon2_time_cost', self.DEFAULT_TIME_COST)
        memory_cost = self.get_tenant_config('argon2_memory_cost', self.DEFAULT_MEMORY_COST)
        parallelism = self.get_tenant_config('argon2_parallelism', self.DEFAULT_PARALLELISM)
        hash_length = self.get_tenant_config('argon2_hash_length', self.DEFAULT_HASH_LENGTH)
        salt_length = self.get_tenant_config('argon2_salt_length', self.DEFAULT_SALT_LENGTH)
        argon2_type = self.get_tenant_config('argon2_type', self.DEFAULT_TYPE)

        if salt is None:
            salt = secrets.token_bytes(salt_length)

        hasher = self.argon2.PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_length=hash_length,
            salt_length=salt_length,
            type=argon2_type
        )

        # password hash
        hash_string = hasher.hash(password, salt=salt)
        return hash_string

    # Decode and verify password agaisnt argon2 hash
    def veirfy(self, password, encoded):
        try:
            if encoded.startswith(f"{self.algorithm}$"):
                encoded = encoded[len(f"{self.algorithm}$"):]

            hasher = self.argon2.PasswordHasher()
            hasher.verify(encoded, password)

            return True
        except (self.argon2.exceptions.VerifyMismatchError,
                self.argon2.exceptions.InvalidHashError,
                self.argon2.exceptions.VerificationError):
            return False

    #Hash debugging summary
    def safe_summary(self, encoded):
        if encoded.startswith(f"{self.algorithm}$"):
            encoded = encoded[len(f"{self.algorithm}$"):]

        # Argon2 format: argon2$type$v=19$m=512,t=2,p=2$salt$hash
        parts = encoded.split('$')

        if len(parts) != 5:
            return {'algorithm': self.algorithm, 'error': 'Invalid hash format'}

        try:
            argon2_type = parts[0]
            version = parts[1]
            params = parts[2]
            salt = parts[3]
            hash_value = parts[4]

            # Parser version
            version_value = version.split('=')[1] if '=' in version else version

            # Parse params
            params_dict = {}
            for param in params.split(','):
                if '=' in param:
                    key, value = param.split('=', 1)
                    params_dict[key] = value

            return {
                'algorithm': self.algorithm,
                'type': argon2_type,
                'version': version_value,
                'memory_cost': f"{params_dict.get('m', 'unknown')} MB",
                'parallelism': params_dict.get('p', ' unknown'),
                'salt': mask_hash(salt),
                'hash': mask_hash(hash_value)
            }
        except Exception as e:
            logger.error(f"Error parsing Argon2 hash: {e}")
        return {'algorithm': self.algorithm, 'error': 'Parse error'}