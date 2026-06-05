"""
Management command to enforce 7-year retention on the audit trail.

Usage:
    python manage.py cleanup_old_audit_logs
    python manage.py cleanup_old_audit_logs --dry-run
    python manage.py cleanup_old_audit_logs --years 7
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from complaints.models import AuditEntry


class Command(BaseCommand):
    help = 'Delete audit entries older than the configured retention window (default 7 years).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--years',
            type=int,
            default=7,
            help='Retention period in years (default 7 per Section 10 of the design).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be deleted without actually deleting.',
        )

    def handle(self, *args, **options):
        years = options.get('years', 7)
        dry_run = options.get('dry_run', False)
        cutoff = timezone.now() - timedelta(days=365 * years)
        qs = AuditEntry.objects.filter(timestamp__lt=cutoff)
        count = qs.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                f'No audit entries older than {years} years (cutoff {cutoff:%Y-%m-%d}).'
            ))
            return
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[DRY-RUN] Would delete {count} audit entry(ies) older than {cutoff:%Y-%m-%d %H:%M}.'
            ))
            return
        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {count} audit entry(ies) older than {years} years (cutoff {cutoff:%Y-%m-%d %H:%M}).'
        ))
