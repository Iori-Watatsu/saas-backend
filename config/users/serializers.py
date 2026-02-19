from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from users.models import CustomUser
from users.api_auth import APIKey, APIKeyAuthenticationBackend
from tenant.models import Tenant
import pyotp
import logging

from users.two_factor_auth import TwoFactorAuthenticationBackend

logger = logging.getLogger(__name__)

class CustomUserSerialers(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = [
            'id', 'email', 'full_name', 'role', 'is_active',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class CustomUserDetailSerializer(serializers.ModelSerializer):
    tenant_subdomain = serializers.CharField(
        source='tenant.subdomain',
        read_only=True
    )
    two_factor_enabled = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            'id', 'email', 'full_name', 'role', 'is_active',
            'tenant_subdomain', 'two_factor_enabled',
            'created_at', 'updated_at', 'last_login'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_login']

    # Check if user 2FA is enabled
    def get_two_factor_enabled(self, obj):
        return obj.two_factor_enabled

# Custom JWT serializer that adds tenant_id to token claims used when users obtain JWT tokens
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    # Add tenant_subdomain field for login
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subdomain'] = serializers.CharField(required=True)

    @classmethod
    # Override to add custom claims to token
    def get_token(cls, user):
        token = super().get_token(user)

        # Add tenant_id to token
        if hasattr(user, 'tenant'):
            token['tenant_id'] = str(user.tenant.id)
            token['tenant_subdomain'] = user.tenant.subdomain

        # Add user info
        token['email'] = user.email
        token['full_name'] = user.full_name
        token['roke'] = user.role

        return token

    def validate(self, attrs):
        subdomain = attrs.pop('subdomain', None)

        if not subdomain:
            raise ValidationError({'subdomain': 'Subdomain is required'})

        # Get tenant
        try:
            tenant = Tenant.objects.get(subdomain=subdomain, status='active')
        except Tenant.DoesNotExist:
            raise ValidationError({'subdomain': 'Invalid tenant'})

        # Get the request from context
        request = self.context.get('request')
        if request:
            request.tenant = tenant

        # Authenticate
        try:
            data = super().validate(attrs)
        except serializers.ValidationError as e:
            logger.warning(
                f'Authenticate failed for tenant {subdomain}: {e.detail}'
            )
            raise

        # Check 2FA requirement
        user = self.user
        if user.two_factor_enabled:
            data = {
                'requires_2fa': True,
                'users_id': str(user.id),
                'email': user.email,
            }

        return data

# Serializer for traditional username/password logi including tenant context
class TenantLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    subdomain = serializers.CharField()

    # Authenticate user with tenantv context
    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')
        subdomain = attrs.get('subdomain')

        # get tenant
        try:
            tenant = Tenant.objects.get(subdomain=subdomain, status='active')
        except Tenant.DoesNotExist:
            raise ValidationError('Invalid tenant')

        # Authenticate user
        user = authenticate(username=email, password=password)

        if not user:
            raise ValidationError('Invalid email or password')

        # Check if user is in this tenant
        if user.tenant != tenant:
            raise ValidationError('User account is inactive')

        # Store for later use
        attrs['user'] = user
        attrs['tenant'] = tenant

        return attrs

# Social auth serializer
class SocialLoginSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=['google', 'facebook', 'microsoft'])
    access_token = serializers.CharField()
    subdomain = serializers.CharField()

    # Validate if provider is enabled
    def validated_provider(self, value):
        from django.conf import settings
        enabled_providers = settings.AUTHENTICATION.get('SOCIAL_AUTH', {})

        if not enabled_providers.get(value, {}).get('enabled'):
            raise ValidationError(f'{value} authentications is not enabled')

        return value

    # Validated social auth
    def validate(self, attrs):
        from users.social_auth import SocialAuthenticationBackend

        provider = attrs.get('provider')
        access_token = attrs.get('access_token')
        subdomain = attrs.get('subdomain')

        # Get tenant
        try:
            tenant = Tenant.objects.get(subdomain=subdomain, status='active')
        except Tenant.DoesNotExist:
            raise ValidationError('Invalid tenant')

        # Authenticate with social backend
        backend = SocialAuthenticationBackend()
        user = backend.authenticate_social(None, provider, access_token, tenant)

        if not user:
            raise ValidationError(f'{provider} authentication failed')

        attrs['user'] = user
        attrs['tenant'] = tenant

        return attrs

# JWT token refresh serializer
class TokenRefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()

    def validate(self, value):
        try:
            token = RefreshToken(value)
            return token
        except Exception as e:
            raise ValidationError(f'Invalid refresh token: {str(e)}')

    # Generate new access token
    def to_representation(self, instance):
        token = instance
        return {
            'access': str(token.access_token),
            'refrsh': str(token), # New refresh token (if rotating)
        }

class UserRegistrationSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, required=True)
    password_confirm = serializers.CharField(write_only=True, required=True)
    subdomain = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = CustomUser
        fields = ['email', 'password', 'password_confirm', 'full_name', 'subdomain']

    # Validate password strength
    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise ValidationError(list(e.messages))

        return value

    # Validate password confirmation and tenant
    def validate(self, attrs):
        password = attrs.get('password')
        password_confirm = attrs.pop('password_confirm')

        if password != password_confirm:
            raise ValidationError({'password': 'Passwords do not match'})

        # Get tenant
        subdomain = attrs.pop('subdomain')
        try:
            tenant = Tenant.objects.get(subdomain=subdomain, status='active')
        except Tenant.DoesNotExist:
            raise ValidationError({'subdomain': 'Invalid tenant'})

        attrs['tenant'] = tenant

        return attrs

    # Create user with hashed password
    def create(self, validated_data):
        password = validated_data.pop('password')
        tenant = validated_data.pop('tenant')

        user = CustomUser.objects.create_user(
            tenant=tenant,
            password=password,
            **validated_data
        )

        logger.info(
            f'New user registered: {user.email} in tenant {tenant.subdomain}'
        )

        return user

