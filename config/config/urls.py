"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.urls import app_name

from tenant.views import TenantView
from users import views

# Configure app api root view and urls
router = DefaultRouter()
router.register(r'tenent', TenantView, basename='tenant')
router.register(r'api-keys-viewset', views.APIKeyViewSet, basename='api-key-viewset')

app_name = 'users'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),

    # Auth endpoints
    path('auth/login/', views.TenantLoginView.as_view(), name='login'), # JWT Login,
    path('auth/social/login/', views.SocialLoginView.as_view(), name='social-login'), # OAuth
    path('auth/token/refresh/', views.TokenRefreshView.as_view(), name='token-refresh'), # Token refresh
    path('auth/login/', views.LogoutView.as_view(), name='logout'), #logout

    # Registration & Password Management
    path('auth/register/', views.RegisterView.as_view(), name='register'), # User reg
    path('auth/password/change/', views.ChangePasswordView.as_view(), name='change-password'), # Change password
    path('auth/password/reset/', views.PasswordResetRequestView.as_view(), name='password-reset'), # Password reset
    path('auth/password/reset/confirm/', views.PasswordResetConfirmView, name='password-reset-confirm'), # Password reset confirmation

    # 2FA Auth
    path('auth/2fa/setup/', views.TwoFactorSetupView.as_view(), name='2fa-setup'), # Setup 2FA (get secret & QR code)
    path('auth/2fa/setup/confirm/', views.TwoFactorConfirmView.as_view(), name='2fa-confirm'), # 2FA setup confirmation
    path('auth/2fa/verify/', views.TwoFactorVerifyView.as_view(), name='2fa-verify'), # Verify 2FA code during login
    path('auth/2fa/disable/', views.TwoFactorDisableView.as_view(), name='2fa-disable'), # Disable 2FA

    # User profile
    path('profile/', views.UserProfileView.as_view(), name='profile'), # Get current user profile
    path('profile/update/', views.UserProfileUpdateView.as_view(), name='profile-update'), # Update user profile

    # API key management
    path('api-keys/', views.APIKeyListCreateView.as_view(), name='api-key-list-create'), # List & create API keys (function-based views)
    path('api-keys/<uuid:api_key_id>/', views.APIKeyDetailView.as_view(), name='api-key-detail'), # Get & delete API key details (function-based views)
    path('', include(router.urls)), # API keys via viewset (alternative)
]
