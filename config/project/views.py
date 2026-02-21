from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .models import Project, ProjectMember, ProjectUsage, ProjectAuditLog
from .serializers import (
    ProjectListSerializer, ProjectDetailSerializer, ProjectCreateSerializer,
    ProjectUpdateSerializer, ProjectPartialUpdateSerializer, ProjectAdminSerializer,
    ProjectMemberDetailSerializer, ProjectMemberCreateUpdateSerializer,
    ProjectUsageSerializer, ProjectAuditLogSerializer
)


# Create your views here.

# Check if user is a member of the project
class IsProjectMember(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        # Owner and admins always have access
        if request.user.is_tenant_admin:
            return True

        # Check if user is a member of the project
        return obj.members.filter(pk=request.user.pk).exists()

# Check if user is project owner
class IsProjectOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.owner_id == request.user.id

# Check if user can manage the project (owner or admin)
class CanManageProject(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        # owner can always manage
        if obj.owner_id == request.user.id:
            return True

        # Check if user is member with admin role
        member = obj.membership_records.filter(user=request.user).first()
        if member and member.role in ['owner', 'admin']:
            return True

        return False

# Ensure user belongs to the same tenant
class IsTenantUser(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user.tenant_id == obj.tenant_id

# Automatically filter user's tenant
class TenantFilterMixin:
    def get_queryset(self):
        # get base queryset
        queryset = super().get_queryset()

        # Filter by user's tenant
        return queryset.filter(tenant=self.request.user.tenant)

# Base viewset for projects with proper filtering and permissions
class BaseProjectViewSet(TenantFilterMixin, viewsets.ModelViewSet):
    permission_classes = [
        permissions.IsAuthenticated,
        IsTenantUser
    ]

    # Add request and tenant to serializer context
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        context['tenant'] = self.request.user.tenant
        return context

# Full CRUD operations for projects
class ProjectViewSet(BaseProjectViewSet):
    def get_queryset(self):
        #Filter projects by tenant
        queryset = super().get_queryset()

        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(operational_status=status_filter)

        # Filter by subscription status if provided
        subscription_status = self.request.query_params.get('subscription_status')
        if subscription_status:
            queryset = queryset.filter(subscription_status=subscription_status)

        # Filter by owner if provided
        owner_id = self.request.query_params.get('owner_id')
        if owner_id:
            queryset = queryset.filter(owner_id=owner_id)

        return queryset.select_related('owner', 'created_by', 'tenant').prefetch_related('members')

    def get_serializer_class(self):
        #Use different serializers for different actions
        if self.action == 'list':
            return ProjectListSerializer
        elif self.action == 'create':
            return ProjectCreateSerializer
        elif self.action in ['update']:
            return ProjectUpdateSerializer
        elif self.action == 'partial_update':
            return ProjectPartialUpdateSerializer
        elif self.action in ['admin_view']:
            return ProjectAdminSerializer
        else:  # retrieve
            return ProjectDetailSerializer

    def get_permissions(self):
        #Adjust permissions based on action
        if self.action == 'create':
            return [permissions.IsAuthenticated()]
        elif self.action in ['update', 'partial_update', 'destroy']:
            return [
                permissions.IsAuthenticated(),
                IsTenantUser(),
                CanManageProject()
            ]
        elif self.action in ['retrieve', 'list']:
            return [
                permissions.IsAuthenticated(),
                IsTenantUser()
            ]
        else:
            return [
                permissions.IsAuthenticated(),
                CanManageProject()
            ]

    # Create project with proper context
    def perform_create(self, serializer):
        serializer.save(tenant=self.request.user.tenant)

        # Log the action
        ProjectAuditLog.objects.create(
            tenant=self.request.user.tenant,
            project=serializer.instance,
            user=self.request.user,
            action='created',
            details={'name': serializer.instance.name},
            ip_address=self.get_client_ip(),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )

    # Update project and log changes
    def perform_update(self, serializer):
        serializer.save()

        # Log the action
        ProjectAuditLog.objects.create(
            tenant=self.request.user.tenant,
            project=serializer.instance,
            user=self.request.user,
            action='updated',
            details={'updated_fields': serializer.validated_data.keys()},
            ip_address=self.get_client_ip(),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )

    # Archive instead of delete
    def perform_destroy(self, instance):
        instance.operational_status = 'deleted'
        instance.save()

        # Log the deletion
        ProjectAuditLog.objects.create(
            tenant=self.request.user.tenant,
            project=instance,
            user=self.request.user,
            action='deleted',
            details={'name': instance.name},
            ip_address=self.get_client_ip(),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )

    @action(detail=True, methods=['post'])
    # Archive a project
    def archive(self, request, pk=None):
        project = self.get_object()
        self.check_object_permissions(request, project)

        project.operational_status = 'archived'
        project.save()

        ProjectAuditLog.objects.create(
            tenant=request.user.tenant,
            project=project,
            user=request.user,
            action='archived',
            ip_address=self.get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

        return Response(
            {'detail': _('Project archived successfully.')},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    # Restore an archived project
    def restore(self, request, pk=None):
        project = self.get_object()
        self.check_object_permissions(request, project)

        project.operational_status = 'active'
        project.save()

        ProjectAuditLog.objects.create(
            tenant=request.user.tenant,
            project=project,
            user=request.user,
            action='restored',
            ip_address=self.get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

        return Response(
            {'detail': _('Project restored successfully.')},
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['get'])
    # Get all members of a project
    def members(self, request, pk=None):
        project = self.get_object()
        members = project.membership_records.all()
        serializer = ProjectMemberDetailSerializer(members, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    # Get usage statistics for a project
    def usage(self, request, pk=None):
        project = self.get_object()
        usage = project.usage_records.order_by('-updated_at').first()

        if usage:
            serializer = ProjectUsageSerializer(usage)
            return Response(serializer.data)

        return Response(
            {'detail': _('No usage data available.')},
            status=status.HTTP_404_NOT_FOUND
        )

    @action(detail=True, methods=['get'])
    #Get audit logs for a project
    def audit_logs(self, request, pk=None):
        project = self.get_object()
        logs = project.audit_logs.all()[:50]
        serializer = ProjectAuditLogSerializer(logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    # Admin view with extended details
    def admin_view(self, request, pk=None):
        if not request.user.is_tenant_admin:
            raise PermissionDenied(_('Only administrators can access this view.'))

        project = self.get_object()
        serializer = ProjectAdminSerializer(project)
        return Response(serializer.data)

    # Get client IP address from request
    def get_client_ip(self):
        x_forwarded_for = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = self.request.META.get('REMOTE_ADDR')
        return ip

class ProjectMemberViewSet(TenantFilterMixin, viewsets.ViewSet):
    permission_classes = [
        permissions.IsAuthenticated,
        CanManageProject
    ]

    # Get the project from URL kwargs
    def get_project(self):
        project_id = self.kwargs.get('project_pk')
        return get_object_or_404(
            Project,
            pk=project_id,
            tenant=self.request.user.tenant
        )

    # List all members of a project
    def list(self, request, project_pk=None):
        project = self.get_project()
        self.check_object_permissions(request, project)

        members = project.membership_records.all()
        serializer = ProjectMemberDetailSerializer(members, many=True)
        return Response(serializer.data)

    # Get a specific project member
    def retrieve(self, request, project_pk=None, pk=None):
        project = self.get_project()
        self.check_object_permissions(request, project)

        member = get_object_or_404(ProjectMember, pk=pk, project=project)
        serializer = ProjectMemberDetailSerializer(member)
        return Response(serializer.data)

    # Add a member to a project
    def create(self, request, project_pk=None):
        project = self.get_project()
        self.check_object_permissions(request, project)

        # Check if project can accept new members
        if not project.can_add_team_member():
            return Response(
                {'detail': _('Project has reached maximum team members limit.')},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ProjectMemberCreateUpdateSerializer(
            data=request.data,
            context={'project': project}
        )

        if serializer.is_valid():
            member = serializer.save(project=project)

            # Log the action
            ProjectAuditLog.objects.create(
                tenant=request.user.tenant,
                project=project,
                user=request.user,
                action='member_added',
                details={'member_email': member.user.email, 'role': member.role},
                ip_address=self.get_client_ip(request)
            )

            return Response(
                ProjectMemberDetailSerializer(member).data,
                status=status.HTTP_201_CREATED
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # Update a project member's role
    def update(self, request, project_pk=None, pk=None):
        project = self.get_project()
        self.check_object_permissions(request, project)

        member = get_object_or_404(ProjectMember, pk=pk, project=project)
        serializer = ProjectMemberCreateUpdateSerializer(
            member,
            data=request.data,
            partial=True,
            context={'project': project}
        )

        if serializer.is_valid():
            old_role = member.role
            member = serializer.save()

            # Log the action
            ProjectAuditLog.objects.create(
                tenant=request.user.tenant,
                project=project,
                user=request.user,
                action='member_role_changed',
                details={
                    'member_email': member.user.email,
                    'old_role': old_role,
                    'new_role': member.role
                },
                ip_address=self.get_client_ip(request)
            )

            return Response(ProjectMemberDetailSerializer(member).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # Remove a member from a project
    def destroy(self, request, project_pk=None, pk=None):
        project = self.get_project()
        self.check_object_permissions(request, project)

        member = get_object_or_404(ProjectMember, pk=pk, project=project)
        member_email = member.user.email
        member.delete()

        # Log the action
        ProjectAuditLog.objects.create(
            tenant=request.user.tenant,
            project=project,
            user=request.user,
            action='member_removed',
            details={'member_email': member_email},
            ip_address=self.get_client_ip(request)
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    # Get client IP address from request
    def get_client_ip(self):
        x_forwarded_for = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = self.request.META.get('REMOTE_ADDR')
        return ip

    ipaddress=get_client_ip()

class ProjectUsageViewSet(TenantFilterMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectUsageSerializer
    permission_classes = [
        permissions.IsAuthenticated,
        IsProjectMember
    ]

    # Filter usage by project and tenant
    def get_queryset(self):
        project_id = self.kwargs.get('project_pk')
        project = get_object_or_404(
            Project,
            pk=project_id,
            tenant=self.request.user.tenant
        )
        return project.usage_records.all().order_by('-reset_date')