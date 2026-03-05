from enum import member
from random import choices

from django.db.models.functions import Coalesce
from django_filters import rest_framework as filters
from django.db.models import Q, Count, F, Sum, Case, When, Value, ExpressionWrapper, FloatField
from django.utils import timezone
from datetime import timedelta
from .models import Project, ProjectMember, ProjectUsage, ProjectAuditLog

class CharInFilter(filters.BaseInFilter, filters.CharFilter):
    pass

class NumberInFilter(filters.BaseInFilter, filters.CharFilter):
    pass

class ProjectFilterSet(filters.FilterSet):
    # Search
    search = filters.CharFilter(
        method='filter_search',
        label='Search by name or description'
    )

    # Status filters
    operation_status = filters.ChoiceFilter(
        choices=Project.OPERATIONAL_STATUS_CHOICES,
        label='Operational Status'
    )

    subscription_status = filters.ChoiceFilter(
        choices=Project.SUBSCRIPTION_STATUS_CHOICES,
        label='Subscription Status'
    )

    # Owner filters
    owner_id = filters.UUIDFilter(
        field_name='owner__id',
        label='Owner ID'
    )

    owner_email = filters.CharFilter(
        field_name='owner__email',
        lookup_expr='icontains',
        label='Owner Email'
    )

    # tenant filter
    tenant_id = filters.UUIDFilter(
        field_name='tenant__id',
        label='Tenant ID'
    )

    # Resource limits
    min_max_team_members = filters.NumberFilter(
        field_name='max_team_members',
        lookup_expr='gte',
        label='Min Max Team Members'
    )

    max_max_team_members = filters.NumberFilter(
        field_name='max_team_members',
        lookup_expr='lte',
        label='Max Max Team Members'
    )

    min_max_storage_gb = filters.NumberFilter(
        field_name='max_storage_gb',
        lookup_expr='gte',
        label='Max Storage (GB)'
    )

    max_max_storage_gb = filters.NumberFilter(
        field_name='max_storage_gb',
        lookup_expr='lte',
        label='Max Storage (GB)'
    )

    # Date filters
    created_after = filters.DateTimeFilter(
        field_name='created_at',
        lookup_expr='gte',
        label='Created After'
    )

    created_before = filters.DateTimeFilter(
        field_name='created_at',
        lookup_expr='lte',
        label='Created Before'
    )

    trial_ends_after = filters.DateTimeFilter(
        field_name='trial_ends_at',
        lookup_expr='gte',
        label='Trial Ends Before'
    )

    trial_ends_before = filters.DateTimeFilter(
        field_name='trial_ends_at',
        lookup_expr='lte',
        label='Trial Ends Before'
    )

    # Custom filters
    trial_ending_soon = filters.BooleanFilter(
        method='filter_trial_ending_soon',
        label='Trial Ending Soon (< 7 days)'
    )

    high_usage = filters.BooleanFilter(
        method='filter_high_usage',
        label='High Usage (> 80%)'
    )

    has_members = filters.BooleanFilter(
        method='filter_has_members',
        label='Has Team Members'
    )

    member_count_min = filters.NumberFilter(
        method='filter_member_count_min',
        label='Min Team Members'
    )

    member_count_max = filters.NumberFilter(
        method='filter_member_count_max',
        label='Max Team Members'
    )

    # Ordering
    ordering = filters.OrderingFilter(
        fields=(
            ('name', 'name'),
            ('created_at', 'created_at'),
            ('updated_at', 'updated_at'),
            ('subscription_status', 'subscription_status'),
        ),
        label='Ordering'
    )

    class Meta:
        model = Project
        fields = [
            'operational_status',
            'subscription_status',
            'tenant_id',
            'owner_id'
        ]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(slug__icontains=value)
        )

    def filter_trial_ending_soon(self, queryset, name, value):
        if value:
            soon = timezone.now() + timedelta(days=7)
            return queryset.filter(
                trial_ends_at__lte=soon,
                trial_ends_at__gt=timezone.now(),
                subscription_status='trial'
            )
        return queryset

    def filter_high_usage(self, queryset, name, value):
        if not value:
            return queryset

        try:
            # Get current month start for usage tracking
            month_start = timezone.now().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            # Annotate with usage calculations
            queryset = queryset.annotate(

                # Get latest usage or claculate form files
                storage_used_gb=Coalesce(
                    # Option 1
                    F('usage__storage_used_gb'),
                    # option 2
                    Sum('files__size') / (1024 * 1024 * 1024),
                    Value(0.0),
                    output_field=FloatField()
                ),

                # Count active members
                members_count=Coalesce(
                    Count(
                        'members',
                        filter=Q(members__status='active'),
                        distinct=True
                    ),
                    value(0),
                    output_field=FloatField()
                ),

                # Tracked monthly api calls
                api_calls_month=Coalesce(
                    F('usage__api_calls_month'),
                    Count(
                        'api_requests',
                        filter=Q(api_requests__created_at__gte=month_start)
                    ),
                    Value(0),
                    output_field=FloatField()
                ),

                # Tracked database redords
                records_count=Coalesce(
                    F('usage__records_count'),
                    Count('records', distinct=True),
                    Value(0),
                    output_field=FloatField()
                ),

                # Limits

                storage_limit=Coalesce(
                    F('max_storage_gb'),
                    Value(10.0), # Default
                    output_field=FloatField()
                ),

                member_limit=Coalesce(
                    F('max_team_members'),
                    Value(5.0),
                    output_field=FloatField()
                ),

                api_limit=Coalesce(
                    F('max_api_calls'),
                    Value(10000.0), # Default 10k calls pm
                    output_field=FloatField()
                ),

                records_limit=Coalesce(
                    F('max_records'),
                    Value(10000.0), # Default 10k records
                    output_field=FloatField()
                ),
            ).annotate(
                # Usage percentages

                storage_pct=Case(
                    When(
                        storage_limit__gt=0,
                        then=ExpressionWrapper(
                            (F('storage_used_gb') / F('storage_limit')) * 100,
                            output_field=FloatField()
                        )
                    ),
                    default=Value(0.0),
                    output_field=FloatField()
                ),

                members_pct=Case(
                    When(
                        members_limit__gb=0,
                        then=ExpressionWrapper(
                            (F('members_count') / F('members_limit')) * 100,
                            output_field=FloatField()
                        )
                    ),
                    default=Value(0.0),
                    output_field=FloatField()
                ),

                api_pct=Case(
                    When(
                        api_limit__gt=0,
                        then=ExpressionWrapper(
                            F(('api_calls_month') / F('api_limit')) * 100,
                            output_field=FloatField()
                        )
                    ),
                    default=Value(0.0),
                    output_field=FloatField()
                ),

                records_pct=Case(
                    When(
                        records_limit__gt=0,
                        then=ExpressionWrapper(
                            (F('records_count') / F('records_limit')) * 100,
                            output_field=FloatField()
                        )
                    ),
                    default=Value(0.0),
                    output_field=FloatField()
                ),
            ).annotate(
                # Flag resources exceeding 80%
                is_high_usage=Case(
                    When(
                        Q(storage_pct__gt=80) |
                        Q(members_pct__gt=80) |
                        Q(api_pct__gt=80) |
                        Q(records_pct__gt=80),
                        then=Value(True)
                    ),
                    default=Value(False),
                    output_field=filters.BooleanField()
                )
            )

            # Filter for high usage and active projects
            return queryset.filter(
                is_high_usage=True,
                subscription_status__in=['active', 'trial']
            )

        except Exception as e:
            logger.error(f"Error filtering high usage projects: {str(e)}")
            return queryset.none()

    # Filter projects that have team member
    def filter_has_member(self, queryset, name, value):

        if value:
            return queryset.annotate(
                members_count=Count('members', filter=Q(members__status='active'))
            ).filter(member_count__gt=0)
        else:
            return queryset.annotate(
                member_count=Count('members', filter=Q(members__status='active'))
            ).filter(member_counts=0)

    # Filter projects with at least N team members
    def filter_member_count_min(self, queryset, name, value):
        return queryset.annotate(
            member_count=Count('members', filter=Q(member__status='active'))
        ).filter(member_count__gte=value)

    # Filter projects with at least N team members
    def filter_member_count(self, queryset, name, value):
        return queryset.annotate(
            member_count=Count('members', filter=Q(member__status='active'))
        ).filter(member_count__lte=value)

