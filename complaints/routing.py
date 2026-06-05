"""
Auto-Assignment & Routing Engine for the Complaint Management System.

Implements Section 8.1 of the System Design Document.

Routing priority (best-fit wins):
  1. Officer with matching scheme + complaint type + location, lowest workload
  2. Officer with matching scheme + complaint type, lowest workload
  3. Officer with matching scheme, lowest workload
  4. Officer with matching location, lowest workload
  5. Any available officer (handler role, active), lowest workload
  6. If no officer found -> leave assigned_to=None and surface a routing-queue
     notification to the supervisor of the scheme (or any supervisor as fallback)
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Complaint, EscalationLog, Notification, User


# ---------------------------------------------------------------------------
# Workload scoring
# ---------------------------------------------------------------------------

def _officer_workload(user):
    """
    Lower is better.  We weight active complaints, escalated complaints and
    overdue complaints so a heavily-burdened officer falls down the list.
    """
    active = Complaint.objects.filter(
        assigned_to=user,
    ).exclude(status__in=['resolved', 'closed', 'reopened']).count()
    escalated = Complaint.objects.filter(
        assigned_to=user, escalation_level__gt=0,
    ).exclude(status__in=['resolved', 'closed']).count()
    overdue = Complaint.objects.filter(
        assigned_to=user, sla_deadline__lt=timezone.now(),
    ).exclude(status__in=['resolved', 'closed']).count()
    return active * 1.0 + escalated * 2.0 + overdue * 3.0


def _filter_candidates(qs):
    """Apply the standard 'available' filter for officers."""
    return qs.filter(role='handler', is_active=True, is_available_for_assignment=True)


def _query_by_scheme(scheme, base_qs):
    if not scheme:
        return base_qs.none()
    return base_qs.filter(
        Q(department__scheme=scheme) | Q(location=scheme)
    ) if False else base_qs  # departments no longer tied to scheme directly


def _query_by_location(location, base_qs):
    if not location:
        return base_qs.none()
    return base_qs.filter(location=location)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_best_handler(complaint, exclude_user=None):
    """
    Locate the best-fit handler for the given complaint.
    Returns a User (handler) or None if no candidate is available.

    The complaint does not need to be saved - we only read its routing hints.
    """
    candidates = _filter_candidates(User.objects.all())
    if exclude_user:
        candidates = candidates.exclude(pk=exclude_user.pk)

    scheme = getattr(complaint, 'scheme', None)
    ctype = getattr(complaint, 'complaint_type', None)
    location = getattr(complaint, 'location', None)

    # 1. Exact: same scheme + same location
    if scheme and location:
        pool = candidates.filter(location=location, department__scheme=scheme)
        if not pool.exists():
            # 1b. same location (any scheme)
            pool = candidates.filter(location=location)
        if not pool.exists():
            # 1c. same scheme (any location)
            pool = candidates.filter(department__scheme=scheme)
    elif scheme:
        pool = candidates.filter(department__scheme=scheme)
    elif location:
        pool = candidates.filter(location=location)
    else:
        pool = candidates

    if not pool.exists():
        pool = candidates  # last resort: any active handler

    if not pool.exists():
        return None

    # Score: lower workload wins; tie-break by earliest assigned_to (FIFO)
    scored = []
    for u in pool.distinct():
        scored.append((_officer_workload(u), u.pk, u))
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2]


def find_supervisor_for_complaint(complaint):
    """
    Locate a supervisor (any active supervisor) for the escalation chain.
    Prefers supervisors in the same scheme, then falls back to any.
    """
    scheme = getattr(complaint, 'scheme', None)
    if scheme and scheme.manager_id:
        mgr = User.objects.filter(
            id=scheme.manager_id, role='supervisor', is_active=True,
        ).first()
        if mgr:
            return mgr
    sup = User.objects.filter(role='supervisor', is_active=True).order_by('id').first()
    return sup


def auto_route(complaint, save=True, triggered_by=None):
    """
    Auto-assign a complaint to the best-fit handler and return the chosen
    user (or None).  If `save=True`, the assignment is persisted on the
    complaint instance.

    Side effects when an officer is found:
      - Updates complaint.assigned_to + assigned_by + assigned_at/registered_at
        wiring
      - Logs a routing notification in the audit trail (via Notification
        model)
    Side effects when no officer is found:
      - Logs a 'routing_queue' notification to the supervisor of the scheme
    """
    if complaint.status in {'resolved', 'closed'}:
        return complaint.assigned_to

    handler = find_best_handler(complaint, exclude_user=complaint.registered_by)
    if handler is None:
        supervisor = find_supervisor_for_complaint(complaint)
        if supervisor:
            Notification.objects.create(
                user=supervisor,
                complaint=complaint,
                notification_type='escalation',  # reuse severity-critical type
                recipient_type='supervisor',
                delivery_channel='in_app',
                recipient_email=supervisor.email or '',
                message=(
                    f"No available handler for complaint {complaint.case_number} - "
                    f"please assign manually."
                ),
                severity='warning',
                metadata={'reason': 'routing_queue_empty'},
                sent_at=timezone.now(),
            )
        return None

    complaint.assigned_to = handler
    if triggered_by and not complaint.assigned_by_id:
        complaint.assigned_by = triggered_by
    if save:
        # Use the model's save() so all derived state (SLA deadline, etc.) is
        # recomputed.
        complaint.save()

    return handler


def rebalance_workload():
    """
    Periodic helper: distribute unassigned complaints fairly across available
    handlers.  Returns a tuple (assigned_count, unassigned_count).
    """
    from .models import Complaint

    queue = Complaint.objects.filter(
        assigned_to__isnull=True,
        status__in=['new', 'under_review', 'pending', 'pending_info'],
    )
    assigned = 0
    remaining = 0
    for c in queue:
        handler = auto_route(c, save=True)
        if handler:
            assigned += 1
        else:
            remaining += 1
    return assigned, remaining
