import argon2
import secrets
from django.contrib.auth.hashers import BasePasswordHasher, mask_hash, PBKDF2PasswordHasher
from django.core.exceptions import ImproperlyConfigured
import logging
import bcrypt
import scrypt
import base64
import math
import binascii
import hashlib

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

        tenant_config = getattr(self.tenant, 'password_hashing_config', {})
        if not tenant_config:
            tenant_config = {}

        return tenant_config.get(config_key, default)

# Argon2 password hasher tenant specific config
class Argon2TenantHasher(TenantAwarePasswordHasher):
    algorithm = 'argon2'
    library = 'argon2'

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
    def verify(self, password, encoded):
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
                'time_cost': params_dict.get('t', 'unknown'),
                'memory_cost': f"{params_dict.get('m', 'unknown')} MB",
                'parallelism': params_dict.get('p', ' unknown'),
                'salt': mask_hash(salt),
                'hash': mask_hash(hash_value)
            }
        except Exception as e:
            logger.error(f"Error parsing Argon2 hash: {e}")
        return {'algorithm': self.algorithm, 'error': 'Parse error'}

# Bcrypt password hasher tenant specific config
class BcryptTenantHasher(TenantAwarePasswordHasher):
    algorithm = 'bcrypt'
    library = ('bcrypt', 'bcrypt')
    rounds = 12 # cost default

    # Bcrypt passowrd encoding
    def encode(self, password, salt=None):
        #bcrypt = self._load_library()

        # Tenant specific rounds
        rounds = self.get_tenant_config('bcrypt_rounds', self.rounds)
        rounds = max(4, min(31, rounds))

        # Salt generation if not provided
        if salt is None:
            salt = bcrypt.gensalt(rounds=rounds)
        else:
            #Ensure salt in bytes
            if  isinstance(salt, str):
                salt = salt.encode('utf-8')

        # Password hashing
        data = bcrypt.hashpw(password.encode('utf-8'), salt)

        # Bcrypt format: bcrypt$encoded
        return f"{self.algorithm}${data.decode('utf-8')}"

    # Decode and verify password agaisnt bcrypt hash
    def verify(self, password, encoded):
        #bcrypt = self._load_library()

        if encoded.startswith(f"{self.algorithm}$"):
            encoded = encoded[len(f"{self.algorithm}$"):]

        try:
            password_bytes = password.encode('utf-8')
            encoded_bytes = encoded.encode('utf-8')

            result = bcrypt.checkpw(password_bytes, encoded_bytes)
            return result
        except (ValueError, TypeError):
            return False

    # Hash debugging summary
    def safe_summary(self, encoded):
        if encoded.startswith(f"{self.algorithm}$"):
            encoded = encoded[len(f"{self.algorithm}$"):]

        if encoded.startswith('$2'):
            # bcrypt hash format: $2b$12$saltsaltsaltsaltsalthashhashhashhashhash
            parts = encoded.split('$')

            if len(parts) == 4:
                version = parts[1]
                rounds = parts[2]
                salt_and_hash = parts[3]

                return {
                    'algorithm': self.algorithm,
                    'version': version,
                    'rounds': rounds,
                    'salt': mask_hash(salt_and_hash[:22]),
                    'hash': mask_hash(salt_and_hash[22:]),
                }

        return {'algorithm': self.algorithm, 'error': 'Invalid hash format'}

    ## Checks if hash needds update
    def must_update(self, encoded):
        if not self.tenant:
            return False

        if encoded.startswith(f"{self.algorithm}$"):
            encoded = encoded[len(f"{self.algorithm}$"):]

        if encoded.startswith('$2'):
            parts = encoded.split('$')

            if len(parts) == 4:
                try:
                    current_rounds = int(parts[2])

                    preferred_rounds = self.get_tenant_config('bcrypt_rounds', self.rounds)

                    return current_rounds < preferred_rounds
                except (ValueError, IndexError):
                    return False

        return False