# Advanced filtering for project members
class ProjectMemberFilterSet(filters.FilterSet):
    # Search
    search = filters.CharFilter(
        method='filter_search',
        label='Search by user eamil or name'
    )

    # Role filter
    role = filters.ChoiceFilter(
        choices=ProjectMember.ROLE_CHOICES,
        label='Role'
    )

    # User filters
    user_email = filters.CharFilter(
        field_name='user__email',
        lookup_expr='icontains',
        label='user Email'
    )

    user_id = filters.UUIDFilter(
        field_name='user__id',
        label='User ID'
    )

    # project filters
    project_id = filters.UUIDFilter(
        field_name='project__id',
        label='Project ID'
    )

    project_name = filters.CharFilter(
        field_name='project__name',
        lookup_expr='icontains',
        label='Project name'
    )

    # Date filters
    invited_after = filters.DateTimeFilter(
        field_name='invited_at',
        lookup_expr='gte',
        label='Invited After'
    )

    invited_before = filters.DateTimeFilter(
        field_name='invited_at',
        lookup_expr='lte',
        label='Invited Before'
    )

    joined_after = filters.DateTimeFilter(
        field_name='joined_at',
        lookup_expr='gte',
        label='Joined After'
    )

    joined_before = filters.DateTimeFilter(
        field_name='joined_at',
        lookup_expr='lte',
        label='Joined Before'
    )

    # Custom filters
    has_joined = filters.BooleanFilter(
        method='filter_has_joined',
        label='Has Joined'
    )

    pending_invitation = filters.BooleanFilter(
        method='filter_pending_invitation',
        label='Pending Invitation'
    )

    # Ordering
    ordering = filters.OrderingFilter(
        fields=(
            ('user__email', 'user_email'),
            ('role', 'role'),
            ('invited_at', 'invited_at'),
            ('joined_at', 'joined_at'),
        ),
        label='Ordering'
    )

    class Meta:
        model = ProjectMember
        fields = ['role', 'project_id', 'user_id']

    # Search in user email and name
    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(user__email__icontains=value) |
            Q(user__first_name__icontains=value) |
            Q(user__last_name__icontains=value)
        )

    # filter by join status
    def filter_has_joined(self, queryset, name, value):
        if value:
            return queryset.filter(joined_at__isnull=False)
        else:
            return queryset.filter(joined_at__isnull=True)

    # Filter pending invitations
    def filter_pending_invitation(self, queryset, name, value):
        if value:
            return queryset.filter(joined_at__isnull=True)
        return queryset

