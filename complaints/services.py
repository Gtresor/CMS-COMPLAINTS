from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from django.urls import reverse

from .models import Complaint, Notification, SystemConfiguration


def test_smtp_connection(config):
    """
    Attempt a real SMTP connection + login using the supplied SystemConfiguration.

    Returns a tuple ``(success: bool, message: str)``. The message is safe to
    display to the end user and is short (single line, no stack traces).
    """
    if not config.is_fully_configured:
        return False, 'Missing required SMTP fields (sender email, host, username, or password).'

    host = (config.smtp_host or '').strip()
    port = int(config.smtp_port or 587)
    username = (config.smtp_username or '').strip()
    password = config.smtp_password or ''

    # Local imports keep the module safe to import even on platforms that
    # don't ship smtplib in unusual embedded contexts.
    import smtplib
    import socket

    connection = None
    timeout = 10
    try:
        # Use SMTP_SSL for port 465, SMTP+STARTTLS for port 587
        if port == 465:
            connection = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            connection = smtplib.SMTP(host, port, timeout=timeout)
            connection.ehlo()
            if connection.has_extn('starttls'):
                connection.starttls()
                connection.ehlo()

        connection.login(username, password)
        try:
            connection.quit()
        except smtplib.SMTPException:
            pass
        return True, 'SMTP connection and login succeeded.'

    except smtplib.SMTPAuthenticationError as exc:
        return False, f'Authentication failed ({exc.smtp_code}): {exc.smtp_error.decode(errors="ignore") if exc.smtp_error else "invalid credentials"}.'
    except smtplib.SMTPConnectError as exc:
        return False, f'Could not connect to SMTP server: {exc}'
    except smtplib.SMTPServerDisconnected:
        return False, 'SMTP server disconnected unexpectedly.'
    except smtplib.SMTPSenderRefused as exc:
        return False, f'SMTP sender refused: {exc}'
    except smtplib.SMTPRecipientsRefused as exc:
        return False, f'SMTP recipients refused: {exc}'
    except smtplib.SMTPDataError as exc:
        return False, f'SMTP data error: {exc}'
    except smtplib.SMTPException as exc:
        return False, f'SMTP error: {exc}'
    except socket.gaierror:
        return False, f'Could not resolve SMTP host "{host}".'
    except socket.timeout:
        return False, f'Connection to {host}:{port} timed out.'
    except ConnectionRefusedError:
        return False, f'Connection to {host}:{port} was refused.'
    except OSError as exc:
        return False, f'Network error: {exc}'
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def record_smtp_test_result(config):
    """
    Run ``test_smtp_connection`` against ``config`` and persist the outcome
    to the same model instance. Returns the (success, message) tuple.
    """
    success, message = test_smtp_connection(config)
    config.last_test_passed = bool(success)
    config.last_test_at = timezone.now()
    config.last_test_message = message
    config.save(update_fields=['last_test_passed', 'last_test_at', 'last_test_message', 'updated_at'])
    return success, message