# Bcrypt password hasher tenant specific config with memory-hard Key Derivation Function resisting GPU/ASIC attack
class ScryptTenantHasher(TenantAwarePasswordHasher):
    algorithm = 'scrypt'

    DEFAULT_N = 2**14 # MEMORY COST FACTOR
    DEFAULT_R = 8 # BLOCK SIZE
    DEFAULT_P = 1 #PARALLELIZATION FACTOR
    DEFAULT_KEY_LENGTH = 32 # 256-BIT OUTPUT
    DEFAULT_SALT_LENGTH = 16

    def __init__(self, *args, **kwaargs):
        super().__init__(*args, **kwaargs)
        self.scrypt = scrypt

    # Scrypt password encoding
    def encode(self, password, salt=None):
        N = self.get_tenant_config('scrypt_N', self.DEFAULT_N)
        r = self.get_tenant_config('scrypt_r', self.DEFAULT_R)
        p = self.get_tenant_config('scrypt_p', self.DEFAULT_P)
        key_length = self.get_tenant_config('scrypt_key_length', self.DEFAULT_KEY_LENGTH)
        salt_length = self.get_tenant_config('scrypt_salt_length', self.DEFAULT_SALT_LENGTH)

        # Salt generation if not provided
        if salt is None:
            salt = secrets.token_bytes(salt_length)
        elif isinstance(salt, str):
            salt = base64.b64decode(salt)

        # Ensure correct salt length
        if len(salt) != salt_length:
            raise ValueError(f"Salt must be {salt_length} bytes")

        # Scrypt hashing
        try:
            hash_bytes = self.scrypt.hash(
                password.encode('utf-8'),
                salt=salt,
                N=N,
                r=r,
                p=p,
                buflen=key_length
            )
        except MemoryError:
            # Fallback to less memory-intensive parameters
            logger.warning("Memory error in scrypt, using reduced parameters")
            N = 2**12  # Reduce memory usage
            hash_bytes = self.scrypt.hash(
                password.encode('utf-8'),
                salt=salt,
                N=N,
                r=r,
                p=p,
                buflen=key_length
            )

        # base64 salt and has encoding
        salt_b64 = base64.b64encode(salt).decode('ascii')
        hash_b64 = base64.b64encode(hash_bytes).decode('ascii')

        # Scrypt format: scrypt$N,r,p$salt$hash
        return f"{self.algorithm}${N},{r},{p}${salt_b64}${hash_b64}"

    # Scrypt hash password decode and verification
    def verify(self, password, encoded):
        try:
            if encoded.startswith(f"{self.algorithm}$"):
                encoded = encoded[len(f"{self.algorithm}$"):]

            parts = encoded.split('$')

            if len(parts) != 3:
                return False

            params_str, salt_b64, hash_b64 = parts

            params = params_str.split(',')
            if len(params) != 3:
                return False

            N, r, p = map(int, params)

            salt = base64.b64decode(salt_b64)
            expected_hash = base64.b64decode(hash_b64)

            computed_hash = self.scrypt.hash(
                password.scrypt('utf-8'),
                salt=salt,
                N=N,
                r=r,
                p=p,
                buflen=len(expected_hash),
            )

            return secrets.compare_digest(computed_hash, expected_hash)

        except (ValueError, TypeError, binascii.Error):

            return False
    # Hash summary for debugging
    def safe_summary(self, encoded):
        if encoded.startswith(f"{self.algorithm}$"):
            encoded = encoded[len(f"{self.algorithm}$"):]

        parts = encoded.split('$')
        if len(parts) != 3:
            return {'algorithm': self.algorithm, 'error': 'Invalid has format'}

        params_str, salt_b64, hash_b64 = parts

        try:
            params = params_str.split(',')
            N, r, p = map(int, params)

            if N > 0 and (N & (N - 1)) == 0: # Check power of 2
                log_n = int(math.log2(N))
                n_display = f"2^{log_n} ({N})"

            else:
                n_display = str(N)

            return {
                'algorithm': self.algorithm,
                'N (CPU/memory cost)': n_display,
                'r (block size)': r,
                'p (parallelization)': p,
                'salt': mask_hash(salt_b64),
                'hash': mask_hash(hash_b64),
            }
        except(ValueError, IndexError):
            return {'algorithm': self.algorithm, 'error': 'Invalid parameters'}

    # Check for hash update
    def must_update(self, encoded):
        if not self.tenant:
            return False
        if encoded.startswith(f"{self.algorithm}$"):
            encoded = encoded[len(f"{self.algorithm}$"):]

        parts = encoded.split('$')
        if len(parts) != 3:
            return True

        params_str = parts[0]
        params = params_str.split(',')

        try:
            current_N, current_r, current_p = map(int, params)

            preferred_N = self.get_tenant_config('scrypt_N', self.DEFAULT_N)
            preferred_r = self.get_tenant_config('scrypt_r', self.DEFAULT_R)
            preferred_p = self.get_tenant_config('scrypt_p', self.DEFAULT_P)

            return (
                current_N != preferred_N or
                current_r != preferred_r or
                current_p != preferred_p
            )
        except (ValueError, IndexError):
            return True

