#!/usr/bin/env python
"""Test SMTP connection with provided credentials."""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rssp_cms.settings')
sys.path.insert(0, os.path.dirname(__file__))

django.setup()

from complaints.services import test_smtp_connection
from rssp_cms.models import SystemConfiguration

# Create a temporary config with user-provided credentials
config = SystemConfiguration.get_settings()

# Store original values in case we need to revert
original_email = config.sender_email
original_name = config.sender_name
original_host = config.smtp_host
original_port = config.smtp_port
original_username = config.smtp_username
original_password = config.smtp_password
original_tls = config.use_tls

try:
    # Set test credentials
    config.sender_email = 'tresor.cy@gmail.com'
    config.sender_name = 'RSSB Complaints System'
    config.smtp_host = 'smtp.gmail.com'
    config.smtp_port = 587
    config.smtp_username = 'tresor.cy@gmail.com'
    config.smtp_password = 'ksod dgnk kqpn qcyk'
    config.use_tls = True
    
    print("=" * 60)
    print("TESTING SMTP CONNECTION")
    print("=" * 60)
    print(f"Sender Email:    {config.sender_email}")
    print(f"Sender Name:     {config.sender_name}")
    print(f"SMTP Host:       {config.smtp_host}")
    print(f"SMTP Port:       {config.smtp_port}")
    print(f"SMTP Username:   {config.smtp_username}")
    print(f"Use TLS:         {config.use_tls}")
    print("-" * 60)
    
    # Test the connection
    success, message = test_smtp_connection(config)
    
    print(f"\n✓ TEST RESULT: {'SUCCESS ✅' if success else 'FAILED ❌'}")
    print(f"Message: {message}")
    print("\n" + "=" * 60)
    
    if success:
        print("✅ The SMTP configuration is working!")
        print("You can now save these settings in the admin panel.")
    else:
        print("❌ The SMTP connection failed.")
        print("Please check:")
        print("  • Email and password are correct")
        print("  • Gmail App Password is enabled (not regular password)")
        print("  • 2-Step Verification is enabled on Gmail account")
        print("  • SMTP host and port are correct")

finally:
    # Restore original values (don't save test credentials)
    config.sender_email = original_email
    config.sender_name = original_name
    config.smtp_host = original_host
    config.smtp_port = original_port
    config.smtp_username = original_username
    config.smtp_password = original_password
    config.use_tls = original_tls