def notify_complaint_created(complaint, performed_by=None):
    """Notify all admins when a new complaint is registered."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admins = User.objects.filter(role='admin', is_active=True)
    message = f'New complaint registered: {complaint.case_number} - {complaint.complaint_title}'
    
    for admin in admins:
        Notification.objects.create(
            user=admin,
            performed_by=performed_by,
            complaint=complaint,
            notification_type='new_complaint',
            message=message,
            severity='info',
            metadata={'case_number': complaint.case_number, 'complaint_id': complaint.pk},
        )


def notify_complaint_assigned(complaint, assigned_by=None):
    """Notify the handler when a complaint is assigned to them and send email.
    Returns tuple (success: bool, message: str)
    """
    handler = complaint.assigned_to
    if not handler:
        return False, "No handler assigned"

    assigner_name = assigned_by.get_full_name() or assigned_by.username if assigned_by else 'System'
    message = f'New complaint assigned to you: {complaint.case_number} - {complaint.complaint_title} (by {assigner_name})'
    Notification.objects.create(
        user=handler,
        performed_by=assigned_by,
        complaint=complaint,
        notification_type='assignment',
        message=message,
        severity='info',
    )

    # Also notify admins
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admins = User.objects.filter(role='admin', is_active=True).exclude(pk=handler.pk)
    admin_message = f'Complaint {complaint.case_number} assigned to {handler.get_full_name() or handler.username} by {assigner_name}'
    for admin in admins:
        Notification.objects.create(
            user=admin,
            performed_by=assigned_by,
            complaint=complaint,
            notification_type='handler_assigned',
            message=admin_message,
            severity='info',
        )

    # Send email notification
    if handler.email:
        return _send_assignment_email(complaint, handler)
    else:
        return False, f"Handler {handler.username} has no email address"


def notify_status_update(complaint, updated_by):
    """Notify about complaint status updates.
    Admins always receive it. Handlers only receive it if the complaint is assigned to them.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    updater_name = updated_by.get_full_name() or updated_by.username
    message = f'Status update on {complaint.case_number}: {complaint.get_status_display()} (by {updater_name})'
    
    # Always notify all admins (except the updater)
    admins = User.objects.filter(is_active=True, role='admin').exclude(pk=updated_by.pk)
    for admin in admins:
        Notification.objects.create(
            user=admin,
            performed_by=updated_by,
            complaint=complaint,
            notification_type='status_update',
            message=message,
            severity='info',
            metadata={
                'case_number': complaint.case_number,
                'status': complaint.status,
                'updated_by_name': updater_name,
            },
        )
    
    # Also notify the complaint's assigned handler (if different from the updater)
    if complaint.assigned_to and complaint.assigned_to.pk != updated_by.pk:
        Notification.objects.create(
            user=complaint.assigned_to,
            performed_by=updated_by,
            complaint=complaint,
            notification_type='status_update',
            message=message,
            severity='info',
            metadata={
                'case_number': complaint.case_number,
                'status': complaint.status,
                'updated_by_name': updater_name,
            },
        )


def notify_complaint_updated(complaint, updated_by, changes=None):
    """Notify when a complaint is updated/edited.
    Admins always receive it. Handlers only receive it if the complaint is assigned to them.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    updater_name = updated_by.get_full_name() or updated_by.username
    message = f'Complaint {complaint.case_number} was updated by {updater_name}'
    
    if changes:
        change_details = ', '.join(changes)
        message += f' ({change_details})'

    # Always notify all admins (except the updater)
    admins = User.objects.filter(is_active=True, role='admin').exclude(pk=updated_by.pk)
    for admin in admins:
        Notification.objects.create(
            user=admin,
            performed_by=updated_by,
            complaint=complaint,
            notification_type='complaint_updated',
            message=message,
            severity='info',
            metadata={
                'case_number': complaint.case_number,
                'changes': changes or [],
                'updated_by_name': updater_name,
            },
        )
    
    # Also notify the complaint's assigned handler (if different from the updater)
    if complaint.assigned_to and complaint.assigned_to.pk != updated_by.pk:
        Notification.objects.create(
            user=complaint.assigned_to,
            performed_by=updated_by,
            complaint=complaint,
            notification_type='complaint_updated',
            message=message,
            severity='info',
            metadata={
                'case_number': complaint.case_number,
                'changes': changes or [],
                'updated_by_name': updater_name,
            },
        )


def notify_user_changed(performed_by, target_user, change_type):
    """Notify all admins when a user is created, updated, or deleted.
    change_type should be one of: 'user_created', 'user_updated', 'user_deleted'
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    performer_name = performed_by.get_full_name() or performed_by.username
    target_name = target_user.get_full_name() or target_user.username
    
    type_labels = {
        'user_created': 'created',
        'user_updated': 'updated',
        'user_deleted': 'deleted',
    }
    action_label = type_labels.get(change_type, change_type)
    severity = 'warning' if change_type == 'user_deleted' else 'info'
    
    message = f'User {target_name} ({target_user.role}) was {action_label} by {performer_name}'

    recipients = User.objects.filter(is_active=True, role='admin').exclude(pk=performed_by.pk)
    for user in recipients:
        Notification.objects.create(
            user=user,
            performed_by=performed_by,
            notification_type=change_type,
            message=message,
            severity=severity,
            metadata={
                'target_user_id': target_user.pk,
                'target_username': target_name,
                'target_role': target_user.role,
                'action': action_label,
            },
        )


