from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Q
from users.api_auth import APIKey, APIKeyAuthenticationBackend, API_VERSION_SCOPES
from datetime import timedelta
from django.utils import timezone

# Register your models here.

# Django admin customization for API key management
class APIKeyAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'prefix_display',
        'user_email',
        'tenant_name',
        'versions_display',
        'status_display',
        'last_used_display',
        'expires_display'
    )
    list_filter = (
        'is_active',
        'tenant',
        'created_at',
        'expires_at',
        'can_read',
        'can_write',
        'can_delete'
    )
    search_fields = (
        'name',
        'key_prefix',
        'user__email',
        'tenant__subdomain'
    )
    readonly_fields = (
        'id',
        'key_prefix',
        'created_at',
        'last_used',
        'scopes_display',
        'versions_display'
    )
    fieldsets = (
        ('Key Information', {
            'fields': ('id', 'name', 'key_prefix', 'user', 'tenant')
        }),
        ('Versions & Scopes', {
            'fields': ('api_versions', 'scopes_display', 'scopes'),
        }),
        ('Permissions', {
            'fields': ('can_read', 'can_write', 'can_delete'),
        }),
        ('Status', {
            'fields': ('is_active', 'expires_at', 'created_at', 'last_used'),
        }),
    )
    actions = [
        'revoke_keys',
        'extend_expiration_30_days',
        'extend_expiration_90_days',
        'add_v2_support',
        'add_v3_support'
    ]

    # Display key prefix
    def prefix_display(self, obj):
        return f"{obj.key_prefix}..." if obj.key_prefix else "-"
    prefix_display.short_description = 'Key Prefix'

    # Display user email
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User'

    # Display tenant name
    def tenant_name(self, obj):
        return obj.tenant.subdomain
    tenant_name.short_description = 'Tenant'

    # Display supported API versions
    def versions_display(self, obj):
        if not obj.api_versions:
            return format_html(
                '<span style="color: orange;">All version (legacy)</span>'
            )

        versions = ', '.join(obj.api_versions)
        return format_html(
            '<span style="color: green; font-weight: bold;">{}</span>',
            versions
        )
    versions_display.short_description = 'API Versions'

    # Display active/inactive status
    def status_display(self, obj):
        if obj.is_inactive:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Active</span>'
            )
        else:
            return format_html(
                '<span style="color: red; font-weight: bold;">✗ Revoked</span>'
            )
    status_display.short_description = 'Status'

    # Display last used with relative time
    def last_used_display(self, obj):
        if not obj.last_used:
            return "Never"

        diff = timezone.now() - obj.last_used
        if diff.days == 0:
            return "Today"
        elif diff.days == 1:
            return "Yesterday"
        elif diff.days < 30:
            return f"{diff.days} days ago"
        else:
            return obj.last_used.strftime('%Y-%m-%d')
    last_used_display.short_description = 'Last Used'

    # Display expiration status
    def expires_display(self, obj):
        if not obj.expires_at:
            return format_html(
                '<span style="color: gray;">No expiration</span>'
            )

        if not obj.is_valid():
            return format_html(
                '<span style="color: red;">✗ Expired</span>'
            )

        diff = obj.expires_at - timezone.now()
        if diff.days < 0:
            return format_html(
                '<span style="color: red;">Expired</span>'
            )
        elif diff.days < 7:
            return format_html(
                '<span style="color: orange; font-weight: bold;">{} days left</span>',
                diff.days
            )
        else:
            return obj.expires_at.strftime('%Y-%m-%d')
    expires_display.short_description = 'Expires'

    # Display scopes in a readavle format
    def scopes_display(self, obj):
        if not obj.scopes:
            return "No scopes"

        scopes_html = '<br>'.join(
            f'<code>{scope}</code>'
            for scope in sorted(obj.scopes)
        )
        return format_html(scopes_html)
    scopes_display.short_description = 'Scopes'

    # Admin action to revoke multiple keys
    def revoke_keys(self, request, queryset):
        backend = APIKeyAuthenticationBackend()
        count = 0
        for api_key in queryset:
            if backend.revoke_api_key(api_key.id):
                count += 1

        self.message_user(
            request,
            f"Successfully revoked {count} API key(s)"
        )
    revoke_keys.short_description = "Revoke selected API keys"

    # Extend expiration by 30 days
    def extend_expiration_30_days(self, request, queryset):
        new_expiry = timezone.now() + timedelta(days=30)
        count = queryset.update(expires_at=new_expiry)

        self.message_user(
            request,
            f"Extended {count} API key(s) expiration to {new_expiry.date()}"
        )
    extend_expiration_30_days.short_description = "Extend expiration by 30 days"

    # Extend expiration by 90 days
    def extend_expiration_90_days(self, request, queryset):
        new_expiry = timezone.now() + timedelta(days=90)
        count = queryset.update(expires_at=new_expiry)

        self.message_user(
            request,
            f"Extended {count} API key(s) expiration to {new_expiry.date()}"
        )
    extend_expiration_90_days.short_description = "Extend expiration by 90 days"

    # Add v2 supposrt to selected keys
    def add_v2_support(self, request, queryset):
        updated = 0
        for api_key in queryset:
            if 'v2' not in api_key.api_versions:
                api_key.api_versions.append('v2')
                api_key.save()
                updated += 1

        self.message_user(
            request,
            f"Added v2 support to {updated} API key(s)"
        )
    add_v2_support.short_description = "Add v2 API support"

    # Add v3 supposrt to selected keys
    def add_v3_support(self, request, queryset):
        updated = 0
        for api_key in queryset:
            if 'v3' not in api_key.api_versions:
                api_key.api_versions.append('v3')
                api_key.save()
                updated += 1

        self.message_user(
            request,
            f"Added v3 support to {updated} API key(s)"
        )
    add_v3_support.short_description = "Add v3 API support"

admin.site.register(APIKey, APIKeyAdmin)