class TwoFactorSetupSerializer(serializers.Serializer):
    # Generate 2FA secret and QR code
    def to_representation(self, instance):
        from django.conf import settings

        user = self.context.get('user')
        if not user:
            raise ValidationError('User required in context')

        # Generate secret
        secret = pyotp.random_base32()

        # Generate provisioning URI for QR code
        totp = pyotp.TOTP(secret)
        issuer = settings.AUTHENTICATION.get('2FA', {}).get('issuer_name', 'SaaSPLatfomr')
        uri = totp.provisioning_uri(
            name=user.email,
            issuer_name=issuer
        )

        return {
            'secret': secret,
            'qr_code_ur': uri,
            'message': 'Scan this QR code with you authenticator app (Google Authenticator, Authy, Microsoft Authenticator, etc.)',
        }

 #Serializer for confirming 2FA setup. Verifies TOTP code and enables 2FA
class TwoFactorConfirmSerializer(serializers.Serializer):
    secret = serializers.CharField(write_only=True)
    code = serializers.CharField(write_only=True)

    # Validate TOTP code format
    def validate(self, value):
        if not value.isdigit() or len(value) != 6:
            raise ValidationError('Code must be 6 digits')
        return value

    # Verify TOTP code matches secret
    def validate(self, attrs):
        secret = attrs.get('secrer')
        code = attrs.get('code')
        totp = pyotp.TOTP(secret)

        # Allow 30 seconds of skew for clock differences
        if not totp.verify(code, valid_window=1):
            raise ValidationError('Invalid code')

        return attrs

