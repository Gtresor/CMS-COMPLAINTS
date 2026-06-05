"""
Escalation service for the Complaint Management System.

Implements Section 9 of the System Design Document - the 4-level escalation matrix:

  Level 1 - Supervisor       (0-24 hrs overdue)
  Level 2 - Manager          (24-48 hrs overdue, or complexity flag)
  Level 3 - Director         (48-72 hrs overdue, or Manager escalation)
  Level 4 - Senior Mgmt      (>72 hrs overdue, media/legal risk, Director escalation)

Auto-trigger rules:
  - SLA deadline reached + not resolved  →  escalate one level
  - Each subsequent overdue window       →  escalate again (capped at level 4)
  - Manual escalation by a manager        →  jump directly to a target level

Resolution:
  - An EscalationLog is "resolved" when the complaint is moved out of
    'escalated' status (e.g., resolved, closed, or re-opened at lower level).
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Complaint, EscalationLog, Notification, User


# ---------------------------------------------------------------------------
# Escalation matrix (Section 9)
# ---------------------------------------------------------------------------

ESCALATION_MATRIX = {
    1: {
        'role': 'supervisor',
        'label': 'Level 1 - Supervisor',
        'trigger_after_overdue_hours': 0,    # 0-24 hrs overdue
        'overdue_window_hours': 24,
    },
    2: {
        'role': 'manager',
        'label': 'Level 2 - Manager',
        'trigger_after_overdue_hours': 24,   # 24-48 hrs overdue
        'overdue_window_hours': 24,
    },
    3: {
        'role': 'director',
        'label': 'Level 3 - Director',
        'trigger_after_overdue_hours': 48,   # 48-72 hrs overdue
        'overdue_window_hours': 24,
    },
    4: {
        'role': 'director',  # Senior management - falls back to director
        'label': 'Level 4 - Senior Management',
        'trigger_after_overdue_hours': 72,   # > 72 hrs overdue
        'overdue_window_hours': None,
    },
}


def _compute_overdue_hours(complaint):
    """Return the number of whole hours the complaint has been overdue (0 if not overdue)."""
    if not complaint.sla_deadline:
        return 0
    if complaint.status in {'resolved', 'closed', 'reopened'}:
        return 0
    if complaint.clock_paused:
        return 0
    delta = timezone.now() - complaint.sla_deadline
    return max(0, int(delta.total_seconds() // 3600))


def _find_escalation_target(complaint, role):
    """
    Find the user to escalate to.  Resolution order:
      1. Complaint's assigned officer's supervisor
      2. User with the target role in the same scheme
      3. Any active user with the target role
      4. None
    """
    qs = User.objects.filter(role=role, is_active=True)

    # 1. Supervisor of the assigned officer
    if complaint.assigned_to and complaint.assigned_to.supervisor_id:
        supervisor = User.objects.filter(
            id=complaint.assigned_to.supervisor_id, is_active=True,
        ).first()
        if supervisor and (supervisor.role == role or role == 'supervisor'):
            return supervisor

    # 2. Same scheme
    if complaint.scheme and complaint.scheme.manager_id:
        mgr = User.objects.filter(
            id=complaint.scheme.manager_id, is_active=True,
        ).first()
        if mgr and mgr.role == role:
            return mgr

    # 3. Any user with the role
    return qs.first()


def hours_until_next_escalation(complaint):
    """
    Returns the number of hours until the next escalation level should fire.
    Returns None if the complaint is already at max level (4) or terminal.
    """
    if complaint.status in {'resolved', 'closed'}:
        return None
    if complaint.clock_paused:
        return None

    current_level = complaint.escalation_level
    next_level = current_level + 1
    if next_level > 4:
        return None

    if not complaint.is_overdue:
        # Not yet overdue - how long until the SLA breach?
        if not complaint.sla_deadline:
            return None
        delta = complaint.sla_deadline - timezone.now()
        return max(0, int(delta.total_seconds() // 3600))

    overdue = _compute_overdue_hours(complaint)
    level_cfg = ESCALATION_MATRIX[next_level]
    threshold = level_cfg['trigger_after_overdue_hours']
    return max(0, threshold - overdue)


@transaction.atomic
def escalate_complaint(complaint, reason='overdue', triggered_by=None, target_level=None):
    """
    Escalate a complaint by one level (or to ``target_level`` if given).
    Returns the EscalationLog created, or None if the complaint is already at
    the maximum level / terminal status.
    """
    if complaint.status in {'resolved', 'closed'}:
        return None

    current_level = complaint.escalation_level
    next_level = target_level if target_level else current_level + 1
    if next_level > 4:
        return None  # already at max
    if next_level <= current_level:
        next_level = current_level + 1

    level_cfg = ESCALATION_MATRIX.get(next_level)
    if not level_cfg:
        return None

    target = _find_escalation_target(complaint, level_cfg['role'])

    log = EscalationLog.objects.create(
        complaint=complaint,
        level=next_level,
        trigger_reason=reason,
        escalated_to=target,
        escalated_by=triggered_by,
        notes=f"Auto-escalated: {reason}" if triggered_by is None else f"Manual: {reason}",
    )

    # Update the complaint
    complaint.escalation_level = next_level
    if complaint.status != 'escalated':
        complaint.status = 'escalated'
    complaint._last_changer = triggered_by
    complaint.save(update_fields=['escalation_level', 'status'])

    # In-app notification
    if target:
        Notification.objects.create(
            user=target,
            performed_by=triggered_by,
            complaint=complaint,
            notification_type='escalation',
            recipient_type=level_cfg['role'],
            delivery_channel='in_app',
            recipient_email=target.email or '',
            message=(
                f"Complaint {complaint.case_number} escalated to "
                f"{level_cfg['label']} - {reason}"
            ),
            severity='critical',
            metadata={
                'escalation_level': next_level,
                'trigger_reason': reason,
                'escalation_id': log.pk,
            },
            sent_at=timezone.now(),
        )

    return log


def resolve_escalation(complaint, resolved_by=None, notes=''):
    """Mark all open escalations on this complaint as resolved."""
    open_logs = complaint.escalation_logs.filter(resolved_at__isnull=True)
    now = timezone.now()
    for log in open_logs:
        log.resolved_at = now
        log.resolved_by = resolved_by
        log.resolution_notes = notes
        log.save(update_fields=['resolved_at', 'resolved_by', 'resolution_notes'])


def reset_escalation_level(complaint, save=True):
    """Reset the escalation level when a complaint is resolved/closed."""
    if complaint.escalation_level != 0:
        complaint.escalation_level = 0
        if save:
            complaint.save(update_fields=['escalation_level'])
    resolve_escalation(complaint, notes='Auto-resolved on status change')


def run_auto_escalation(dry_run=False):
    """
    Scan all active complaints and trigger escalations as needed.
    Returns a tuple (escalated_count, skipped_count).
    """
    active_statuses = ['new', 'under_review', 'assigned', 'in_progress',
                       'pending_info', 'pending', 'escalated']
    escalated = 0
    skipped = 0

    qs = Complaint.objects.filter(
        status__in=active_statuses,
        escalation_level__lt=4,
    ).select_related('assigned_to', 'scheme')

    for complaint in qs:
        overdue = _compute_overdue_hours(complaint)
        if overdue <= 0:
            skipped += 1
            continue

        # Find the highest level we *should* be at given the overdue hours
        target_level = 0
        for level in (1, 2, 3, 4):
            cfg = ESCALATION_MATRIX[level]
            if overdue >= cfg['trigger_after_overdue_hours']:
                target_level = level

        if target_level <= complaint.escalation_level:
            skipped += 1
            continue

        if dry_run:
            escalated += 1
            continue

        log = escalate_complaint(
            complaint,
            reason=f'overdue_{overdue}h',
        )
        if log:
            escalated += 1
        else:
            skipped += 1

    return escalated, skipped


# ---------------------------------------------------------------------------
# SLA reminder helpers (Section 8.2)
# ---------------------------------------------------------------------------

def check_sla_reminders(dry_run=False):
    """
    Walk all active complaints and:
      - create 'sla_reminder_50' notifications when 50% of the SLA window has
        elapsed and the complaint is not yet resolved
      - create 'sla_reminder_75' notifications when 75% has elapsed

    Idempotent: will not duplicate-fire on the same day.
    """
    active_statuses = ['new', 'under_review', 'assigned', 'in_progress',
                       'pending_info', 'pending', 'escalated']
    qs = Complaint.objects.filter(
        status__in=active_statuses,
    ).select_related('assigned_to', 'scheme')

    sent_50 = 0
    sent_75 = 0
    today = timezone.localdate()

    for complaint in qs:
        pct = complaint.sla_pct_consumed
        if pct < 50 or not complaint.sla_hours:
            continue

        # Skip if clock is paused
        if complaint.clock_paused:
            continue

        if 50 <= pct < 75 and not _already_notified_today(complaint, 'sla_reminder_50', today):
            if not dry_run:
                _send_sla_reminder(complaint, 'sla_reminder_50', 50)
            sent_50 += 1
        elif pct >= 75 and not _already_notified_today(complaint, 'sla_reminder_75', today):
            if not dry_run:
                _send_sla_reminder(complaint, 'sla_reminder_75', 75)
            sent_75 += 1

    return sent_50, sent_75


def _already_notified_today(complaint, ntype, today):
    return Notification.objects.filter(
        complaint=complaint,
        notification_type=ntype,
        created_at__date=today,
    ).exists()


def _send_sla_reminder(complaint, ntype, percentage):
    """Send SLA reminder to the assigned officer + supervisor."""
    recipients = []
    if complaint.assigned_to:
        recipients.append((complaint.assigned_to, 'handler'))
        if complaint.assigned_to.supervisor_id:
            sup = User.objects.filter(id=complaint.assigned_to.supervisor_id).first()
            if sup:
                recipients.append((sup, 'supervisor'))

    if not recipients:
        # Fallback: notify all admins
        for u in User.objects.filter(role='admin', is_active=True):
            recipients.append((u, 'admin'))

    severity = 'warning' if percentage == 50 else 'critical'
    message = (
        f"SLA {percentage}% consumed for {complaint.case_number}. "
        f"{complaint.hours_remaining}h remaining "
        f"(deadline {complaint.sla_deadline:%Y-%m-%d %H:%M})."
    )

    for user, role in recipients:
        Notification.objects.create(
            user=user,
            complaint=complaint,
            notification_type=ntype,
            recipient_type=role,
            delivery_channel='in_app',
            recipient_email=user.email or '',
            message=message,
            severity=severity,
            metadata={
                'percentage': percentage,
                'sla_deadline': str(complaint.sla_deadline) if complaint.sla_deadline else '',
                'hours_remaining': complaint.hours_remaining,
            },
            sent_at=timezone.now(),
        )
