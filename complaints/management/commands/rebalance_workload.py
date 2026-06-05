"""
Management command to rebalance unassigned complaints across available handlers.

Usage:
    python manage.py rebalance_workload
    python manage.py rebalance_workload --dry-run
"""
from django.core.management.base import BaseCommand

from complaints.routing import rebalance_workload


class Command(BaseCommand):
    help = 'Distribute unassigned complaints fairly across available handlers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be done without making any changes.',
        )

    def handle(self, *args, **options):
        if options.get('dry_run'):
            from complaints.routing import find_best_handler
            from complaints.models import Complaint

            queue = Complaint.objects.filter(
                assigned_to__isnull=True,
                status__in=['new', 'under_review', 'pending', 'pending_info'],
            )
            self.stdout.write(self.style.SUCCESS(
                f'[DRY-RUN] Would attempt to assign {queue.count()} complaint(s):'
            ))
            for c in queue[:10]:
                h = find_best_handler(c)
                if h:
                    self.stdout.write(
                        f'  - {c.case_number} -> {h.username} ({"available"})'
                    )
                else:
                    self.stdout.write(
                        f'  - {c.case_number} -> NO AVAILABLE HANDLER (would notify supervisor)'
                    )
            return

        assigned, remaining = rebalance_workload()
        self.stdout.write(self.style.SUCCESS(
            f'Rebalance complete: {assigned} complaint(s) assigned, {remaining} still in queue.'
        ))