# 2FA verification serializer during login
class TwoFactorVerifySerializer(serializers.Serializer):
    user_id = serializers.CharField()
    code = serializers.CharField()

    # Validate code format
    def validate_code(self, value):
        if not value.isdigit() or len(value) != 6:
            raise ValidationError('Code must be 6 digits')
        return value

    # Verify TOTP code
    def validate(self, attrs):
        from uuid import UUID

        user_id = attrs.get('user_id')
        code = attrs.get('code')

        # get user
        try:
            user = CustomUser.objects.get(id=UUID(user_id))
        except (CustomUser.DoesNotExist, ValueError):
            raise ValidationError('Invalid user')

        # Verify 2FA
        if not user.two_factor_enabled:
            raise ValidationError('2FA not enabled for this user')

        # Get 2FA record
        try:
            two_fa = TwoFactorAuthenticationBackend.objects.get(user=user, is_active=True)
        except TwoFactorAuthenticationBackend.DoesNotExist:
            raise ValidationError('2FA configuration not found')

        # Verify code
        totp = pyotp.TOTP(two_fa.secret)
        if not totp.verify(code, valid_window=1):
            raise ValidationError('Invalid code')

        attrs['user'] = user

        return attrs

# Serializer for listing API keys (without secret). Shows masking prefix and metadata only
class APIKeyListSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    tenant_subdomain = serializers.CharField(source='tenant.subdomain', read_only=True)
    prefix_display = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = APIKey
        fields = [
            'id', 'name', 'prefix_display', 'user_email', 'tenant_subdomain',
            'api_versions', 'scopes', 'status', 'last_used', 'expires_at',
            'can_read', 'can_write', 'can_delete', 'created_at'
        ]
        read_only_fields = fields

    # Return masked prefix
    def get_prefix_display(self, obj):
        return f'{obj.key_prefix}...'

    # Return human-readable status
    def get_status(self, obj):
        if not obj.is_active:
            return 'Revoked'

        from django.utils import timezone
        if obj.expires_at and timezone.now() > obj.expires_at:
            return 'Expired'

        if obj.expires_at:
            diff = (obj.expires_at - timezone()).days
            if diff < 30:
                return f'Expiration soon ({diff} days)'

        return 'Active'

# Serializer for creating API keys. Returns the actual secret (shown once)
class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    scopes = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list
    )
    api_versions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list
    )
    expires_in_days = serializers.IntegerField(required=False)
    can_read = serializers.BooleanField(default=True)
    can_write = serializers.BooleanField(default=False)
    can_delete = serializers.BooleanField(default=False)

    def validate_api_versions(self, value):
        from users.api_auth import API_VERSION_SCOPES

        validate_version = list(API_VERSION_SCOPES.key())

        for version in value:
            if version not in validate_version:
                raise ValidationError(
                    f'Invalid version: {version}. Valid: {validate_version}'
                )

        return value

    # Validate scope/version compatibility
    def validate(self, attrs):
        from users.api_auth import APIKeyAuthenticationBackend, API_VERSION_SCOPES

        scopes = attrs.get('scopes')
        api_version = attrs.get('api_version')

        # If no versions specified, use all
        if not api_version:
            api_versions = list(API_VERSION_SCOPES.keys())
            attrs['api_versions'] = api_versions

        # Validate scopes exist in at least one version
        if scopes:
            backend = APIKeyAuthenticationBackend()
            if not backend._validate_scopes(scopes, api_versions):
                raise ValidationError(
                    'One or more scopes not available in selected versions'
                )

        return attrs

    # Create API key and return secret
    def create(self, validated_data):
        user = self.context.get('user')
        tenant = self.context.get('tenant')

        if not user or not tenant:
            raise  ValueError('User and tenant required in context')

        backend = APIKeyAuthenticationBackend()
        api_key_data = backend.create_api_key(
            tenant=tenant,
            user=user,
            name=validated_data.get('name'),
            scopes=validated_data.get('scopes'),
            api_versions=validated_data.get('api_versions'),
            expires_in_days=validated_data.get('expires_in_days'),
            can_read=validated_data.get('can_read', True),
            can_write=validated_data.get('can_write', False),
            can_delete=validated_data.get('can_delete', False),
        )

        return api_key_data

