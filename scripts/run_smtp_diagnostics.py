#!/usr/bin/env python3
"""Run SMTP diagnostics in Django context.

Usage:
  python scripts/run_smtp_diagnostics.py

This prints SystemConfiguration values, runs the app's `test_smtp_connection`,
performs a raw TCP connect test, and prints selected environment variables.
"""
import os
import sys
import socket
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rssp_cms.settings')

import django
django.setup()

from complaints.models import SystemConfiguration
from complaints.services import test_smtp_connection


def print_header(msg):
    print('\n' + '=' * 60)
    print(msg)
    print('=' * 60)


def main():
    print_header('SYSTEM CONFIGURATION (DB)')
    cfg = SystemConfiguration.get_settings()
    print('sender_email:', cfg.sender_email)
    print('smtp_host:', cfg.smtp_host)
    print('smtp_port:', cfg.smtp_port)
    print('smtp_username:', cfg.smtp_username)
    print('has_password:', bool(cfg.smtp_password))
    print('use_tls:', cfg.use_tls)
    print('is_fully_configured:', cfg.is_fully_configured)

    print_header('RUNNING APP SMTP TEST (test_smtp_connection)')
    try:
        success, message = test_smtp_connection(cfg)
        print('result:', 'SUCCESS' if success else 'FAILED')
        print('message:', message)
    except Exception as e:
        print('Exception while running test_smtp_connection:')
        traceback.print_exc()

    host = (cfg.smtp_host or '').strip() or os.environ.get('SMTP_HOST') or os.environ.get('EMAIL_HOST')
    port = int(cfg.smtp_port or os.environ.get('SMTP_PORT') or os.environ.get('EMAIL_PORT') or 0)

    if host and port:
        print_header(f'RAW TCP CONNECT TEST TO {host}:{port}')
        try:
            s = socket.socket()
            s.settimeout(10)
            s.connect((host, port))
            print('TCP connect: OK')
            s.close()
        except Exception as e:
            print('TCP connect: FAILED =>', type(e).__name__, e)
    else:
        print_header('TCP TEST SKIPPED')
        print('No host/port available from DB or env vars to test TCP connectivity.')

    print_header('SELECT ENVIRONMENT VARIABLES')
    keys = (
        'SMTP_HOST','SMTP_PORT','SMTP_USERNAME','SMTP_PASSWORD',
        'EMAIL_HOST','EMAIL_PORT','DEFAULT_FROM_EMAIL',
        'DATABASE_URL','DJANGO_SETTINGS_MODULE'
    )
    for k in keys:
        v = os.environ.get(k)
        if k.lower().find('password') >= 0:
            v = bool(v)
        print(f'{k} = {v}')

    print('\nDiagnostics complete.')


if __name__ == '__main__':
    main()