# Advanced filtering for project usage
class ProjectUsageFilterSet(filters.FilterSet):
    # Project filters
    project_id = filters.UUIDFilter(
        field_name='project__id',
        label='Project ID'
    )

    project_name = filters.CharFilter(
        field_name='project__name',
        lookup_expr='icontains',
        label='Project Name'
    )

    # API calls filters
    min_api_calls = filters.NumberFilter(
        field_name='api_calls_used',
        lookup_expr='gte',
        label='Min API Calls'
    )

    max_api_calls = filters.NumberFilter(
        field_name='api_calls_used',
        lookup_expr='lte',
        label='Max API Calls'
    )

    # Storage filters
    min_storage_gb = filters.NumberFilter(
        field_name='storage_used_gb',
        lookup_expr='gte',
        label='Min Storage (GB)'
    )

    max_storage_gb = filters.NumberFilter(
        field_name='storage_used_gb',
        lookup_expr='lte',
        label='Max Storage (GB)'
    )

    # Team members filters
    min_team_members = filters.NumberFilter(
        field_name='team_members_count',
        lookup_expr='gte',
        label='Min Team Members'
    )

    max_team_members = filters.NumberFilter(
        field_name='team_members_count',
        lookup_expr='lte',
        label='Max Team Members'
    )

    # Date filters
    reset_date_after = filters.DateFilter(
        field_name='reset_date',
        lookup_expr='gte',
        label='Reset Date After'
    )

    reset_date_before = filters.DateFilter(
        field_name='reset_date',
        lookup_expr='lte',
        label='Reset Date Before'
    )

    # Custom filters
    high_api_usage = filters.BooleanFilter(
        method='filter_high_api_usage',
        label='High API Usage'
    )

    high_storage_usage = filters.BooleanFilter(
        method='filter_high_storage_usage',
        label='High Storage Usage'
    )

    # Ordering
    ordering = filters.OrderingFilter(
        fields=(
            ('project__name', 'project_name'),
            ('reset_date', 'reset_date'),
            ('api_calls_used', 'api_calls'),
            ('storage_used_gb', 'storage'),
            ('team_members_count', 'team_members'),
            ('updated_at', 'updated_at'),
        ),
        label='Ordering'
    )

    class Meta:
        model = ProjectUsage
        fields = ['project_id']

    # filter high api usage
    def filter_high_api_usage(self, queryset, name, value):
        if value:
            return queryset.filter(
                api_calls_used__gt=F('project__max_api_calls_monthly') * 0.8
            )
        return queryset

    # filter high storage usage
    def filter_high_storage_usage(self, queryset, name, value):
        if value:
            return queryset.filter(
                storage_used_gb__gt=F('project__max_storage_gb') * 0.8
            )
        return queryset