# Detailed API key serializer
class APIKeyDetailSerializer(serializers.ModelSerializer):
    effective_scopes_v1 = serializers.SerializerMethodField()
    effective_scopes_v2 = serializers.SerializerMethodField()
    effective_scopes_v3 = serializers.SerializerMethodField()
    prefix_display = serializers.SerializerMethodField()

    class Meta:
        model = APIKey
        fields = [
            'id', 'name', 'prefix_display', 'api_versions', 'scopes',
            'effective_scopes_v1', 'effective_scopes_v2', 'effective_scopes_v3',
            'can_read', 'can_write', 'can_delete', 'is_active',
            'last_used', 'expires_at', 'created_at'
        ]
        read_only_fields = fields

    def get_prefix_display(self, obj):
        return f'{obj.key_prefix}...'

    def get_effective_scopes_v1(self, obj):
        return obj.get_effective_scopes('v1')

    def get_effective_scopes_v2(self, obj):
        return obj.get_effective_scopes('v2')

    def get_effective_scopes_v3(self, obj):
        return obj.get_effective_scopes('v3')

# Serializers for revoking API keys
class APIKeyRevokeSerializer(serializers.Serializer):
    confirm = serializers.BooleanField(
        required=True,
        help_text='Must be true to confirm revocation'
    )

    def validate_confirm(self, value):
        if not value:
            raise ValidationError('Revocation must be confirmed')
        return value

# Serializer for user password change
class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        user = self.context.get('user')

        if not user.check_password(value):
            raise ValidationError('Old password is incorrect')

        return value

    # Validate new password strength
    def validate_new_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise ValidationError(list(e.messages))

        return value

    # Validate password confrimation
    def validate(self, attrs):
        new_password = attrs.get('new_password')
        new_password_confirm = attrs.pop('new_password_confirm')

        if new_password != new_password_confirm:
            raise ValidationError({'new_password': 'Passwords do not match'})

        user = self.context.get('user')
        if user.check_password(new_password):
            raise ValidationError({
                'new_password': 'New password cannot be the same as old password'
            })

        return attrs

# Serializers for requesting password reset
class PasswordResetRequestSerializer(serializers.Serializer):

    email = serializers.EmailField()
    subdomain = serializers.CharField()

    # Valid user exists in tenant
    def validate(self, attrs):
        email = attrs.get('email')
        subdomain = attrs.get('subdomain')

        try:
            tenant = Tenant.objects.get(subdomain=subdomain, status='active')
        except Tenant.DoesNotExist:
            # Don't reveal tenant doesn't exist for security
            raise ValidationError('If account exists, reset email will be sent')

        try:
            user = CustomUser.objects.get(email=email, tenant=tenant)
            attrs['user'] = user
        except CustomUser.DoesNotExist:
            # Don't reveal user doesn't exist for security
            pass

        return attrs

# Serializer for confiming password reset
class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)

    # Validate password strength
    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise ValidationError(list(e.message))

        return value

    # Validate token and password confirmation
    def validate(self, attrs):
        password = attrs.get('password')
        password_confirm = attrs.pop('password_confirm')

        if password != password_confirm:
            raise ValidationError({'password': 'Passwords do not match'})

        # TODO: Validate token (implement token generation in views)

        return attrs

# Serializer for updating user profile
class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['email', 'full_name', 'role']
        read_only_fields = ['email', 'role'] # Can't change email/role via API

    # Update user profile
    def update(self, instance, validated_data):
        instance.full_name = validated_data.get('full_name', instance.full_name)
        instance.save()

        logger.info(f'user profile updated: {instance.email}')

        return instance

# Serializer for token response, used in login endpoints
class TokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = CustomUserDetailSerializer()
    requires_2fa = serializers.BooleanField(required=False)

# Serializer for error reponses
class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()
    detail = serializers.CharField(required=False)
    code = serializers.CharField(required=False)