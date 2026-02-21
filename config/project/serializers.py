from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from .models import Project, ProjectMember, ProjectUsage, ProjectAuditLog

User = get_user_model()

class CustomUserMinimalSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='get_full_name', read_only=True)

    class Meta:
        model = User
        fiels = ['id', 'email', 'full_name', 'first_name', 'last_name']
        read_only_fields = ['id', 'email', 'first_name', 'last_name', 'full_name']

class CustomUserDetailSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='get_full_name', read_only=True)
    is_tenant_admin = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'full_name', 'first_name', 'last_name',
            'title', 'department', 'phone', 'is_tenant_admin', 'status'
        ]
        read_only_fields = ['id', 'email', 'full_name', 'first_name', 'last_name']

class ProjectMemberSerializer(serializers.ModelSerializer):
    user = CustomUserMinimalSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=True,
        source='user'
    )

    class Meta:
        model = ProjectMember
        fields = ['id', 'user', 'user_id', 'role', 'invited_at', 'joined_at']
        read_only_fields = ['id', 'invited_at']

class ProjectMemberDetailSerializer(serializers.ModelSerializer):
    user = CustomUserDetailSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=True,
        source='user'
    )

    class Meta:
        model = ProjectMember
        fields = [
            'id', 'user', 'user_id', 'role', 'invited_at', 'joined_at'
        ]
        read_only_fields = ['id', 'invited_at']

class ProjectMemberCreateUpdateSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=True,
        source='user',
        help_text='The user ID to add/update as project member'
    )

    class Meta:
        model = ProjectMember
        fields = ['user_id', 'role']

    # Ensure user belongs to the same tenant as the project
    def validate_user_id(self, value):
        project = self.context.get('project')
        if project and value.tenant != project.tenant:
            raise serializers.ValidationError(
                _('User must belong to the same tenant as the project.')
            )
        return value

    # Prevent duplicate members
    def validate(self, data):
        project = self.context.get('project')
        user = data.get('user')

        if project and user:
            # Check if user is already a member (exclude current instance on update)
            qs = ProjectMember.objects.filter(project=project, user=user)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise serializers.ValidationError(
                    _('This user is already a member of the project.')
                )

        return data

class ProjectUsageSerializer(serializers.ModelSerializer):
    storage_percentage = serializers.SerializerMethodField()
    api_calls_percentage = serializers.SerializerMethodField()
    team_percentage = serializers.SerializerMethodField()

    class Meta:
        model = ProjectUsage
        fields = [
            'id', 'api_calls_used', 'storage_used_gb', 'team_members_count',
            'reset_date', 'updated_at', 'storage_percentage', 'api_calls_percentage',
            'team_percentage'
        ]
        read_only_fields = [
            'id', 'api_calls_used', 'storage_used_gb', 'team_members_count',
            'reset_date', 'updated_at'
        ]

    # Calculate storage usage percentage
    def get_storage_percentage(self, obj):
        if obj.project and obj.project.max_storage_gb > 0:
            return round((obj.storage_used_gb / obj.project.max_storage_gb) * 100, 2)
        return 0

    # Calculate API calls usage percentange
    def get_api_calls_percentage(self, obj):
        if obj.project and obj.project.max_api_calls_monthly > 0:
            return round((obj.api_calls_used / obj.project.max_api_calls_monthly) * 100, 2)
        return 0

    # Calculate team members usage percentage
    def get_team_percentage(self, obj):
        if obj.project and obj.project.max_team_members > 0:
            return round((obj.team_members_count / obj.project.max_team_members) * 100, 2)
        return 0

class ProjectAuditLogSerializer(serializers.ModelSerializer):
    user =  CustomUserMinimalSerializer(read_only=True)
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = ProjectAuditLog
        fields = [
            'id', 'action', 'action_display', 'user', 'details',
            'ip_address', 'timestamp'
        ]
        read_only_fields = fields

class ProjectListSerializer(serializers.ModelSerializer):
    owner = CustomUserMinimalSerializer(read_only=True)
    member_count = serializers.SerializerMethodField()
    usage = ProjectUsageSerializer(
        source='usage_records.first',
        read_only=True,
        help_text='Latest usage statistics'
    )

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'slug', 'owner', 'operational_status',
            'subscription_status', 'member_count', 'usage', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']

    # Get current number of member
    def get_member_count(self, obj):
        return obj.members.count()

class ProjectDetailSerializer(serializers.ModelSerializer):
    owner = CustomUserDetailSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        write_only=True,
        required=False,
        source='owner',
        help_text='User ID of the project owner'
    )
    created_by = CustomUserDetailSerializer(read_only=True)

    members = ProjectMemberSerializer(
        many=True,
        read_only=True,
        source='membership_records'
    )
    usage = serializers.SerializerMethodField()
    recent_audit_logs = serializers.SerializerMethodField()

    # Display values
    operational_status_display = serializers.CharField(
        source='get_operational_status_display',
        read_only=True
    )
    subscription_status_display = serializers.CharField(
        source='get_subscription_status_display',
        read_only=True
    )

    # helpers
    can_add_member = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            'id', 'tenant', 'name', 'slug', 'description',
            'owner', 'owner_id', 'created_by',
            'operational_status', 'operational_status_display',
            'subscription_status', 'subscription_status_display',
            'max_team_members', 'max_storage_gb', 'max_api_calls_monthly',
            'trial_ends_at',
            'members', 'usage', 'recent_audit_logs',
            'can_add_member', 'member_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'tenant', 'slug', 'created_by',
            'created_at', 'updated_at', 'members', 'usage', 'recent_audit_logs',
            'can_add_member', 'member_count'
        ]

    # Get latest usage statistics
    def get_usage(self, obj):
        usage = obj.usage_records.order_by('-updated_at').first()
        if usage:
            return ProjectUsageSerializer(usage).data
        return None

    # Get last 10 audit log entries
    def get_recent_audit_logs(self, obj):
        logs = obj.audit_logs.all()[:10]
        return ProjectAuditLogSerializer(logs, many=True).data

    # Check if project can accept new members
    def get_can_add_member(self, obj):
        return obj.can_ass_team_member()

    # Get current number of members
    def get_member_count(self, obj):
        return obj.members.count()

