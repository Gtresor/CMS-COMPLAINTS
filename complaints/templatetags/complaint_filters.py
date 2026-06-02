from django import template

register = template.Library()

@register.filter
def replace(value, args):
    old, new = args.split(",")
    return str(value).replace(old.strip(), new.strip())

@register.filter
def status_badge_class(value):
    mapping = {
        'new':          'badge-new',
        'under_review': 'badge-review',
        'assigned':     'badge-assigned',
        'in_progress':  'badge-progress',
        'pending_info': 'badge-pending',
        'resolved':     'badge-resolved',
        'closed':       'badge-closed',
        'overdue':      'badge-overdue',
    }
    return mapping.get(str(value).lower(), 'badge-new')

@register.filter
def priority_badge_class(value):
    mapping = {
        'low':      'badge-low',
        'medium':   'badge-medium',
        'high':     'badge-high',
        'critical': 'badge-critical',
    }
    return mapping.get(str(value).lower(), 'badge-low')