# Advanced filtering for audit logs
class ProjectAuditLogFilterSet(filters.FilterSet):
    # Search
    search = filters.CharFilter(
        method='filter_search',
        label='Search by project name or user email'
    )

    # Action filters
    action = filters.ChoiceFilter(
        choices=ProjectAuditLog.ACTION_CHOICES,
        label='Action'
    )

    action_in = CharInFilter(
        field_name='action',
        label='Actions (comma-separated)'
    )

    # Project filters
    project_id = filters.UUIDFilter(
        field_name='project__id',
        label='Project ID'
    )

    project_name = filters.CharFilter(
        field_name='project__name',
        lookup_expr='icontains',
        label='Project Name'
    )

    # User filters
    user_email = filters.CharFilter(
        field_name='user__email',
        lookup_expr='icontains',
        label='User Email'
    )

    user_id = filters.UUIDFilter(
        field_name='user__id',
        label='User ID'
    )

    # IP address filter
    ip_address = filters.CharFilter(
        field_name='ip_address',
        lookup_expr='exact',
        label='IP Address'
    )

    # Date filters
    timestamp_after = filters.DateTimeFilter(
        field_name='timestamp',
        lookup_expr='gte',
        label='After'
    )

    timestamp_before = filters.DateTimeFilter(
        field_name='timestamp',
        lookup_expr='lte',
        label='Before'
    )

    # Tenant filter
    tenant_id = filters.UUIDFilter(
        field_name='tenant__id',
        label='Tenant ID'
    )

    # Custom filters
    recent_actions = filters.BooleanFilter(
        method='filter_recent_actions',
        label='Recent Actions (last 24h)'
    )

    critical_actions = filters.BooleanFilter(
        method='filter_critical_actions',
        label='Critical Actions (delete/restore)'
    )

    # Ordering
    ordering = filters.OrderingFilter(
        fields=(
            ('-timestamp', 'recent_first'),
            ('timestamp', 'oldest_first'),
            ('action', 'action'),
            ('project__name', 'project'),
            ('user__email', 'user'),
        ),
        label='Ordering'
    )

    class Meta:
        model = ProjectAuditLog
        fields = ['action', 'project_id', 'user_id', 'tenant_id']

    # Search in project name and user email
    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(project__name__icontains=value) |
            Q(user__email__icontains=value)
        )

    # Filter actions from last 24 hours
    def filter_recent_actions(self, queryset, name, value):
        if value:
            last_24h = timezone.now() - timedelta(hours=24)
            return queryset.filter(timestamp__gte=last_24h)
        return queryset

    # filter critical actions
    def filter_critical_action(self, queryset, name, value):
        if value:
            return queryset.filter(
                action__in=['deleted', 'restored', 'member_removed']
            )
        return queryset