# Serializers for creating new projects
class ProjectCreateSerializer(serializers.ModelSerializer):
    owner_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        write_only=True,
        source='owner',
        help_text='User ID of the project owner (defaults to current user)'
    )

    class Meta:
        model = Project
        fields = [
            'name', 'description', 'owner_id',
            'max_team_members', 'max_storage_gb', 'max_api_calls_monthly',
            'trial_ends_at'
        ]

    # Validate project name
    def validate_name(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError(_('Project name cannot be empty.'))

        if len(value) > 100:
            raise serializers.ValidationError(
                _('Project name cannot exceed 100 characters.')
            )

        return value

    # Validate team member limit
    def validate_max_team_members(self, value):
        if value < 1:
            raise serializers.ValidationError(
                _('Maximum team members must be at least 1.')
            )
        return value

    # Validate storage limit
    def validate_max_storage_gb(self, value):
        if value < 1:
            raise serializers.ValidationError(
                _('Maximum storage must be at least 1 GB.')
            )
        return value

    # Validate API call limit
    def validate_max_api_calls_monthly(self, value):
        if value < 100:
            raise serializers.ValidationError(
                _('Maximum API calls must be at least 100.')
            )
        return value

    # Validate trial end date
    def validate_trial_ends_at(self, value):
        from django.utils import timezone
        if value and value <= timezone.now():
            raise serializers.ValidationError(
                _('Trial end date must be in the future.')
            )
        return value

    # Create project with current user as creator if no owner specified
    def create(self, validated_data):
        request = self.context.get('request')
        tenant = self.context.get('tenant') or request.user.tenant

        # Set default owner to current user if not provided
        if 'owner' not in validated_data or validated_data['owner'] is None:
            validated_data['owner'] = request.user

        # Set created_by to current user
        validated_data['created_by'] = request.user

        # Set tenant
        validated_data['tenant'] = tenant

        return super().create(validated_data)

# Serializer for updating projects
class ProjectUpdateSerializer(serializers.ModelSerializer):
    owner_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        write_only=True,
        source='owner',
        help_text='User ID of the project owner'
    )

    class Meta:
        model = Project
        fields = [
            'name', 'description', 'owner_id',
            'operational_status', 'subscription_status',
            'max_team_members', 'max_storage_gb', 'max_api_calls_monthly',
            'trial_ends_at'
        ]

    # Validate project name
    def validate_name(self, value):
        if not value or len(value.strip()) == 0:
            raise serializers.ValidationError(_('Project name cannot be empty.'))

        if len(value) > 100:
            raise serializers.ValidationError(
                _('Project name cannot exceed 100 characters.')
            )

        return value

    # Validate team member limit
    def validate_max_team_members(self, value):
        if value < 1:
            raise serializers.ValidationError(
                _('Maximum team members must be at least 1.')
            )

        # Check current member count doesn't exceed new limit
        current_count = self.instance.members.count()
        if current_count > value:
            raise serializers.ValidationError(
                _(f'Cannot reduce team limit below current members ({current_count}).')
            )

        return value

    # Validate storage limit
    def validate_max_storage_gb(self, value):
        if value < 1:
            raise serializers.ValidationError(
                _('Maximum storage must be at least 1 GB.')
            )
        return value

    # Validate API call limit
    def validate_max_api_calls_monthly(self, value):
        if value < 100:
            raise serializers.ValidationError(
                _('Maximum API calls must be at least 100.')
            )
        return value

    # Update project
    def update(self, instance, validated_data):
        # Prevent tenant modification
        validated_data.pop('tenant', None)
        return super().update(instance, validated_data)

# Serializer for partial updates(PATCH)
class ProjectPartialUpdateSerializer(ProjectUpdateSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make all fields optional for PATCH
        for field in self.fields.values():
            field.required = False

# Extended serializer with admin-only fields
class ProjectAdminSerializer(ProjectDetailSerializer):
    all_members = ProjectMemberDetailSerializer(
        many=True,
        read_only=True,
        source='membership_records'
    )
    all_audit_logs = ProjectAuditLogSerializer(
        many=True,
        read_only=True,
        source='audit_logs'
    )
    usage_history = serializers.SerializerMethodField()

    class Meta(ProjectDetailSerializer.Meta):
        fields = ProjectDetailSerializer.Meta.fields + [
            'all_members', 'all_audit_logs', 'usage_history'
        ]
        read_only_fields = ProjectDetailSerializer.Meta.read_only_fields + [
            'all_members', 'all_audit_logs', 'usage_history'
        ]

    # Get usage history for last 12 months
    def get_usage_history(self, obj):
        from django.utils import timezone
        from datetime import timedelta

        cutoff_date = timezone.now().date() - timedelta(days=365)
        usage_records = obj.usage_records.filter(reset_date__gte=cutoff_date).order_by('reset_date')
        return ProjectUsageSerializer(usage_records, many=True).data