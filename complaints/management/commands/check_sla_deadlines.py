"""
Management command to check SLA deadlines and trigger escalations.

Usage:
    python manage.py check_sla_deadlines
    python manage.py check_sla_deadlines --dry-run
    python manage.py check_sla_deadlines --skip-escalation

This command:
  1. Creates SLA-approaching / SLA-exceeded in-app notifications
  2. Sends 50% / 75% SLA-consumed reminders (Section 8.2)
  3. Auto-escalates overdue complaints through the 4-level matrix (Section 9)

Schedule via cron / Windows Task Scheduler to run hourly.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from complaints.models import Complaint, Notification
from complaints.escalation import check_sla_reminders, run_auto_escalation


class Command(BaseCommand):
    help = 'Check SLA deadlines, send reminders, and auto-escalate overdue complaints.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be done without making any changes.',
        )
        parser.add_argument(
            '--skip-escalation',
            action='store_true',
            help='Skip the auto-escalation step (notifications only).',
        )
        parser.add_argument(
            '--approaching-window-hours',
            type=int,
            default=24,
            help='Complaints within this many hours of their deadline are flagged as "approaching".',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        skip_escalation = options.get('skip_escalation', False)
        approaching_hours = options.get('approaching_window_hours', 24)

        now = timezone.now()
        today = timezone.localdate()
        active_statuses = [
            'new', 'under_review', 'assigned', 'in_progress',
            'pending_info', 'pending', 'escalated',
        ]

        # 1. Existing "approaching / exceeded" notifications (kept for back-compat)
        from complaints.services import notify_sla_event

        exceeded_qs = Complaint.objects.filter(
            sla_deadline__lt=now,
            status__in=active_statuses,
        ).exclude(sla_deadline__isnull=True)

        exceeded_count = 0
        skipped_existing = 0
        for complaint in exceeded_qs:
            already = Notification.objects.filter(
                complaint=complaint,
                notification_type='sla_exceeded',
                created_at__date=today,
            ).exists()
            if already:
                skipped_existing += 1
                continue
            if not dry_run:
                notify_sla_event(complaint, 'sla_exceeded')
            exceeded_count += 1
            self.stdout.write(self.style.WARNING(
                f'SLA EXCEEDED: {complaint.case_number} (due {complaint.sla_deadline})'
            ))

        approaching_qs = Complaint.objects.filter(
            sla_deadline__gte=now,
            sla_deadline__lte=now + timezone.timedelta(hours=approaching_hours),
            status__in=active_statuses,
        ).exclude(sla_deadline__isnull=True)

        approach_count = 0
        for complaint in approaching_qs:
            already = Notification.objects.filter(
                complaint=complaint,
                notification_type='sla_approaching',
                created_at__date=today,
            ).exists()
            if already:
                continue
            if not dry_run:
                notify_sla_event(complaint, 'sla_approaching')
            approach_count += 1
            self.stdout.write(self.style.WARNING(
                f'SLA APPROACHING: {complaint.case_number} - '
                f'{complaint.hours_remaining}h remaining'
            ))

        # 2. 50% / 75% SLA-consumed reminders (Section 8.2)
        sent_50, sent_75 = (0, 0)
        if not dry_run:
            sent_50, sent_75 = check_sla_reminders(dry_run=False)
        else:
            sent_50, sent_75 = check_sla_reminders(dry_run=True)
        if sent_50 or sent_75:
            self.stdout.write(self.style.WARNING(
                f'SLA reminders: 50%={sent_50}, 75%={sent_75}'
            ))

        # 3. Auto-escalate overdue complaints (Section 9)
        escalated = 0
        if not skip_escalation:
            if dry_run:
                escalated, _ = run_auto_escalation(dry_run=True)
            else:
                escalated, _ = run_auto_escalation(dry_run=False)
            if escalated:
                self.stdout.write(self.style.ERROR(
                    f'AUTO-ESCALATED: {escalated} complaint(s)'
                ))

        # Summary
        verb = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'\n{verb}SLA check complete:\n'
            f'  - {exceeded_count} exceeded deadline(s) notified ({skipped_existing} already notified)\n'
            f'  - {approach_count} approaching deadline(s) notified\n'
            f'  - {sent_50} 50% reminder(s), {sent_75} 75% reminder(s)\n'
            f'  - {escalated} complaint(s) auto-escalated'
        ))
