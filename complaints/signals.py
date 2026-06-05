"""
Signals for the audit trail (Section 10 of the design).

When a tracked model (Complaint, ComplaintAction, ComplaintNote, EscalationLog,
SatisfactionSurvey) is saved, this module records what changed, by whom, and
when.  The records are immutable: AuditEntry.save() does not overwrite an
existing row, and a periodic retention-cleanup command prunes rows older
than 7 years.
"""
import json
import threading

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.forms.models import model_to_dict
from django.utils import timezone

from .models import (
    AuditEntry,
    Complaint,
    ComplaintAction,
    ComplaintNote,
    EscalationLog,
    SatisfactionSurvey,
    User,
)

# Tracked fields per model (to avoid logging noise from auto_now / auto_now_add)
TRACKED_FIELDS = {
    Complaint: [
        'case_number', 'status', 'priority', 'scheme_id', 'complaint_type_id',
        'location_id', 'assigned_to_id', 'assigned_by_id', 'complaint_title',
        'description', 'complaint_source', 'full_name', 'phone_number', 'email',
        'national_id', 'member_number', 'bank_institution', 'sla_hours',
        'sla_deadline', 'escalation_level', 'final_resolution', 'is_reopened',
        'satisfaction_rating',
    ],
    ComplaintAction: ['action_taken', 'action_description', 'previous_status', 'new_status'],
    ComplaintNote: ['note'],
    EscalationLog: ['level', 'trigger_reason', 'notes', 'resolved_at', 'resolution_notes'],
    SatisfactionSurvey: ['rating', 'comments'],
}

# Thread-local request cache (set by AuditMiddleware when available)
_local = threading.local()


def get_current_actor():
    """Return (User, ip, user_agent) for the in-flight request, if any."""
    return getattr(_local, 'actor', None), getattr(_local, 'ip', None), getattr(_local, 'ua', '')


class AuditMiddleware:
    """
    Captures the current request's user + IP + UA into thread-local storage
    so signals can attribute the change.  Safe to add/remove from MIDDLEWARE.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            _local.actor = user
        else:
            _local.actor = None
        # Honour X-Forwarded-For if behind a proxy
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            _local.ip = xff.split(',')[0].strip()
        else:
            _local.ip = request.META.get('REMOTE_ADDR')
        _local.ua = request.META.get('HTTP_USER_AGENT', '')[:255]
        try:
            response = self.get_response(request)
        finally:
            _local.actor = None
            _local.ip = None
            _local.ua = ''
        return response


# Pre-save: snapshot the old row so post_save can diff
_old_state = {}


@receiver(pre_save)
def _capture_old_state(sender, instance, **kwargs):
    if sender not in TRACKED_FIELDS:
        return
    if not instance.pk:
        return
    try:
        old = sender.objects.get(pk=instance.pk)
        _old_state[(sender, instance.pk)] = {
            f.name: getattr(old, f.attname) for f in sender._meta.fields
        }
    except sender.DoesNotExist:
        pass


@receiver(post_save)
def _record_audit_entry(sender, instance, created, **kwargs):
    if sender not in TRACKED_FIELDS:
        return
    actor, ip, ua = get_current_actor()
    label = instance._meta.label_lower
    content_type = f'{sender._meta.app_label}.{sender.__name__}'
    obj_repr = str(instance)[:255]

    if created:
        AuditEntry.objects.create(
            content_type=content_type,
            object_id=instance.pk,
            object_repr=obj_repr,
            action=AuditEntry.ACTION_CREATE,
            field_name='',
            old_value='',
            new_value=_summarize_instance(instance, TRACKED_FIELDS[sender]),
            actor=actor if isinstance(actor, User) else None,
            actor_label=str(actor) if actor else 'system',
            ip_address=ip,
            user_agent=ua,
        )
        return

    old = _old_state.pop((sender, instance.pk), None)
    if not old:
        return

    for field in TRACKED_FIELDS[sender]:
        old_val = old.get(field)
        new_val = getattr(instance, field, None)
        if old_val != new_val:
            AuditEntry.objects.create(
                content_type=content_type,
                object_id=instance.pk,
                object_repr=obj_repr,
                action=AuditEntry.ACTION_UPDATE,
                field_name=field,
                old_value=_truncate(repr(old_val)),
                new_value=_truncate(repr(new_val)),
                actor=actor if isinstance(actor, User) else None,
                actor_label=str(actor) if actor else 'system',
                ip_address=ip,
                user_agent=ua,
            )


def _truncate(value, maxlen=2000):
    if value is None:
        return ''
    s = str(value)
    return s if len(s) <= maxlen else s[:maxlen] + '... (truncated)'


def _summarize_instance(instance, fields):
    """Compact JSON snapshot for 'create' audit rows."""
    snapshot = {}
    for f in fields:
        snapshot[f] = str(getattr(instance, f, ''))[:500]
    return json.dumps(snapshot, default=str)
