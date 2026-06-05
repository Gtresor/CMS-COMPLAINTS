"""
Data migration to seed the 3 schemes, 10 complaint types, default locations,
and default SLA configurations from the System Design Document.

This migration is idempotent: re-running it on a populated database is a no-op.
"""
from django.db import migrations


# -----------------------------------------------------------------------------
# Seed data definitions (Section 2, 3, 4, 14 of the design)
# -----------------------------------------------------------------------------

SCHEMES = [
    {
        'code': 'PENSION',
        'name': 'Pension Scheme',
        'description': 'Old-age, invalidity, and survivors\' benefits for registered members.',
    },
    {
        'code': 'OCCUPATIONAL_HAZARDS',
        'name': 'Occupational Hazards Scheme',
        'description': 'Work-related injury and disease compensation benefits.',
    },
    {
        'code': 'MATERNITY_LEAVE',
        'name': 'Maternity Leave Benefits Scheme',
        'description': 'Maternity benefit payments to eligible female members.',
    },
]

# Section 4.1 - 10 complaint types with applicable schemes
# priority / sla-hours reflects the *default* (auto-suggested) per Section 4.2
COMPLAINT_TYPES = [
    ('CT-01', 'Delayed Payment',                        'high',     120, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-02', 'Benefit Calculation Issue',              'medium',   240, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-03', 'Missing or Incorrect Contribution',      'medium',   240, ['PENSION', 'OCCUPATIONAL_HAZARDS']),
    ('CT-04', 'Employer-Related Issue',                  'high',     120, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-05', 'Medical Assessment Issue',                'high',     120, ['OCCUPATIONAL_HAZARDS']),
    ('CT-06', 'Claim Rejection',                         'medium',   240, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-07', 'Documentation / Administrative Issue',    'low',      360, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-08', 'Service Delivery Complaint',              'low',      360, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-09', 'Appeal',                                  'high',     120, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
    ('CT-10', 'Other / General Enquiry',                 'low',      360, ['PENSION', 'OCCUPATIONAL_HAZARDS', 'MATERNITY_LEAVE']),
]

LOCATIONS = [
    ('Head Office',         'HO'),
    ('Kigali Branch',       'KGL'),
    ('Butare Branch',       'BTR'),
    ('Gisenyi Branch',      'GSY'),
    ('Ruhengeri Branch',    'RHG'),
    ('Cyangugu Branch',     'CYG'),
]

# Section 4.2 - Default SLA hours per priority
SLA_CONFIGS = [
    # (priority, max_hours, pause_on_pending)
    ('critical', 48,  True),
    ('high',     120, True),
    ('medium',   240, True),
    ('low',      360, True),
]


def seed_schemes(apps, schema_editor):
    Scheme = apps.get_model('complaints', 'Scheme')
    for data in SCHEMES:
        Scheme.objects.get_or_create(
            code=data['code'],
            defaults={'name': data['name'], 'description': data['description']},
        )


def seed_complaint_types(apps, schema_editor):
    Scheme = apps.get_model('complaints', 'Scheme')
    ComplaintType = apps.get_model('complaints', 'ComplaintType')

    for code, name, priority, hours, scheme_codes in COMPLAINT_TYPES:
        ct, created = ComplaintType.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'default_priority': priority,
                'default_sla_hours': hours,
            },
        )
        # Link applicable schemes
        for sc in scheme_codes:
            scheme = Scheme.objects.filter(code=sc).first()
            if scheme:
                ct.applicable_schemes.add(scheme)


def seed_locations(apps, schema_editor):
    Location = apps.get_model('complaints', 'Location')
    for name, code in LOCATIONS:
        Location.objects.get_or_create(
            name=name,
            defaults={'region_code': code},
        )


def seed_sla_configs(apps, schema_editor):
    SLAConfiguration = apps.get_model('complaints', 'SLAConfiguration')
    for priority, hours, pause in SLA_CONFIGS:
        SLAConfiguration.objects.get_or_create(
            priority=priority,
            scheme=None,
            complaint_type=None,
            defaults={
                'max_hours': hours,
                'pause_on_pending': pause,
                'business_hours_only': False,
                'reminder_50_percent': True,
                'reminder_75_percent': True,
                'is_active': True,
            },
        )


def reverse_seed(apps, schema_editor):
    """Reverse: remove seeded data (kept for symmetry)."""
    Scheme = apps.get_model('complaints', 'Scheme')
    ComplaintType = apps.get_model('complaints', 'ComplaintType')
    Location = apps.get_model('complaints', 'Location')
    SLAConfiguration = apps.get_model('complaints', 'SLAConfiguration')

    SLAConfiguration.objects.filter(complaint_type__isnull=True, scheme__isnull=True).delete()
    ComplaintType.objects.filter(code__in=[ct[0] for ct in COMPLAINT_TYPES]).delete()
    Scheme.objects.filter(code__in=[s['code'] for s in SCHEMES]).delete()
    Location.objects.filter(name__in=[l[0] for l in LOCATIONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('complaints', '0007_complainttype_escalationlog_location_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_schemes, reverse_code=reverse_seed),
        migrations.RunPython(seed_complaint_types, reverse_code=migrations.RunPython.noop),
        migrations.RunPython(seed_locations, reverse_code=migrations.RunPython.noop),
        migrations.RunPython(seed_sla_configs, reverse_code=migrations.RunPython.noop),
    ]
