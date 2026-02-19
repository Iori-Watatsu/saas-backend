from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.generics import CreateAPIView, UpdateAPIView
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken
import logging
from users.serializers import (
    TenantLoginSerializer,
    SocialLoginSerializer,
    TokenRefreshSerializer,
    UserRegistrationSerializer,
    TwoFactorSetupSerializer,
    TwoFactorConfirmSerializer,
    TwoFactorVerifySerializer,
    CustomUserDetailSerializer,
    UserProfileUpdateSerializer,
    ChangePasswordSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    APIKeyListSerializer,
    APIKeyCreateSerializer,
    APIKeyDetailSerializer,
)
from users.models import CustomUser,TwoFactorAuth
from users.api_auth import APIKey, APIKeyAuthenticationBackend
from tenant.models import Tenant
from users.auth_decorators import JWTRequiredMixin, APIKeyRequiredMixin, BothAuthMethodsMixin

logger = logging.getLogger(__name__)

# Create your views here.

# POST endpoint for traditional username/password login with tenant context
class TenantLoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = TenantLoginSerializer
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        tenant = serializer.validated_data['tenant']

        # Set tenant on request
        request.tenant = tenant

        # Check if 2FA is required
        if user.two_factor_enabled:
            logger.info(f'2FA required for user {user.email}')
            return Response({
                'requires_2fa': True,
                'user_id': str(user.id),
                'email': user.email,
            }, status=status.HTTP_202_ACCEPTED)

        # Generate JWT token
        refresh = RefreshToken.for_user(user)

        # Add tenant_id and custom claims to taken
        refresh['tenant_id'] = str(tenant.id)
        refresh['tenant_subdomain'] = tenant.subdomain
        refresh['email'] = user.email
        refresh['full_name'] = user.full_name
        refresh['role'] = user.role

        # Update last login
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        logger.info(f'User {user.email} logged in via password (tenant: {tenant.subdomain})')

        response_data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': CustomUserDetailSerializer(user).data,
        }

        return Response(response_data, status=status.HTTP_200_OK)

# POST endpoint for OAuth2 login (Google, Facebook, Microsoft, etc.)
class SocialLoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = SocialLoginSerializer
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        tenant = serializer.validated_data['tenant']

        # Set tenant on request
        request.tenant = tenant

        # Social auth shouldn't have 2FA (provider handles it)
        # but if user explicitly enabled 2FA, require it
        if user.two_factor_enabled:
            logger.info(f'2FA required for social user {user.email}')
            return Response({
                'requires_2fa': True,
                'user_id': str(user.id),
                'email': user.email,
            }, status=status.HTTP_202_ACCEPTED)

        # Generate JWT token
        refresh = RefreshToken.for_user(user)

        # Add tenant and custom claims
        refresh['tenant_id'] = str(tenant.id)
        refresh['tenant_subdomain'] = tenant.subdomain
        refresh['email'] = user.email
        refresh['full_name'] = user.full_name
        refresh['role'] = user.role

        # Update last login
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        provider = serializer.validated_data.get('provider', 'unknown')
        logger.info(
                f'User {user.email} logged in via {provider} '
                f'(tenant: {tenant.subdomain})'
        )

        response_data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': CustomUserDetailSerializer(user).data,
        }

        return Response(response_data, status=status.HTTP_200_OK)

# POST endpoint for refreshing JWT access token
class TokenRefreshView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(serializer.data, status=status.HTTP_200_OK)

