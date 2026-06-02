"""
Management command to check SLA deadlines and create notifications.

Usage:
    python manage.py check_sla_deadlines

This command checks all active complaints for:
1. Approaching SLA deadlines (within 3 days) -> creates 'sla_approaching' notifications
2. Exceeded SLA deadlines -> creates 'sla_exceeded' notifications

It can be scheduled via cron or Windows Task Scheduler to run daily.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from complaints.models import Complaint, Notification


class Command(BaseCommand):
    help = 'Check SLA deadlines and send notifications for approaching/exceeded deadlines'

    def handle(self, *args, **options):
        today = timezone.localdate()
        active_statuses = ['new', 'under_review', 'assigned', 'in_progress', 'pending_info', 'pending']
        
        from complaints.services import notify_sla_event
        
        # Check for exceeded deadlines
        exceeded = Complaint.objects.filter(
            sla_deadline__lt=today,
            status__in=active_statuses,
        ).exclude(
            sla_deadline__isnull=True
        )
        
        excluded_count = 0
        for complaint in exceeded:
            # Check if we already notified about this exceeded deadline today
            already_notified = Notification.objects.filter(
                complaint=complaint,
                notification_type='sla_exceeded',
                created_at__date=today,
            ).exists()
            
            if not already_notified:
                notify_sla_event(complaint, 'sla_exceeded')
                self.stdout.write(
                    self.style.WARNING(
                        f'SLA EXCEEDED: {complaint.case_number} (due {complaint.sla_deadline})'
                    )
                )
            else:
                excluded_count += 1
        
        # Check for approaching deadlines (within 3 days)
        approaching = Complaint.objects.filter(
            sla_deadline__gte=today,
            sla_deadline__lte=today + timezone.timedelta(days=3),
            status__in=active_statuses,
        ).exclude(
            sla_deadline__isnull=True
        )
        
        approach_count = 0
        for complaint in approaching:
            # Check if already notified about this approaching deadline
            already_notified = Notification.objects.filter(
                complaint=complaint,
                notification_type='sla_approaching',
                created_at__date=today,
            ).exists()
            
            if not already_notified:
                notify_sla_event(complaint, 'sla_approaching')
                self.stdout.write(
                    self.style.WARNING(
                        f'SLA APPROACHING: {complaint.case_number} - {complaint.days_remaining} day(s) remaining'
                    )
                )
                approach_count += 1
        
        exceeded_count = exceeded.count() - excluded_count
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSLA check complete:'
                f'\n  - {exceeded_count} exceeded deadline(s) notified'
                f'\n  - {approach_count} approaching deadline(s) notified'
                f'\n  - {excluded_count} already notified (skipped)'
            )
        )