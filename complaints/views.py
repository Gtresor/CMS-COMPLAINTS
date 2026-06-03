from collections import defaultdict
from datetime import datetime, timedelta

from django.db.models import Avg, Count, ExpressionWrapper, F, Q, DurationField
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.template.response import TemplateResponse
from django.utils import timezone
from django.views.generic import TemplateView, RedirectView
from django.views import View
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.urls import reverse
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Attachment, Category, Complaint, ComplaintAction, ComplaintNote, Department, Notification, SystemConfiguration, User
from .permissions import AttachmentPermission, ComplaintNotePermission, ComplaintPermission
from .serializers import (
    AttachmentSerializer,
    AttachmentUploadSerializer,
    ComplaintNoteSerializer,
    ComplaintSerializer,
    NotificationSerializer,
)


class ComplaintViewSet(viewsets.ModelViewSet):
    queryset = Complaint.objects.all()
    serializer_class = ComplaintSerializer
    permission_classes = [permissions.IsAuthenticated, ComplaintPermission]

    @action(detail=True, methods=['post'], url_path='attachments', permission_classes=[permissions.IsAuthenticated, AttachmentPermission])
    def attachments(self, request, pk=None):
        complaint = self.get_object()
        files = request.FILES.getlist('files')
        serializer = AttachmentUploadSerializer(data={'files': files})
        serializer.is_valid(raise_exception=True)

        attachments = []
        for upload in serializer.validated_data['files']:
            attachment = Attachment.objects.create(
                complaint=complaint,
                file=upload,
                file_type=upload.content_type or '',
            )
            attachments.append(attachment)

        output_serializer = AttachmentSerializer(attachments, many=True, context={'request': request})
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def get_queryset(self):
        user = self.request.user
        if user.role in {'admin', 'reviewer'}:
            return Complaint.objects.all()
        if user.role == 'handler':
            return Complaint.objects.filter(assigned_to=user)
        return Complaint.objects.none()

    def perform_create(self, serializer):
        complaint = serializer.save(registered_by=self.request.user)
        # Trigger notification for API-based complaint creation
        from .services import notify_complaint_created, notify_complaint_assigned
        notify_complaint_created(complaint, performed_by=self.request.user)
        if complaint.assigned_to:
            notify_complaint_assigned(complaint, assigned_by=self.request.user)

    def update(self, request, *args, **kwargs):
        return self._handle_update(request, super().update, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return self._handle_update(request, super().partial_update, *args, **kwargs)

    def _handle_update(self, request, update_method, *args, **kwargs):
        user = request.user
        if user.role == 'reviewer':
            allowed = {'assigned_to', 'assigned_by', 'date_assigned', 'sla_days', 'sla_mode', 'status'}
            provided = set(request.data.keys())
            if provided - allowed:
                raise PermissionDenied('Reviewers may only update assignment and status fields.')
            if 'status' in request.data and request.data['status'] not in {'under_review', 'assigned'}:
                raise PermissionDenied('Reviewers may only set status to under_review or assigned.')
        elif user.role == 'handler':
            provided = set(request.data.keys())
            if provided - {'status'}:
                raise PermissionDenied('Handlers may only update complaint status.')
            if 'status' not in request.data:
                raise PermissionDenied('Handlers must update the status field.')
            if request.data['status'] not in {'in_progress', 'pending_info', 'resolved'}:
                raise PermissionDenied('Handlers may only set status to in_progress, pending_info, or resolved.')
        elif user.role == 'registrar':
            # Registrars can update like admins (full access)
            pass
        return update_method(request, *args, **kwargs)

    def perform_update(self, serializer):
        # Detect changes before saving
        changes = []
        if serializer.instance:
            old_status = serializer.instance.status
            old_assigned_to = serializer.instance.assigned_to
        else:
            old_status = None
            old_assigned_to = None
        
        if self.request.user.role == 'reviewer' and 'assigned_to' in self.request.data and 'assigned_by' not in self.request.data:
            complaint = serializer.save(assigned_by=self.request.user)
        else:
            complaint = serializer.save()
        
        # Determine what changed
        if 'status' in self.request.data and self.request.data['status'] != old_status:
            changes.append(f"status: {old_status} → {complaint.get_status_display()}")
        if 'assigned_to' in self.request.data:
            assigned_user_id = self.request.data.get('assigned_to')
            if assigned_user_id:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                try:
                    new_handler = User.objects.get(pk=assigned_user_id)
                    changes.append(f"assigned to: {new_handler.get_full_name() or new_handler.username}")
                except User.DoesNotExist:
                    pass
        
        # Trigger notifications
        from .services import notify_complaint_updated, notify_status_update, notify_complaint_assigned
        
        if changes:
            notify_complaint_updated(complaint, self.request.user, changes=changes)
        
        if 'status' in self.request.data and self.request.data['status'] != old_status:
            notify_status_update(complaint, self.request.user)
        
        if 'assigned_to' in self.request.data and complaint.assigned_to and complaint.assigned_to != old_assigned_to:
            notify_complaint_assigned(complaint, assigned_by=self.request.user)


class AttachmentViewSet(viewsets.ModelViewSet):
    queryset = Attachment.objects.all()
    serializer_class = AttachmentSerializer
    permission_classes = [permissions.IsAuthenticated, AttachmentPermission]

    def get_queryset(self):
        if self.request.user.role == 'admin':
            return Attachment.objects.all()
        return Attachment.objects.none()


class ComplaintNoteViewSet(viewsets.ModelViewSet):
    queryset = ComplaintNote.objects.all()
    serializer_class = ComplaintNoteSerializer
    permission_classes = [permissions.IsAuthenticated, ComplaintNotePermission]

    def get_queryset(self):
        user = self.request.user
        if user.role == 'admin':
            return ComplaintNote.objects.all()
        if user.role == 'handler':
            return ComplaintNote.objects.filter(complaint__assigned_to=user)
        return ComplaintNote.objects.none()

    def perform_create(self, serializer):
        complaint = serializer.validated_data.get('complaint')
        if self.request.user.role == 'handler' and complaint.assigned_to_id != self.request.user.id:
            raise PermissionDenied('Handlers may only add notes to complaints assigned to them.')
        serializer.save(author=self.request.user)


class DashboardStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in {'admin', 'reviewer'}:
            return Response({'detail': 'Access denied.'}, status=403)

        today = timezone.localdate()
        complaints = Complaint.objects.all()

        total = complaints.count()
        count_by_status = complaints.values('status').annotate(count=Count('id'))
        count_by_priority = complaints.values('priority').annotate(count=Count('id'))
        count_by_source = complaints.values('source').annotate(count=Count('id'))
        count_by_department = complaints.values('department__name').annotate(count=Count('id'))

        overdue_q = Q(sla_deadline__lt=today) & ~Q(status__in=['resolved', 'closed'])
        overdue_count = complaints.filter(overdue_q).count()

        resolved = complaints.filter(status='resolved', date_resolved__isnull=False)
        avg_resolution = resolved.annotate(
            duration=ExpressionWrapper(F('date_resolved') - F('date_entered_system'), output_field=DurationField())
        ).aggregate(avg=Avg('duration'))['avg']
        avg_resolution_hours = avg_resolution.total_seconds() / 3600 if avg_resolution else None

        last_7 = complaints.filter(date_entered_system__date__gte=today - timedelta(days=7)).count()
        last_30 = complaints.filter(date_entered_system__date__gte=today - timedelta(days=30)).count()

        return Response({
            'total_complaints': total,
            'count_by_status': list(count_by_status),
            'count_by_priority': list(count_by_priority),
            'count_by_source': list(count_by_source),
            'count_by_department': [
                {'department': item['department__name'] or 'Unassigned', 'count': item['count']} for item in count_by_department
            ],
            'complaints_overdue': overdue_count,
            'average_resolution_time_hours': avg_resolution_hours,
            'registered_last_7_days': last_7,
            'registered_last_30_days': last_30,
        })


class AgentPerformanceView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role not in {'admin', 'reviewer'}:
            return Response({'detail': 'Access denied.'}, status=403)

        today = timezone.localdate()
        handlers = User.objects.filter(role='handler')
        performance = []

        for handler in handlers:
            assigned = Complaint.objects.filter(assigned_to=handler)
            total_assigned = assigned.count()
            resolved = assigned.filter(status='resolved', date_resolved__isnull=False)
            total_resolved = resolved.count()
            overdue = assigned.filter(sla_deadline__lt=today).exclude(status__in=['resolved', 'closed']).count()

            avg_duration = resolved.annotate(
                duration=ExpressionWrapper(F('date_resolved') - F('date_entered_system'), output_field=DurationField())
            ).aggregate(avg=Avg('duration'))['avg']
            avg_days_to_resolve = avg_duration.total_seconds() / 86400 if avg_duration else None
            resolution_rate = (total_resolved / total_assigned * 100) if total_assigned else 0

            performance.append({
                'handler_id': handler.id,
                'handler_username': handler.username,
                'total_assigned': total_assigned,
                'total_resolved': total_resolved,
                'total_overdue': overdue,
                'average_days_to_resolve': avg_days_to_resolve,
                'resolution_rate_percent': resolution_rate,
            })

        return Response(performance)


class AccountRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        user = self.request.user
        if user and hasattr(user, 'role') and user.role:
            role_map = {
                'admin': 'admin-dashboard',
                'reviewer': 'reviewer-dashboard',
                'handler': 'handler-dashboard',
                'registrar': 'registrar-dashboard',
            }
            if user.role in role_map:
                return reverse(role_map[user.role])
        return reverse('home')


class RegistrarDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'complaints/registrar_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'registrar':
            raise DjangoPermissionDenied('Only registrars may access the registrar dashboard.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        my_complaints = Complaint.objects.filter(registered_by=self.request.user).select_related('assigned_to')
        all_complaints = Complaint.objects.select_related('assigned_to').all()

        total_count = my_complaints.count()
        pending_count = my_complaints.filter(status__in=['pending', 'new', 'assigned', 'under_review', 'in_progress', 'pending_info']).count()
        resolved_count = my_complaints.filter(status__in=['resolved', 'closed']).count()
        overdue_count = my_complaints.filter(
            sla_deadline__lt=timezone.localdate()
        ).exclude(status__in=['resolved', 'closed']).count()

        context['total_count'] = total_count
        context['pending_count'] = pending_count
        context['resolved_count'] = resolved_count
        context['overdue_count'] = overdue_count
        # Show ALL complaints (registrars get full system-wide access with View buttons)
        context['recent_complaints'] = all_complaints.order_by('-date_entered_system')
        return context


class HandlerDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'complaints/handler_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'handler':
            raise DjangoPermissionDenied('Only handlers may access the handler dashboard.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        complaints = Complaint.objects.filter(assigned_to=self.request.user).select_related('assigned_to')

        assigned_count = complaints.count()
        overdue_count = complaints.filter(
            sla_deadline__lt=timezone.localdate()
        ).exclude(status__in=['resolved', 'closed']).count()
        resolved_count = complaints.filter(status__in=['resolved', 'closed']).count()

        context['assigned_count'] = assigned_count
        context['overdue_count'] = overdue_count
        context['resolved_count'] = resolved_count
        context['assigned_complaints'] = complaints.order_by('-date_entered_system')
        return context


class ReviewerDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'complaints/reviewer_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'reviewer':
            raise DjangoPermissionDenied('Only reviewers may access the reviewer dashboard.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        complaints = Complaint.objects.select_related('assigned_to').all()

        context['new_count'] = complaints.filter(status='new', assigned_to__isnull=True).count()
        context['overdue_count'] = complaints.filter(
            sla_deadline__lt=today
        ).exclude(status__in=['resolved', 'closed']).count()
        context['active_count'] = complaints.exclude(status__in=['resolved', 'closed']).count()
        context['complaints'] = complaints.order_by('-date_entered_system')
        context['handlers'] = User.objects.filter(role='handler').order_by('username')
        return context


class AdminDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/admin_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'admin':
            raise DjangoPermissionDenied('Only admins may access the admin dashboard.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        month_start = today.replace(day=1)
        complaints = Complaint.objects.select_related('assigned_to').all()

        context['total_count'] = complaints.count()
        context['active_count'] = complaints.exclude(status__in=['resolved', 'closed']).count()
        context['overdue_count'] = complaints.filter(
            sla_deadline__lt=today
        ).exclude(status__in=['resolved', 'closed']).count()
        context['resolved_month'] = complaints.filter(
            status='resolved',
            date_resolved__date__gte=month_start
        ).count()

        status_counts = complaints.values('status').annotate(count=Count('id'))
        context['status_data'] = [
            {'status': item['status'].replace('_', ' ').replace('under review', 'Under Review').title(), 'count': item['count']} for item in status_counts
        ]

        source_counts = complaints.values('source').annotate(count=Count('id')).order_by('-count')
        context['source_data'] = [
            {'source': item['source'].title(), 'count': item['count']} for item in source_counts
        ]

        handlers = User.objects.filter(role='handler')
        agent_stats = []
        for handler in handlers:
            assigned = complaints.filter(assigned_to=handler)
            resolved = assigned.filter(status='resolved', date_resolved__isnull=False)
            overdue = assigned.filter(sla_deadline__lt=today).exclude(status__in=['resolved', 'closed']).count()
            avg_duration = resolved.annotate(
                duration=ExpressionWrapper(F('date_resolved') - F('date_entered_system'), output_field=DurationField())
            ).aggregate(avg=Avg('duration'))['avg']
            avg_days = avg_duration.total_seconds() / 86400 if avg_duration else None
            total_assigned = assigned.count()
            resolution_rate = (resolved.count() / total_assigned * 100) if total_assigned else 0

            agent_stats.append({
                'handler_username': handler.get_full_name() or handler.username,
                'total_assigned': total_assigned,
                'total_resolved': resolved.count(),
                'total_overdue': overdue,
                'average_days_to_resolve': round(avg_days, 1) if avg_days is not None else None,
                'resolution_rate_percent': round(resolution_rate, 1),
            })

        context['agent_stats'] = agent_stats
        context['recent_complaints'] = complaints.order_by('-date_entered_system')[:10]
        return context


class UserProfileView(LoginRequiredMixin, View):
    template_name = 'complaints/user_profile.html'

    def get(self, request, *args, **kwargs):
        return TemplateView.as_view(template_name=self.template_name)(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if 'update_profile' in request.POST:
            user = request.user
            user.first_name = (request.POST.get('first_name') or '').strip()
            user.last_name = (request.POST.get('last_name') or '').strip()
            user.email = (request.POST.get('email') or '').strip()
            user.save()
            messages.success(request, 'Profile updated successfully.')
        return redirect('user-profile')


class AdminUserManagementView(LoginRequiredMixin, View):
    template_name = 'dashboard/admin_user_management.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'admin':
            raise DjangoPermissionDenied('Only admins may access user management.')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        users = User.objects.select_related('department').order_by('username')
        departments = Department.objects.order_by('name')
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        handler_count = User.objects.filter(role='handler').count()
        inactive_users = total_users - active_users
        return TemplateView.as_view(template_name=self.template_name, extra_context={
            'users': users,
            'departments': departments,
            'role_choices': User.ROLE_CHOICES,
            'total_users': total_users,
            'active_users': active_users,
            'handler_count': handler_count,
            'inactive_users': inactive_users,
        })(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')

        if action == 'create':
            username = (request.POST.get('username') or '').strip()
            password = request.POST.get('password') or ''
            first_name = (request.POST.get('first_name') or '').strip()
            last_name = (request.POST.get('last_name') or '').strip()
            email = (request.POST.get('email') or '').strip()
            phone = (request.POST.get('phone') or '').strip()
            role = request.POST.get('role') or 'registrar'
            is_active = request.POST.get('is_active') != 'off'
            department_id = request.POST.get('department') or None

            if not username or not password:
                messages.error(request, 'Username and password are required.')
                return redirect('admin-user-management')

            if User.objects.filter(username=username).exists():
                messages.error(request, 'Username already exists.')
                return redirect('admin-user-management')

            department = Department.objects.filter(id=department_id).first() if department_id else None
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                email=email,
                role=role,
                phone=phone,
                department=department,
                is_active=is_active,
            )
            from .services import notify_user_changed
            notify_user_changed(performed_by=request.user, target_user=user, change_type='user_created')
            messages.success(request, f'User {user.username} created successfully.')
            return redirect('admin-user-management')

        if action == 'delete':
            user_id = request.POST.get('user_id')
            user = User.objects.filter(id=user_id).first()
            if not user:
                messages.error(request, 'User not found.')
                return redirect('admin-user-management')
            if user == request.user:
                messages.error(request, 'You cannot delete your own account.')
                return redirect('admin-user-management')
            from .services import notify_user_changed
            target_username = user.username
            target_role = user.role
            user.delete()
            # Create a temporary object for notification
            from django.contrib.auth import get_user_model
            User_dummy = get_user_model()
            dummy_user = User_dummy(username=target_username, role=target_role)
            notify_user_changed(performed_by=request.user, target_user=dummy_user, change_type='user_deleted')
            messages.success(request, f'User {target_username} deleted successfully.')
            return redirect('admin-user-management')

        if action == 'toggle_active':
            user_id = request.POST.get('user_id')
            user = User.objects.filter(id=user_id).first()
            if not user:
                messages.error(request, 'User not found.')
                return redirect('admin-user-management')
            user.is_active = not user.is_active
            user.save()
            from .services import notify_user_changed
            change_type = 'user_updated'
            notify_user_changed(performed_by=request.user, target_user=user, change_type=change_type)
            status_text = 'activated' if user.is_active else 'deactivated'
            messages.success(request, f'User {user.username} {status_text} successfully.')
            return redirect('admin-user-management')

        if action == 'update':
            user_id = request.POST.get('user_id')
            user = User.objects.filter(id=user_id).first()
            if not user:
                messages.error(request, 'User not found.')
                return redirect('admin-user-management')

            new_username = (request.POST.get('username') or '').strip()
            if new_username and new_username != user.username:
                if User.objects.filter(username=new_username).exclude(id=user.id).exists():
                    messages.error(request, 'Username already taken.')
                    return redirect('admin-user-management')
                user.username = new_username

            user.first_name = (request.POST.get('first_name') or '').strip()
            user.last_name = (request.POST.get('last_name') or '').strip()
            user.email = (request.POST.get('email') or '').strip()
            user.phone = (request.POST.get('phone') or '').strip()
            user.role = request.POST.get('role') or user.role
            department_id = request.POST.get('department') or None
            user.department = Department.objects.filter(id=department_id).first() if department_id else None
            user.is_active = request.POST.get('is_active') == 'on'

            new_password = (request.POST.get('new_password') or '').strip()
            if new_password:
                user.set_password(new_password)

            user.save()
            from .services import notify_user_changed
            notify_user_changed(performed_by=request.user, target_user=user, change_type='user_updated')
            messages.success(request, f'User {user.username} updated successfully.')
            return redirect('admin-user-management')

        messages.error(request, 'Invalid action.')
        return redirect('admin-user-management')


class HandleComplaintView(LoginRequiredMixin, View):
    template_name = 'complaints/handle_complaint.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in {'handler', 'admin', 'reviewer', 'registrar'}:
            raise DjangoPermissionDenied('Only handlers, admins, reviewers, and registrars may access this page.')
        return super().dispatch(request, *args, **kwargs)

    def get_complaint(self, pk):
        return get_object_or_404(Complaint.objects.select_related('assigned_to', 'registered_by'), pk=pk)

    def get(self, request, pk, *args, **kwargs):
        complaint = self.get_complaint(pk)
        # Ensure handler only sees their own assigned complaints
        # Registrars, admins, and reviewers can see all complaints
        if request.user.role == 'handler' and complaint.assigned_to_id != request.user.id:
            raise DjangoPermissionDenied('You can only handle complaints assigned to you.')

        actions = complaint.actions.select_related('handler').all()
        notes = complaint.notes.select_related('author').all()
        attachments = complaint.attachments.all()

        return TemplateView.as_view(template_name=self.template_name, extra_context={
            'complaint': complaint,
            'actions': actions,
            'notes': notes,
            'attachments': attachments,
            'handler_statuses': Complaint.HANDLER_STATUSES,
            'active_page': 'handle-complaint',
        })(request, *args, **kwargs)

    def post(self, request, pk, *args, **kwargs):
        complaint = self.get_complaint(pk)
        if request.user.role == 'handler' and complaint.assigned_to_id != request.user.id:
            raise DjangoPermissionDenied('You can only handle complaints assigned to you.')

        action_taken = request.POST.get('action_taken') == 'on'
        action_description = (request.POST.get('action_description') or '').strip()
        new_status = request.POST.get('status') or ''
        note_text = (request.POST.get('note') or '').strip()

        # Validate that closed is only allowed when action has been taken
        if new_status == 'closed' and not complaint.has_action_taken and not action_taken:
            messages.error(request, 'The complaint can only be closed after an action has been taken. Please mark action as taken first.')
            return redirect('complaint-handle', pk=complaint.pk)

        # Validate handler statuses (handlers only)
        if request.user.role == 'handler' and new_status not in {'in_progress', 'pending_info', 'resolved', 'closed', ''}:
            messages.error(request, 'Invalid status selection.')
            return redirect('complaint-handle', pk=complaint.pk)

        if new_status:
            if new_status not in dict(Complaint.STATUS_CHOICES):
                messages.error(request, 'Invalid status value.')
                return redirect('complaint-handle', pk=complaint.pk)

            # Record the action
            ComplaintAction.objects.create(
                complaint=complaint,
                handler=request.user,
                action_taken=action_taken,
                action_description=action_description,
                previous_status=complaint.status,
                new_status=new_status,
            )

            complaint.status = new_status
            complaint.save()

            # Send notification about status update
            from .services import notify_status_update
            notify_status_update(complaint, request.user)

        # Add a note if provided
        if note_text:
            ComplaintNote.objects.create(
                complaint=complaint,
                author=request.user,
                note=note_text,
            )

        messages.success(request, f'Complaint {complaint.case_number} updated successfully.')
        return redirect('complaint-handle', pk=complaint.pk)


class HandleComplaintRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in {'handler', 'admin', 'reviewer', 'registrar'}:
            raise DjangoPermissionDenied('Only handlers, admins, reviewers, and registrars may access this page.')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        messages.warning(request, 'Please select a complaint from the dashboard to handle.')
        return super().get(request, *args, **kwargs)

    def get_redirect_url(self, *args, **kwargs):
        if self.request.user.role == 'admin':
            return reverse('admin-dashboard')
        if self.request.user.role == 'reviewer':
            return reverse('reviewer-dashboard')
        if self.request.user.role == 'registrar':
            return reverse('registrar-dashboard')
        return reverse('handler-dashboard')


class AdminAllComplaintsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/admin_all_complaints.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'admin':
            raise DjangoPermissionDenied('Only admins may access all complaints board.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        complaints = Complaint.objects.select_related('assigned_to', 'category').all()
        handlers = User.objects.filter(role='handler', is_active=True).order_by('username')
        context['status_columns'] = [
            ('new', 'New'),
            ('under_review', 'Under Review'),
            ('assigned', 'Assigned'),
            ('in_progress', 'In Progress'),
            ('pending_info', 'Pending Info'),
            ('resolved', 'Resolved'),
            ('closed', 'Closed'),
        ]
        categories = Category.objects.order_by('name')
        context['priority_choices'] = Complaint.PRIORITY_CHOICES
        context['source_choices'] = Complaint.SOURCE_CHOICES
        context['handlers'] = handlers
        context['categories'] = categories
        context['complaints'] = complaints
        return context


class EditComplaintView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/edit_complaint.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in {'admin', 'reviewer', 'registrar'}:
            raise DjangoPermissionDenied('You do not have permission to edit complaints.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pk = self.kwargs.get('pk')
        complaint = get_object_or_404(Complaint.objects.select_related('category', 'assigned_to'), pk=pk)
        handlers = User.objects.filter(role='handler', is_active=True).order_by('username')
        context['status_columns'] = [
            ('new', 'New'),
            ('under_review', 'Under Review'),
            ('assigned', 'Assigned'),
            ('in_progress', 'In Progress'),
            ('pending_info', 'Pending Info'),
            ('resolved', 'Resolved'),
            ('closed', 'Closed'),
        ]
        categories = Category.objects.order_by('name')
        context['priority_choices'] = Complaint.PRIORITY_CHOICES
        context['source_choices'] = Complaint.SOURCE_CHOICES
        context['handlers'] = handlers
        context['categories'] = categories
        context['complaint'] = complaint
        return context


class ComplaintRegistrationView(LoginRequiredMixin, View):
    template_name = 'complaints/register_complaint.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in {'admin', 'registrar', 'reviewer'}:
            raise DjangoPermissionDenied('Only admins, registrars, and reviewers may register complaints.')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        handlers = User.objects.filter(role='handler', is_active=True).order_by('username')
        context = {
            'handlers': handlers,
            'source_choices': Complaint.SOURCE_CHOICES,
        }
        return TemplateResponse(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        categories = Category.objects.order_by('name')
        handlers = User.objects.filter(role='handler', is_active=True).order_by('username')

        # New form fields
        case_number = (request.POST.get('case_number') or '').strip()
        full_name = (request.POST.get('full_name') or '').strip()
        phone_number = (request.POST.get('phone_number') or '').strip()
        complaint_source = request.POST.get('complaint_source') or 'online'
        email = (request.POST.get('email') or '').strip()
        national_id = (request.POST.get('national_id') or '').strip()
        bank_institution = (request.POST.get('bank_institution') or '').strip()
        complaint_title = (request.POST.get('complaint_title') or '').strip()
        complaint_date = request.POST.get('complaint_date')
        description = (request.POST.get('description') or '').strip()
        assigned_to_id = request.POST.get('assigned_to')
        response_due_days = request.POST.get('response_due_days')

        def render_form_error():
            context = {
                'categories': categories,
                'handlers': handlers,
                'source_choices': Complaint.SOURCE_CHOICES,
            }
            return TemplateResponse(request, self.template_name, context)

        # Validate required fields
        if not full_name or not phone_number or not complaint_title or not complaint_date or not assigned_to_id or not response_due_days:
            messages.error(request, 'Please fill all required fields: full name, phone number, complaint title, complaint date, assigned handler, and response due days.')
            return render_form_error()

        # Validate case_number uniqueness if provided
        if case_number and Complaint.objects.filter(case_number=case_number).exists():
            messages.error(request, 'A complaint with this case number already exists.')
            return render_form_error()

        # Get assigned user and validate role
        try:
            assigned_to = User.objects.get(id=assigned_to_id)
            if assigned_to.role != 'handler':
                messages.error(request, 'Assigned user must have the role "handler".')
                return render_form_error()
        except User.DoesNotExist:
            messages.error(request, 'Selected handler does not exist.')
            return render_form_error()

        # Validate response_due_days
        try:
            response_due_days_int = int(response_due_days)
            if response_due_days_int < 1:
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, 'Response due days must be a positive integer.')
            return render_form_error()

        # Create complaint with new fields
        complaint = Complaint(
            case_number=case_number if case_number else '',  # Will be auto-generated if empty
            full_name=full_name,
            phone_number=phone_number,
            complaint_source=complaint_source,
            email=email if email else '',
            national_id=national_id if national_id else '',
            bank_institution=bank_institution if bank_institution else '',
            complaint_title=complaint_title,
            complaint_date=complaint_date,
            description=description if description else '',
            assigned_to=assigned_to,
            response_due_days=response_due_days_int,
            registered_by=request.user,
            status='pending',  # Default status as per requirements
        )
        complaint.save()

        # Send notifications
        from .services import notify_complaint_created, notify_complaint_assigned
        notify_complaint_created(complaint, performed_by=request.user)
        
        email_feedback = ""
        if complaint.assigned_to:
            email_success, email_message = notify_complaint_assigned(complaint, assigned_by=request.user)
            if email_success:
                email_feedback = f" ✓ {email_message}"
                messages.success(request, email_message)
            else:
                email_feedback = f" ⚠ {email_message}"
                messages.warning(request, email_message)

        # Handle attachments
        for upload in request.FILES.getlist('attachments'):
            Attachment.objects.create(
                complaint=complaint,
                file=upload,
                file_type=getattr(upload, 'content_type', '') or '',
            )

        messages.success(request, f'Complaint {complaint.case_number} registered successfully.{email_feedback}')
        return redirect('complaint-register')


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.mark_as_read()
        return Response({'status': 'ok'})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True, read_at=timezone.now())
        return Response({'status': 'ok'})

    @action(detail=False, methods=['get'])
    def recent(self, request):
        notifications = self.get_queryset()[:5]
        serializer = self.get_serializer(notifications, many=True)
        return Response(serializer.data)


class UnreadNotificationCountView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({'unread_count': count})


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True, read_at=timezone.now())
        return Response({'status': 'ok'})


class NotificationsPageView(LoginRequiredMixin, TemplateView):
    template_name = 'complaints/notifications.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_page'] = 'notifications'
        context['notification_types'] = Notification.NOTIFICATION_TYPES
        return context


class AdminAnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/admin_analytics.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'admin':
            raise DjangoPermissionDenied('Only admins may access analytics.')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        complaints = Complaint.objects.select_related('assigned_to', 'department').all()

        # KPI calculations
        total_count = complaints.count()
        resolved_count = complaints.filter(status__in=['resolved', 'closed']).count()
        resolution_rate = round((resolved_count / total_count * 100), 1) if total_count else 0

        resolved_with_dates = complaints.filter(status='resolved', date_resolved__isnull=False)
        avg_duration = resolved_with_dates.annotate(
            duration=ExpressionWrapper(F('date_resolved') - F('date_entered_system'), output_field=DurationField())
        ).aggregate(avg=Avg('duration'))['avg']
        avg_resolution_days = round(avg_duration.total_seconds() / 86400, 1) if avg_duration else None

        # SLA Compliance
        resolved_or_closed = complaints.filter(status__in=['resolved', 'closed'], date_resolved__isnull=False)
        sla_met = resolved_or_closed.filter(sla_deadline__isnull=False, date_resolved__date__lte=F('sla_deadline')).count()
        sla_total = resolved_or_closed.filter(sla_deadline__isnull=False).count()
        sla_compliance = round((sla_met / sla_total * 100), 1) if sla_total else 0

        # Status distribution
        status_counts = complaints.values('status').annotate(count=Count('id'))
        status_map = {
            'new': 'New', 'under_review': 'Under Review', 'assigned': 'Assigned',
            'in_progress': 'In Progress', 'pending_info': 'Pending Info',
            'resolved': 'Resolved', 'closed': 'Closed', 'pending': 'Pending',
        }
        context['status_data'] = [
            {'status': status_map.get(item['status'], item['status'].title()), 'count': item['count']}
            for item in sorted(status_counts, key=lambda x: x['status'])
        ]

        # Source distribution
        source_counts = complaints.values('complaint_source').annotate(count=Count('id')).order_by('-count')
        source_map = dict(Complaint.SOURCE_CHOICES)
        context['source_data'] = [
            {'source': source_map.get(item['complaint_source'], item['complaint_source'].title()), 'count': item['count']}
            for item in source_counts
        ]

        # Priority distribution
        priority_counts = complaints.values('priority').annotate(count=Count('id')).order_by('priority')
        context['priority_data'] = [
            {'priority': item['priority'].title(), 'count': item['count']}
            for item in priority_counts if item['priority']
        ]

        # Trend data (last 12 months)
        monthly_data = defaultdict(int)
        for c in complaints.filter(date_entered_system__gte=today - timedelta(days=365)).values('date_entered_system'):
            month_key = c['date_entered_system'].strftime('%b %Y')
            monthly_data[month_key] += 1
        sorted_months = sorted(monthly_data.keys(), key=lambda m: datetime.strptime(m, '%b %Y'))
        context['trend_data'] = [{'label': m, 'count': monthly_data[m]} for m in sorted_months]

        # Department stats
        departments = Department.objects.annotate(
            total=Count('users__assigned_complaints'),
            active=Count('users__assigned_complaints', filter=~Q(users__assigned_complaints__status__in=['resolved', 'closed'])),
            resolved=Count('users__assigned_complaints', filter=Q(users__assigned_complaints__status__in=['resolved', 'closed'])),
            overdue=Count('users__assigned_complaints', filter=Q(users__assigned_complaints__sla_deadline__lt=today, users__assigned_complaints__status__in=['new', 'under_review', 'assigned', 'in_progress', 'pending_info', 'pending'])),
        ).values('name', 'total', 'active', 'resolved', 'overdue').order_by('-total')

        context['department_stats'] = departments
        context['total_count'] = total_count
        context['resolution_rate'] = resolution_rate
        context['avg_resolution_days'] = avg_resolution_days
        context['sla_compliance'] = sla_compliance
        context['agent_stats'] = self._get_agent_stats(complaints, today)
        context['active_page'] = 'analytics'
        return context

    def _get_agent_stats(self, complaints, today):
        stats = []
        for handler in User.objects.filter(role='handler'):
            assigned = complaints.filter(assigned_to=handler)
            total_assigned = assigned.count()
            resolved = assigned.filter(status__in=['resolved', 'closed'])
            total_resolved = resolved.count()
            overdue = assigned.filter(sla_deadline__lt=today).exclude(status__in=['resolved', 'closed']).count()
            avg_duration = resolved.annotate(
                duration=ExpressionWrapper(F('date_resolved') - F('date_entered_system'), output_field=DurationField())
            ).aggregate(avg=Avg('duration'))['avg']
            avg_days = round(avg_duration.total_seconds() / 86400, 1) if avg_duration else None
            resolution_rate = round((total_resolved / total_assigned * 100), 1) if total_assigned else 0
            stats.append({
                'handler_username': handler.get_full_name() or handler.username,
                'total_assigned': total_assigned,
                'total_resolved': total_resolved,
                'total_overdue': overdue,
                'average_days_to_resolve': avg_days,
                'resolution_rate_percent': resolution_rate,
            })
        return sorted(stats, key=lambda s: s['resolution_rate_percent'], reverse=True)


class AdminSettingsView(LoginRequiredMixin, View):
    template_name = 'dashboard/admin_settings.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role != 'admin':
            raise DjangoPermissionDenied('Only admins may access settings.')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        departments = Department.objects.annotate(
            handler_count=Count('users', filter=Q(users__role='handler'))
        ).order_by('name')
        categories = Category.objects.order_by('name')
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        handler_count = User.objects.filter(role='handler').count()
        email_config = SystemConfiguration.get_settings()

        context = {
            'departments': departments,
            'categories': categories,
            'total_users': total_users,
            'active_users': active_users,
            'handler_count': handler_count,
            'email_config': email_config,
            'active_page': 'settings',
        }
        return TemplateResponse(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get('action')

        # Email configuration
        if action == 'save_email_config':
            config = SystemConfiguration.get_settings()
            config.sender_email = (request.POST.get('sender_email') or '').strip()
            config.sender_name = (request.POST.get('sender_name') or '').strip()
            config.smtp_host = (request.POST.get('smtp_host') or '').strip()
            try:
                config.smtp_port = int(request.POST.get('smtp_port', 587))
            except (ValueError, TypeError):
                config.smtp_port = 587
            config.smtp_username = (request.POST.get('smtp_username') or '').strip()
            smtp_password = (request.POST.get('smtp_password') or '').strip()
            if smtp_password:
                config.smtp_password = smtp_password
            config.use_tls = request.POST.get('use_tls') == 'on'
            config.save()

            if config.smtp_host:
                # Run a live SMTP test against the freshly-saved configuration so
                # the UI border reflects the real status (green / red) immediately.
                from .services import record_smtp_test_result
                test_ok, test_message = record_smtp_test_result(config)

                if test_ok:
                    messages.success(
                        request,
                        f'Email configuration saved and verified. {test_message}',
                    )
                else:
                    messages.warning(
                        request,
                        f'Email configuration saved, but the live SMTP test failed: {test_message}',
                    )
            else:
                messages.success(
                    request,
                    'Email configuration saved. No direct SMTP host configured; the app will use the Django email backend from environment settings.',
                )
            return redirect('admin-settings')

        # Run SMTP test on the currently-saved configuration (no save).
        if action == 'test_email_config':
            from .services import record_smtp_test_result
            config = SystemConfiguration.get_settings()
            test_ok, test_message = record_smtp_test_result(config)
            if test_ok:
                messages.success(request, f'SMTP test passed. {test_message}')
            else:
                messages.error(request, f'SMTP test failed: {test_message}')
            return redirect('admin-settings')

        # Department actions
        if action == 'add_department':
            name = (request.POST.get('name') or '').strip()
            default_sla_days = request.POST.get('default_sla_days') or 0
            if not name:
                messages.error(request, 'Department name is required.')
                return redirect('admin-settings')
            if Department.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Department already exists.')
                return redirect('admin-settings')
            Department.objects.create(name=name, default_sla_days=int(default_sla_days))
            messages.success(request, f'Department "{name}" created successfully.')
            return redirect('admin-settings')

        if action == 'edit_department':
            dept_id = request.POST.get('dept_id')
            name = (request.POST.get('name') or '').strip()
            default_sla_days = request.POST.get('default_sla_days') or 0
            dept = Department.objects.filter(id=dept_id).first()
            if not dept:
                messages.error(request, 'Department not found.')
                return redirect('admin-settings')
            if name and name.lower() != dept.name.lower() and Department.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Department name already exists.')
                return redirect('admin-settings')
            if name:
                dept.name = name
            dept.default_sla_days = int(default_sla_days)
            dept.save()
            messages.success(request, f'Department "{dept.name}" updated successfully.')
            return redirect('admin-settings')

        if action == 'delete_department':
            dept_id = request.POST.get('dept_id')
            dept = Department.objects.filter(id=dept_id).first()
            if not dept:
                messages.error(request, 'Department not found.')
                return redirect('admin-settings')
            name = dept.name
            dept.delete()
            messages.success(request, f'Department "{name}" deleted successfully.')
            return redirect('admin-settings')

        # Category actions
        if action == 'add_category':
            name = (request.POST.get('name') or '').strip()
            default_sla_days = request.POST.get('default_sla_days') or 0
            if not name:
                messages.error(request, 'Category name is required.')
                return redirect('admin-settings')
            if Category.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Category already exists.')
                return redirect('admin-settings')
            Category.objects.create(name=name, default_sla_days=int(default_sla_days))
            messages.success(request, f'Category "{name}" created successfully.')
            return redirect('admin-settings')

        if action == 'edit_category':
            cat_id = request.POST.get('cat_id')
            name = (request.POST.get('name') or '').strip()
            default_sla_days = request.POST.get('default_sla_days') or 0
            cat = Category.objects.filter(id=cat_id).first()
            if not cat:
                messages.error(request, 'Category not found.')
                return redirect('admin-settings')
            if name and name.lower() != cat.name.lower() and Category.objects.filter(name__iexact=name).exists():
                messages.error(request, 'Category name already exists.')
                return redirect('admin-settings')
            if name:
                cat.name = name
            cat.default_sla_days = int(default_sla_days)
            cat.save()
            messages.success(request, f'Category "{cat.name}" updated successfully.')
            return redirect('admin-settings')

        if action == 'delete_category':
            cat_id = request.POST.get('cat_id')
            cat = Category.objects.filter(id=cat_id).first()
            if not cat:
                messages.error(request, 'Category not found.')
                return redirect('admin-settings')
            name = cat.name
            cat.delete()
            messages.success(request, f'Category "{name}" deleted successfully.')
            return redirect('admin-settings')

        messages.error(request, 'Invalid action.')
        return redirect('admin-settings')
