from celery.worker.state import reserved_requests
from rest_framework import serializers
from .models import Tenant
from.models import Tenant, Domain

# Serializer for tenant modeladmin operations
class TenantSerializer(serializers.ModelSerializer):

    # Computed fields
    database_type = serializers.SerializerMethodField()
    user_count= serializers.SerializerMethodField()
    project_count = serializers.SerializerMethodField()
    domain = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            'id',
            'name',
            'status',
            'subdomain',
            'tenant_type',
            'plan',
            'database_name',
            'max_users',
            'max_projects',
            'custom_domain',
            'created_at',
            'updated_at',
            # Computed fields
            'database_type',
            'user_count',
            'project_count',
            'domain'
        ]
        read_only_fields = [
            'id',
            'tenant_type',
            'database_name',
            'created_at',
            'updated_at',
            'database_type',
            'user_count',
            'project_count',
            'domain'
        ]

    @staticmethod
    def get_database_type(obj):
        if obj.tenant_type == 'premium':
            return 'Dedicated database'
        return 'Shared schema'

    @staticmethod
    def get_user_count(obj):
        try:
            from django_tenants.utils import tenant_context

            if obj.tenant_type == 'standard':
                with tenant_context(obj):
                    from users.models import User
                    return User.objects.filter(tenant=obj).count()
            else:
                return 'N/A'
        except:
            return 'Unkown'

    @staticmethod
    def get_projects_count(obj):
        try:
            from django_tenants.utils import tenant_context

            if obj.tenant_type == 'standard':
                with tenant_context(obj):
                    from project.models import Project
                    return Project.objects.filter(tenant=obj).count()
            else:
                return 'N/A'
        except:
            return 'Unknown'

    @staticmethod
    def get_domain(obj):
        try:
            primary_domain = Domain.objects.filter(
                tenant = obj,
                is_primary = True
            ).first()
            return primary_domain.doamin if primary_domain else None

        except:
            return None

    def validate_subdomain(self, value):
        import re

        value = value.lower()

        if len(value) < 3:
            raise serializers.ValidationError("Subdomain must be atleast 3 characters")
        if len(value) > 100:
            raise serializers.ValidationError("Subdomain cannot exceed 100 characters")

        if not re.match(r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$', value):
            raise serializers.ValidationError(
                "Subdomain can only contain lowercase letters, numbers, and hyphens. "
                "Cannot start or end with a hyphen."
            )

        reserved_subdomains = [
            'www', 'admin', 'api', 'app', 'dashboard', 'mail', 'blog',
            'support', 'help', 'status', 'dev', 'test', 'staging', 'prod'
        ]
        if value in reserved_subdomains:
            raise serializers.ValidationError(f"Subdomain '{value}' is reserved")

        # check for existings dubdomain
        instance = self.instance
        if instance:
            if Tenant.objects.exclude(id=instance.id).filter(subdomain=value).exists():
                raise serializers.ValidationError(f"Subdomain '{value}' is already taken")
        else:
            if Tenant.objects.filter(subdomain=value).exists():
                raise serializers.ValidationError(f"Subdomain '{value}' is already taken")

        return value

    # Handle tenant plan changes
    def update(self, instance, validated_data):
        old_plan = instance.plan
        new_plan = validated_data.get('plan', old_plan)

        instance = super().update(instance, validated_data)

        # Handle plan upgrade/downgrade
        if old_plan != new_plan:
            self.handle_plan_change(instance, old_plan, new_plan)

        return instance

    # Handle tenant plan change logic
    def handle_plan_change(self, tenant, old_plan, new_plan):
        from django.utils import timezone

        # Upgrade to premium
        if old_plan in ['free', 'starter'] and new_plan in ['professional', 'enterprise']:
            tenant.pla_change_date = timezone.now()
            tenant.plan_change_note = f"Upgraded from {old_plan} to {new_plan}"

            # Trigger async database creation (In production use celery or similar)
            self.trigger_database_creation(tenant)

        # Downgrade from premium
        elif old_plan in ['professional', 'enterprise'] and new_plan == 'starter':
            tenant.plan_change_date = timezone.now()
            tenant.plan_change_note = f"Downgraded from {old_plan} to {new_plan}"

            self.schedule_downgrade_migration(tenant, old_plan, new_plan)

        tenant.save()

    # A celery async database trigger task in production, this is logging just for dev
    @staticmethod
    def trigger_database_creation(tenant):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Triggered tenant database creation {tenant.subdomain}")

    #Schedule database migration for schema downgrades
    @staticmethod
    def shcedule_downgrade_mirgration(tenant):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Sheduling migration downgrade for{tenant.subdomain}")

# Domain Model Serializer
class DomainSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source='tenant.companyname', read_only=True)

    class Meta:
        model = Domain
        fields = [
            'id',
            'domain',
            'tenant',
            'tenant_name',
            'is_primary',
            'is_custom',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

        def __init__(self):
            self.instance = None

        def validate_domain(self, value):
            import re

            if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$', value):
                raise serializers.ValidationError("Enter a valid domain name")

            # Checkdomain registration
            instance = self.instance
            if instance:
                if Domain.objects.exlude(id=instance.id).filter(domain=value).exists():
                    raise serializers.ValidationError(f"Domain '{value}' aldready registered")
            else:
                if Domain.objects.filter(domain=value).exists():
                    raise serializers.ValidationError(f"Domain '{value}' already registered")

            return value.lower()

# Tenant stats serializer
class TenantStatsSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    subdomain = serializers.CharField(read_only=True)
    plan = serializers.CharField(read_only=True)
    tenant_type = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)

    user_count = serializers.IntegerField(read_only=True)
    project_count = serializers.IntegerField(read_only=True)
    task_count = serializers.IntegerField(read_only=True)

    created_at = serializers.DateTimeField(read_only=True)
    days_active = serializers.IntegerField(read_only=True)

    database_size = serializers.CharField(read_only=True, allow_null=True)
    last_backup = serializers.DateTimeField(read_only=True, allow_null=True)

    # Stats rep
    def to_representation(self, instance):
        from django.utils import timezone

        data = super().to_representation(instance)

        # Calc active days
        if instance.created_at:
            days_active = (timezone.now() - instance.created_at).days
            data['days_active'] = days_active

        return data