# POST endpoint for logout (blacklist refresh token), JWT auth only
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get('refresh')

        if not refresh:
            return Response(
                {'error': 'Refresh token required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from rest_framework_simplejwt.tokens import RefreshToken
            token = RefreshToken(refresh)
            token.blacklist()

            logger.info(f'User {request.user.email} logged out')

            return Response({'success': True}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': f'Invalid token: {str(e)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

# POST endpoint for user registration
class RegisterView(CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)

        # Get user from validated data properly
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid:
            user = serializer.validated_data.get('user')
            logger.info(f'New user registration: {user.email}')

        return response

# POST endpoint for changing password (for logged-in users)
class ChangePasswordView(UpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={'user': request.user})
        serializer.is_valid(raise_exception=True)

        user = request.user
        new_password = serializer.validated_data['new_password']
        user.set_password(new_password)
        user.save()

        logger.info(f'User {user.email} changed password')

        return Response({'success': True}, status=status.HTTP_200_OK)

# POST endpoint to request password reset
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data.get('user')

        if user:
            # TODO: Generate reset token and send email
            logger.info(f'Password reset requested for user {user.email}')

        # Always return success for security (don't reveal user exists)
        return Response({
            'success': True,
            'message': 'If account exists, password reset email will be sent'
        }, status=status.HTTP_200_OK)

# POST endpoint to confirm password reset
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # TODO: Verify token and set password

        return Response({'success': True}, status=status.HTTP_200_OK)

# GET endpoint to start 2FA. Returns secret and QR code URI
class TwoFactorSetupView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = TwoFactorSetupSerializer(
            data={},
            context={'user': request.user}
        )

        return Response(serializer.data, status=status.HTTP_200_OK)

# POST endpoint to confirm 2FA setup. Verifies TOTP code enables 2FA
class TwoFactorConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TwoFactorConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        secret = serializer.validated_data['secret']
        user = request.user

        # Create or update 2FA record
        two_fa, created = TwoFactorAuth.objects.update_or_create(
            user=user,
            defaults={
                'secret': secret,
                'is_active': True,
            }
        )

        # Generate backup codes
        import secrets
        backup_codes = [secrets.token_hex(4) for _ in range(10)]
        two_fa.backup_codes = backup_codes
        two_fa.save()

        logger.info(f'User {user.email} enabled 2FA')

        return Response({
            'success': True,
            'backup_codes': backup_codes,
            'message': 'Save these backup codes in a safe place. You can use them to access your account if you lose your authenticator device,',
        }, status=status.HTTP_201_CREATED)

# POST endpoint to verify 2FA code during login. Returns JWT token after successful verification
class TwoFactorVerifyView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TwoFactorVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        tenant = user.tenant

        # Generate JWT token
        refresh = RefreshToken.for_user(user)

        # Add tenant and custom claims
        refresh['tenant_id'] = str(tenant.id)
        refresh['tenant_subdomain'] = tenant.subdomain
        refresh['email'] = user.email
        refresh['full_name'] = user.full_name
        refresh['role'] = user.role

        # Update last login
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        logger.info(f'User {user.email} passed 2FA verification')

        response_data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': CustomUserDetailSerializer(user).data
        }

        return Response(response_data, status=status.HTTP_200_OK)

# POST endpoint to disable 2FA. Requires password confirmation
class TwoFactorDisableView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        password = request.data.get('password')

        if not password:
            return Response(
                {'error': 'Password required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user

        if not user.check_password(password):
            return Response(
                {'error': 'Invalid password'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Disable 2FA
        TwoFactorAuth.objects.filter(user=user).update(is_active=False)

        logger.info(f'User {user.email} disabled 2FA')

        return Response({'success': True}, status=status.HTTP_200_OK)

# GET endpoint for current user. Works with bot JWT and API key auth
class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = CustomUserDetailSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

# PATCH endpoint to update user profile
class UserProfileUpdateView(UpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileUpdateSerializer

    def get_object(self):
        return self.request.user

# GET: List all API keys for current user
# POST: Create new API key
if APIKey is not None:
    class APIKeyListCreateView(APIView):
        permission_classes = [IsAuthenticated]

        # List API keys. Ensure not using API key to list keys
        def get(self, request):
            if hasattr(request, 'api_key') and request.api_key:
                return Response(
                    {'error': 'Cannot list API keys using API key auth'},
                    status=status.HTTP_403_FORBIDDEN
                )

            api_keys = APIKey.objects.filter(
                user=request.user,
                tenant=request.tenant
            ).order_by('-created_at')

            serializer = APIKeyListSerializer(api_keys, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Create new API key. Ensure not using key to create keys
        def post(self, request):
            if hasattr(request, 'api_key') and request.api_key:
                return Response(
                    {'error': 'Cannot create API key using API key auth'},
                    status=status.HTTP_403_FORBIDDEN
                )

            serializer = APIKeyCreateSerializer(
                data=request.data,
                context={'user': request.user, 'tenant': request.tenant}
            )
            serializer.is_valid(raise_exception=True)

            api_key_data = serializer.save()

            logger.info(
                f"API key created: {api_key_data['name']} "
                f"(user: {request.user.email}, tenant: {request.tenant.subdomain})"
            )

            return Response(api_key_data, status=status.HTTP_201_CREATED)

    # GET: Retrieve API key details
    # DELETE: Revoke API key
    class APIKeyDetailView(APIView):
        permission_classes = [IsAuthenticated]

        def get_object(self, api_key_id):
            try:
                api_key = APIKey.objects.get(
                    id=api_key_id,
                    user=self.request.user,
                    tenant=self.request.tenant
                )
                return api_key
            except APIKey.DoesNotExist:
                return None

        # Get API key details
        def get (self, request, api_key_id):
            api_key = self.get_object(api_key_id)

            if not api_key:
                return Response(
                    {'error': 'API key not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            serializer = APIKeyDetailSerializer(api_key)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # Revoke delete API key
        def delete(self, request, api_key_id):
            api_key = self.get_object(api_key_id)

            if not api_key:
                return Response(
                    {'error': 'API key not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            backend = APIKeyAuthenticationBackend()
            backend.revoke_api_key(api_key.id)

            logger.info(
                f"API key revoked: {api_key.name} "
                f"(user: {request.user.email})"
            )

            return Response(
                {'success': True, 'message': 'API key revoked'},
                status=status.HTTP_200_OK
            )

# Alternative: ViewSet for API key management with DRF routing
class APIKeyViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    # List API keys
    def list(self, request):
        api_key = APIKey.objects.filter(
            user=request.user,
            tenant=request.tenant
        ).order_by('-created_at')

        serializer = APIKeyListSerializer(api_key, many=True)
        return Response(serializer.data)

    # Create API key
    def create(self, request):
        serializer = APIKeyCreateSerializer(
            data=request.data,
            context={'user': request.user, 'tenant': request.tenant}
        )
        serializer.is_valid(raise_exception=True)

        api_key_data = serializer.save()
        return Response(api_key_data, status=status.HTTP_201_CREATED)

    # Retrieve API key details
    def retrieve(self, request, pk=None):
        try:
            api_key = APIKey.objects.get(
                id=pk,
                user=request.user,
                tenant=request.tenant
            )
        except APIKey.DoesNotExist:
            return Response(
                {'error': 'Not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = APIKeyDetailSerializer(api_key)
        return Response(serializer.data)

    # Revoke API key
    def destroy(self, request, pk=None):
        try:
            api_key = APIKey.objects.get(
                id=pk,
                user=request.user,
                tenant=request.tenant
            )
        except APIKey.DoesNotExist:
            return Response(
                {'error': 'Not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        backend = APIKeyAuthenticationBackend()
        backend.revoke_api_key(api_key.id)

        return Response({'success': True}, status=status.HTTP_200_OK)