# PBKDF2 password hasher with tenant-specific configuration
class PBKDF2TenantHasher(TenantAwarePasswordHasher):
    algorithm = 'pbkdf_sha256_tenant'

    DEFAULT_ITERATIONS = 260000 # Updated from 180000 for better security
    DEFAULT_DIGEST = hashlib.sha256

    # PBKDF@ password encoder
    def encode(self, password, salt=None, iterations=None):
        if iterations is None:
            iterations = self.get_tenant_config('pbkdf2_iterations', self.DEFAULT_ITERATIONS)

        # Get digest algorithm
        digest = self.get_tenant_config('pbkdf2_digest', self.DEFAULT_DIGEST)

        # Salt generations
        if salt is None:
            salt = secrets.token_urlsafe(12)[:16] # 12 bytes = 16 base64 chars

        # Hash the password
        hash_value = hashlib.pbkdf2_hmac(
            digest().name,
            password.encode('utf-8'),
            self.encode('ascii'),
            iterations
        )
        hash_b64 = base64.b64encode(hash_value).decode('ascii').strip()

        # PBKDF2 format: algorithm$iterations$salt$hash
        return f"{self.algorithm}${iterations}${salt}${hash_b64}"

    def verify(self, password, encoded):
        try:
            if not encoded.startswith(f"{self.algorithm}$"):
                return False

            algorithm, iterations, salt, hash_value = encoded.split('$', 3)
            iterations = int(iterations)

            encoded_2 = self.encode(password, salt, iterations)

            # Constant-time comparison
            return secrets.compare_digest(encoded, encoded_2)

        except (ValueError, TypeError):
            return False

    # Hash debugging summary
    def safe_summary(self, encoded):
        try:
            if not encoded.startswith(f"{self.algorithm}$"):
                return {'algorithm': self.algorithm, 'error': 'Invalid algorithm'}

            algorithm, iterations, salt, hash_value = encoded.split('$', 3)

            return {
                'algorithm': self.algorithm,
                'iterations': iterations,
                'salt': mask_hash(salt),
                'hash': mask_hash(hash_value),
            }
        except (ValueError, IndexError):
            return {'algorithm': self.algorithm, 'error': 'Invalid hash format'}

    def must_update(self, encoded):
            if not self.tenant:
                return False

            try:
                if not encoded.startswith(f"{self.algorithm}$"):
                    return False

                algorithm, iterations, salt, hash_value = encoded.split('$', 3)
                current_iterations = int(iterations)

                preferred_iterations = self.get_tenant_config('pbkdf2_iterations', self.DEFAULT_ITERATIONS)

                # Update if current iterations are less than preferred
                return current_iterations < preferred_iterations

            except (ValueError, IndexError):
                return True