# Seperate database premium tenant signup serializer
class PremiumTenantSingupSerializer(serializers.Serializer):
    # Comapany Info
    name = serializers.CharField(
        max_length=100,
        required=True,
        help_text="Name of your company/organization"
    )

    subdomain = serializers.CharField(
        max_length=100,
        read_only=True,
        help_text="Unique subdomain (letters, numbers, hyphens only)"
    )

    plan = serializers.ChoiceField(
        choices=[
            ('professional', 'Professional - Separate Database'),
            ('enterprise', 'Enterprise - Dedicated Database with SLA'),
        ],
        required=True,
        default='professional',
        help_text="Choose your premiujm plan"
    )

    # Required admin info
    admin_email = serializers.EmailField(
        required=True,
        help_text="Admisin user email address"
    )

    admin_password = serializers.CharField(
        max_length=128,
        write_only=True,
        required=True,
        min_length=16,
        help_text="Admin password (min 16 characters)"
    )

    confirm_password = serializers.CharField(
        max_length=128,
        write_only=True,
        required=True,
        help_text="Confirm admin password"
    )

    admin_first_name = serializers.CharField(
        max_length=100,
        required=True,
        help_text="Admin first name"
    )

    admin_last_name = serializers.CharField(
        max_length=100,
        required=True,
        help_text="Admin last name"
    )

    # Billing info
    billing_email = serializers.EmailField(
        required=False,
        allow_blank=True,
        help_text="Billing contact email (default to admin email)"
    )

    # Optional: Custom domain
    custom_domain = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Optional custom domain (e.g., app.yourcompany.com)"
    )

    # terms and conditions
    accept_terms = serializers.BooleanField(
        required=True,
        error_messages={
            'required': 'You must accept the terms and conditions'
        },
        help_text="Accept terms and conditions"
    )

    @staticmethod
    def validate_subdomain(value):
        import re

        value = value.lower()

        # validate length
        if len(value) < 3:
            raise serializers.ValidationError("Subdomain must be at least 3 characters long")

        if len(value) > 63:
            raise serializers.ValidationError("Subdomain cannot exceed 100 characters")

        # Validate allowed characters
        if not re.match(r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?$', value):
            raise serializers.ValidationError(
                "Subdomain can only contain lowercase letters, numbers, and hyphens. "
                "Cannot start or end with a hyphen."
            )

        # validate reserved subdomains
        reserved_subdomains = [
            'www', 'admin', 'api', 'app', 'dashboard', 'mail', 'blog',
            'support', 'help', 'status', 'dev', 'test', 'staging', 'prod'
        ]

        if value in reserved_subdomains:
            raise serializers.ValidationError(f"Subdomain '{value}' is reserved")

        # Validate if subdomain exists
        if Tenant.objects.filter(subdomain=value).exists():
            raise serializers.ValidationError(f"Subdomain '{value}' already taken")

        # Database availibility
        db_name = f"tenant_{value}"

        if Tenant.objects.filter(database_name=db_name).exists():
            raise serializers.ValidationError(f"Database name '{db_name}' already in use. Please choose a different subdomain.")

        if len(db_name) > 63:
            raise serializers.ValidationError(
                f"Subdomain is too long. Database name would be {len(db_name)} characters (max 63)."
            )

        return value

    # Validate passwd strength
    @staticmethod
    def validate_admin_password(value):
        if len(value) < 16:
            raise serializers.ValidationError("Password must be at least 16 characters long")

        # Check complexity
        has_upper = any(c.isupper() for c in value)
        has_lower = any(c.islower() for c in value)
        has_digit = any(c.isdigit() for c in value)

        if not (has_upper and has_lower and has_digit):
            raise serializers.ValidationError(
                "Password must contain at least one uppercase letter, "
                "one lowercase letter, and one number"
            )

        return value

    # Validate password confirmation
    def validate(self, data):
        if data.get('admin_password') != data.get('confirm_password'):
            raise serializers.ValidationError({
                'confirm_password': "Passwords do not match"
            })

        # Set billing email to admin email if not provide
        if not data.get('billin_email') and data.get('admin_email'):
            data['billing_email'] = data['admin_email']

        # Term acceptance validation
        if not data.get('accept_terms'):
            raise serializers.ValidationError({
                'accept_terms': "You must accept the terms and conditions"
            })

        return data

    # Creation will be handled by the view validated data
    def create(self, validated_data):
#       !!!...#########...!!!
        return validated_data