def notify_sla_event(complaint, event_type):
    """Notify about SLA approaching or exceeded deadlines.
    event_type: 'sla_approaching' or 'sla_exceeded'
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    severity = 'warning' if event_type == 'sla_approaching' else 'critical'
    
    if event_type == 'sla_approaching':
        message = f'SLA deadline approaching for {complaint.case_number}: {complaint.days_remaining} day(s) remaining (due {complaint.sla_deadline})'
    else:
        message = f'SLA deadline EXCEEDED for {complaint.case_number}! Was due on {complaint.sla_deadline}'

    recipients = User.objects.filter(is_active=True)
    # Notify the assigned handler first (higher priority)
    if complaint.assigned_to:
        Notification.objects.create(
            user=complaint.assigned_to,
            complaint=complaint,
            notification_type=event_type,
            message=message,
            severity=severity,
            metadata={
                'case_number': complaint.case_number,
                'sla_deadline': str(complaint.sla_deadline) if complaint.sla_deadline else '',
                'days_remaining': complaint.days_remaining,
            },
        )
    
    # Notify all other active users except the handler
    exclude_pk = complaint.assigned_to.pk if complaint.assigned_to else None
    for user in recipients.exclude(pk=exclude_pk):
        if complaint.assigned_to and user.pk == complaint.assigned_to.pk:
            continue
        Notification.objects.create(
            user=user,
            performed_by=complaint.assigned_to,
            complaint=complaint,
            notification_type=event_type,
            message=message,
            severity=severity,
            metadata={
                'case_number': complaint.case_number,
                'sla_deadline': str(complaint.sla_deadline) if complaint.sla_deadline else '',
            },
        )


def _send_assignment_email(complaint, handler):
    """Send an HTML email to the handler about the assigned complaint.
    Returns tuple (success: bool, message: str)
    """
    config = SystemConfiguration.get_settings()

    sender_email = config.sender_email or settings.DEFAULT_FROM_EMAIL
    sender_name = config.sender_name or 'RSSB Complaints System'
    use_custom_smtp = bool(config.smtp_host and config.smtp_username and config.smtp_password)
    use_default_backend = bool(
        getattr(settings, 'EMAIL_BACKEND', None)
        and (
            settings.EMAIL_BACKEND != 'django.core.mail.backends.smtp.EmailBackend'
            or getattr(settings, 'EMAIL_HOST', None)
        )
    )

    if not use_custom_smtp and not use_default_backend:
        return False, 'Email service not configured'

    subject = f'New Complaint Assigned: {complaint.case_number}'

    handle_url = f'{settings.SITE_URL}{reverse("complaint-handle", kwargs={"pk": complaint.pk})}' if hasattr(settings, 'SITE_URL') else None

    context = {
        'complaint': complaint,
        'handler': handler,
        'sender_name': sender_name,
        'handle_url': handle_url,
    }

    try:
        html_message = render_to_string('emails/complaint_assigned.html', context)
        plain_message = strip_tags(html_message)

        handler_display = handler.get_full_name() or handler.username

        if use_custom_smtp:
            from django.core.mail import EmailMultiAlternatives, get_connection

            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=f'{sender_name} <{sender_email}>',
                to=[handler.email],
            )
            msg.attach_alternative(html_message, 'text/html')

            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config.smtp_host,
                port=config.smtp_port,
                username=config.smtp_username,
                password=config.smtp_password,
                use_tls=config.use_tls,
            )
            msg.connection = connection
            try:
                msg.send()
                return True, f"Email sent successfully to {handler_display} ({handler.email})"
            except Exception:
                if not use_default_backend:
                    raise

        if use_default_backend:
            from django.core.mail import send_mail
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=sender_email,
                recipient_list=[handler.email],
                html_message=html_message,
                fail_silently=False,
            )
            return True, f"Email sent successfully to {handler_display} ({handler.email})"

        return False, 'Email service not configured'

    except Exception as e:
        error_msg = f"Failed to send email to handler: {str(e)[:100]}"
        return False, error_msg


# ---------------------------------------------------------------------------
# Phase 4: Complainant-facing notifications (Section 8.2 of the design)
# ---------------------------------------------------------------------------

def _send_complainant_email(complaint, subject, template_name, context, ntype):
    """
    Internal helper - send an email to the complainant (if email is on file)
    and log a Notification row with recipient_type='complainant'.

    Returns (success: bool, message: str).
    """
    if not complaint.email:
        return False, 'No email on file for this complainant.'

    config = SystemConfiguration.get_settings()
    sender_email = config.sender_email or settings.DEFAULT_FROM_EMAIL
    sender_name = config.sender_name or 'RSSB Complaints System'

    use_custom_smtp = bool(config.smtp_host and config.smtp_username and config.smtp_password)
    if not use_custom_smtp and not getattr(settings, 'EMAIL_HOST', None):
        return False, 'Email service not configured'

    try:
        html_message = render_to_string(template_name, context)
        plain_message = strip_tags(html_message)

        if use_custom_smtp:
            from django.core.mail import EmailMultiAlternatives, get_connection
            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=f'{sender_name} <{sender_email}>',
                to=[complaint.email],
            )
            msg.attach_alternative(html_message, 'text/html')
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config.smtp_host,
                port=config.smtp_port,
                username=config.smtp_username,
                password=config.smtp_password,
                use_tls=config.use_tls,
            )
            msg.connection = connection
            msg.send()
        else:
            from django.core.mail import send_mail
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=sender_email,
                recipient_list=[complaint.email],
                html_message=html_message,
                fail_silently=False,
            )

        # Log the delivery
        Notification.objects.create(
            user=None,
            complaint=complaint,
            notification_type=ntype,
            recipient_type='complainant',
            delivery_channel='email',
            recipient_email=complaint.email,
            message=subject,
            severity='info',
            metadata={'template': template_name},
            sent_at=timezone.now(),
            delivered_at=timezone.now(),
        )
        return True, f'Email sent to {complaint.email}'

    except Exception as e:
        # Log failure
        Notification.objects.create(
            user=None,
            complaint=complaint,
            notification_type=ntype,
            recipient_type='complainant',
            delivery_channel='email',
            recipient_email=complaint.email or '',
            message=subject,
            severity='warning',
            failure_reason=str(e)[:200],
            metadata={'template': template_name},
            sent_at=timezone.now(),
        )
        return False, f'Failed: {str(e)[:100]}'


def notify_complaint_acknowledged(complaint, performed_by=None):
    """
    Step 3 of the workflow (Section 7.2): send acknowledgement to the
    complainant when the case officer starts the review.

    Returns (success, message).
    """
    config = SystemConfiguration.get_settings()
    if not config.enable_complainant_emails:
        return False, 'Complainant emails are disabled in system configuration'

    context = {
        'complaint': complaint,
        'complainant_name': complaint.full_name or complaint.complainant_name or 'Member',
        'case_number': complaint.case_number,
        'handler_name': complaint.assigned_to.get_full_name() if complaint.assigned_to else 'our team',
        'scheme': complaint.scheme.name if complaint.scheme else '',
    }
    subject = f'Complaint {complaint.case_number} - Acknowledgement of Receipt'
    return _send_complainant_email(
        complaint, subject, 'emails/complaint_acknowledged.html', context,
        'complainant_acknowledgement',
    )


def notify_complaint_status_to_complainant(complaint, performed_by=None, status_label=None):
    """
    Section 8.2 - send a status-update email to the complainant whenever the
    complaint status changes (and the complainant has an email on file).
    """
    config = SystemConfiguration.get_settings()
    if not config.enable_complainant_emails:
        return False, 'Complainant emails are disabled'

    context = {
        'complaint': complaint,
        'complainant_name': complaint.full_name or complaint.complainant_name or 'Member',
        'case_number': complaint.case_number,
        'status_label': status_label or complaint.get_status_display(),
        'handler_name': complaint.assigned_to.get_full_name() if complaint.assigned_to else 'our team',
    }
    subject = f'Complaint {complaint.case_number} - Status Update: {context["status_label"]}'
    return _send_complainant_email(
        complaint, subject, 'emails/complaint_status_update.html', context,
        'complainant_status_update',
    )


def notify_complaint_resolved(complaint, performed_by=None):
    """Section 7.2 Step 6 - send resolution confirmation to the complainant."""
    config = SystemConfiguration.get_settings()
    if not config.enable_complainant_emails:
        return False, 'Complainant emails are disabled'

    context = {
        'complaint': complaint,
        'complainant_name': complaint.full_name or complaint.complainant_name or 'Member',
        'case_number': complaint.case_number,
        'resolution': complaint.final_resolution or 'Your complaint has been resolved.',
        'resolution_proof_url': complaint.resolution_proof.url if complaint.resolution_proof else '',
    }
    subject = f'Complaint {complaint.case_number} - Resolved'
    return _send_complainant_email(
        complaint, subject, 'emails/complaint_resolved.html', context,
        'complainant_resolved',
    )


def notify_complaint_closed(complaint, performed_by=None):
    """
    Section 7.2 Step 7 - send closure + satisfaction survey link to the
    complainant.
    """
    config = SystemConfiguration.get_settings()
    if not config.enable_complainant_emails:
        return False, 'Complainant emails are disabled'

    # Build token-based survey URL (matches SatisfactionSurveyView._make_token)
    import hashlib
    token_base = f'{complaint.case_number}-survey-{complaint.pk}'
    token = hashlib.sha256(token_base.encode()).hexdigest()[:32]
    survey_url = ''
    if hasattr(settings, 'SITE_URL'):
        survey_url = f'{settings.SITE_URL}/complaints/{complaint.pk}/survey/{token}/'

    context = {
        'complaint': complaint,
        'complainant_name': complaint.full_name or complaint.complainant_name or 'Member',
        'case_number': complaint.case_number,
        'survey_url': survey_url,
    }
    subject = f'Complaint {complaint.case_number} - Closed'
    return _send_complainant_email(
        complaint, subject, 'emails/complaint_closed.html', context,
        'complainant_closed',
    )



def notify_escalation_email(escalation_log, complaint):
    """
    Section 8.2 - send escalation email (in addition to the in-app
    notification created in escalation.py).
    """
    config = SystemConfiguration.get_settings()
    if not config.enable_escalation_emails:
        return False, 'Escalation emails are disabled'

    target = escalation_log.escalated_to
    if not target or not target.email:
        return False, 'No escalation target / email'

    sender_email = config.sender_email or settings.DEFAULT_FROM_EMAIL
    sender_name = config.sender_name or 'RSSB Complaints System'
    use_custom_smtp = bool(config.smtp_host and config.smtp_username and config.smtp_password)
    if not use_custom_smtp and not getattr(settings, 'EMAIL_HOST', None):
        return False, 'Email service not configured'

    level_label = dict(Complaint.ESCALATION_LEVEL_CHOICES).get(escalation_log.level, '')
    context = {
        'complaint': complaint,
        'escalation': escalation_log,
        'level_label': level_label,
        'trigger_reason': escalation_log.trigger_reason,
        'overdue_hours': max(0, (timezone.now() - complaint.sla_deadline).total_seconds() // 3600) if complaint.sla_deadline else 0,
    }
    subject = f'[ESCALATION] Complaint {complaint.case_number} - {level_label}'
    template = 'emails/escalation_alert.html'

    try:
        html_message = render_to_string(template, context)
        plain_message = strip_tags(html_message)

        if use_custom_smtp:
            from django.core.mail import EmailMultiAlternatives, get_connection
            msg = EmailMultiAlternatives(
                subject=subject, body=plain_message,
                from_email=f'{sender_name} <{sender_email}>',
                to=[target.email],
            )
            msg.attach_alternative(html_message, 'text/html')
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=config.smtp_host, port=config.smtp_port,
                username=config.smtp_username, password=config.smtp_password,
                use_tls=config.use_tls,
            )
            msg.connection = connection
            msg.send()
        else:
            from django.core.mail import send_mail
            send_mail(
                subject=subject, message=plain_message, from_email=sender_email,
                recipient_list=[target.email], html_message=html_message, fail_silently=False,
            )
        return True, f'Escalation email sent to {target.email}'
    except Exception as e:
        return False, f'Failed: {str(e)[:100]}'


