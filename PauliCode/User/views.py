# Core Django imports
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.contrib import messages
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.decorators import method_decorator
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.safestring import mark_safe
from django.core.files.storage import default_storage
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout, authenticate
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.hashers import make_password, check_password
from django.views.decorators.csrf import csrf_exempt

# Admin Dashboard
from django.contrib.admin.views.decorators import staff_member_required
import django
import logging
from django.db.models import Count, Sum

# Generate 6-digit verification code
import random

# Database
from django.db import IntegrityError
from django.db.models import Q, Sum, Max, F, Exists, OuterRef, Count, Case, When, IntegerField

# Models
from .models import User, Class, Problem, Enrollment, ProblemTestCase, Submission, ChatHistory, ProblemResource, EmailVerification, Exam, ExamSession, CTFChallenge, CTFSolve, ActivityLog

User = get_user_model()

# Email imports
from django.core.mail import send_mail
from django.template.loader import render_to_string

# Python standard library
from datetime import datetime, timedelta
import json
import requests
import subprocess
import tempfile
import os
import shutil
import logging
import re
import time
from collections import defaultdict

# Third-party
from google import genai

#----------------RATE LIMITING IMPORTS----------------#

from .rate_limiter import (
    rate_limit,
    rate_limit_api,
    record_failed_login,
    clear_failed_login,
    get_failed_login_count,
    RateLimiter
)
from .rate_limit_config import RATE_LIMITS


# ---------------- ADMINDASHBOARD ---------------- #

@staff_member_required
def your_exams_view(request):
    now = timezone.now()
    exams = Exam.objects.order_by('start_time')
    return render(request, 'Admin/exams.html', {'exams': exams})


@staff_member_required
def your_riskflags_view(request):
    flags = ExamSession.objects.select_related('student', 'exam').order_by('-risk_level')
    return render(request, 'Admin/risk_flags.html', {'flags': flags})


@staff_member_required
def your_activity_view(request):
    activity = ActivityLog.objects.select_related('user').order_by('-timestamp')[:50]
    return render(request, 'Admin/activity.html', {'activity': activity})

logger = logging.getLogger(__name__)
 
 
@staff_member_required
def admin_dashboard(request):
    """
    Admin-only overview dashboard.
    Requires the user to have is_staff=True (all Teachers get this automatically).
    """
    now = timezone.now()
 
    # ── Students ──────────────────────────────────────────────────────────────
    # FIX: removed the duplicate `role='Student'` lines; use user_type throughout
    total_students = User.objects.filter(user_type='Student').count()
    one_week_ago   = now - timezone.timedelta(days=7)
    new_students   = User.objects.filter(
        user_type='Student',
        date_joined__gte=one_week_ago
    ).count()
 
    # ── Exams ─────────────────────────────────────────────────────────────────
    active_exams_qs = Exam.objects.filter(start_time__lte=now, end_time__gte=now)
    upcoming_qs     = Exam.objects.filter(start_time__gt=now).order_by('start_time')[:2]
 
    exams_display = []
    for exam in list(active_exams_qs) + list(upcoming_qs):
        exams_display.append({
            'title':       exam.title,
            'is_live':     exam.start_time <= now <= exam.end_time,
            'taker_count': ExamSession.objects.filter(exam=exam, submitted=False).count(),
            'start_time':  exam.start_time.strftime('%I:%M %p'),
        })
 
    today_end          = now.replace(hour=23, minute=59, second=59)
    exams_ending_today = active_exams_qs.filter(end_time__lte=today_end).count()
 
    # ── Risk flags ────────────────────────────────────────────────────────────
    # FIX: capture count BEFORE slicing — calling .count() on a sliced queryset
    # raises TypeError in Django.
    risk_sessions_qs  = (
        ExamSession.objects
        .filter(exam__in=active_exams_qs, risk_level__in=['high', 'medium', 'low'])
        .select_related('student', 'exam')
        .order_by('-risk_level')
    )
    risk_flags_count = risk_sessions_qs.count()   # count before slice
    risk_sessions    = risk_sessions_qs[:10]       # then slice for display
 
    new_flags = ExamSession.objects.filter(
        risk_level='high',
        flagged_at__gte=now - timezone.timedelta(days=1)
    ).count()
 
    # ── CTF ───────────────────────────────────────────────────────────────────
    total_ctf_solves = CTFSolve.objects.count()
    ctf_solves_today = CTFSolve.objects.filter(solved_at__date=now.date()).count()
 
    # FIX: replaced non-existent `.annotate_solve_count()` custom manager method
    # with a standard Django annotation using Count on the reverse relation.
    # CTFSolve has a related_name='solves' on its challenge FK, so we use 'solves'.
    ctf_challenges = (
        CTFChallenge.objects
        .filter(is_active=True)
        .annotate(solve_count=Count('solves'))
        [:5]
    )
 
    # ── Leaderboard ───────────────────────────────────────────────────────────
    # FIX: removed the duplicate queryset lines (two separate statements with no
    # operator between them caused a SyntaxError).  Only one filter is needed.
    leaderboard_qs = (
        User.objects
        .filter(user_type='Student')
        .annotate(total_xp=Sum('xp_grants__points'))   # related_name='xp_grants' from XPGrant
        .order_by('-total_xp')[:5]
    )
 
    first = leaderboard_qs.first()
    max_xp = (first.total_xp or 1) if first else 1
    leaderboard = [
        {
            'user':       u,
            'xp_percent': round((u.total_xp or 0) / max(max_xp, 1) * 100),
        }
        for u in leaderboard_qs
    ]
 
    # ── Recent activity ───────────────────────────────────────────────────────
    # NOTE: ActivityLog now uses `event_type` instead of `type` to avoid
    # shadowing Python's built-in type().  Update the template accordingly
    # ({{ event.event_type }} instead of {{ event.type }}).
    recent_activity = (
        ActivityLog.objects
        .select_related('user')
        .order_by('-timestamp')[:8]
    )
 
    # ── Server info ───────────────────────────────────────────────────────────
    ngrok_url = os.environ.get('NGROK_URL', '')
 
    context = {
        'total_students':         total_students,
        'new_students_this_week': new_students,
        'active_exams':           exams_display,
        'active_exams_count':     active_exams_qs.count(),
        'exams_ending_today':     exams_ending_today,
        'risk_flags':             risk_sessions,
        'risk_flags_count':       risk_flags_count,   # FIX: pre-sliced count
        'new_flags':              new_flags,
        'total_ctf_solves':       total_ctf_solves,
        'ctf_solves_today':       ctf_solves_today,
        'ctf_challenges':         ctf_challenges,
        'leaderboard':            leaderboard,
        'recent_activity':        recent_activity,
        'ngrok_url':              ngrok_url,
        'django_version':         django.get_version(),
    }
 
    # FIX: removed the duplicate return statement; one render call with the
    # correct template path.  Adjust 'Admin/admin.html' to wherever your
    # template actually lives.
    return render(request, 'Admin/admin.html', context)

# ---------------- LOGIN & DASHBOARD ---------------- #

def index(request):
    """Login page - redirects if already logged in"""
    if request.user.is_authenticated:
        if request.user.user_type == 'Teacher':
            return redirect('dashboard')
        else:
            return redirect('StudentDashboard')
    context = {
        'currentpage': 'index',
    }
    return render(request, 'Portal/index.html', {'currentpage': 'index'})



@rate_limit('login', max_requests=5, time_window=60)
def login_view(request):
    """Handle user login - initial authentication with rate limiting"""
    if request.method == 'POST':
        school_id = request.POST.get('school_id', '').strip()
        password = request.POST.get('password', '').strip()

        # ✅ Get identifier for failed attempt tracking
        identifier = RateLimiter.get_client_ip(request)
        
        # ✅ Check failed login attempts
        failed_count = get_failed_login_count(identifier)
        if failed_count >= 5:
            messages.error(request, 'Too many failed login attempts. Please try again in 30 minutes.')
            return redirect('index')

        # Use Django's authenticate with custom backend
        user = authenticate(request, school_id=school_id, password=password)

        if user is not None:
            # ✅ Clear failed attempts on successful authentication
            clear_failed_login(identifier)
            
            # Generate 6-digit verification code
            code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
            
            # Delete any existing unverified login codes for this user
            EmailVerification.objects.filter(
                email=user.email, 
                is_used=False,
                first_name='LOGIN_2FA'
            ).delete()
            
            # Create new verification record for login
            expiry_time = timezone.now() + timedelta(minutes=15)
            EmailVerification.objects.create(
                email=user.email,
                code=code,
                first_name='LOGIN_2FA',
                last_name=user.school_id,
                expires_at=expiry_time
            )
            
            # Send verification email
            subject = 'PauliCode - Login Verification Code'
            message = f"""
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                    <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                        <h2 style="color: #4CD964; text-align: center;">Login Verification</h2>
                        <p>Hi {user.first_name},</p>
                        <p>Someone is trying to log in to your PauliCode account. To complete the login process, please use the verification code below:</p>
                        <div style="background-color: #f0f0f0; padding: 20px; text-align: center; border-radius: 8px; margin: 20px 0;">
                            <h1 style="color: #4CD964; letter-spacing: 5px; margin: 0;">{code}</h1>
                        </div>
                        <p>This code will expire in 15 minutes.</p>
                        <p>If you didn't try to log in, please ignore this email and consider changing your password.</p>
                        <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                        <p style="color: #888; font-size: 12px; text-align: center;">
                            PauliCode - Interactive Coding Platform<br>
                            © 2025 All rights reserved.
                        </p>
                    </div>
                </body>
            </html>
            """
            
            try:
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    html_message=message,
                    fail_silently=False,
                )
                
                request.session['login_email'] = user.email
                request.session['login_school_id'] = user.school_id
                
                messages.success(request, f"Verification code sent to {user.email}")
                return render(request, 'Portal/index.html', {
                    'currentpage': 'index',
                    'show_verification': True,
                    'email': user.email
                })
                
            except Exception as e:
                logger.error(f"Failed to send login verification email: {str(e)}")
                messages.error(request, "Failed to send verification code. Please try again.")
                return redirect('index')
        else:
            # ✅ Record failed attempt
            attempts = record_failed_login(identifier)
            remaining = max(0, 5 - attempts)
            
            messages.error(request, f"Invalid School ID or Password. {remaining} attempts remaining.")
            return redirect('index')

    return render(request, 'Portal/index.html', {'currentpage': 'index'})

@csrf_exempt
def send_verification_code(request):
    """
    ✅ NEW UNIFIED VERSION - Handles BOTH signup and login
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
        context = data.get('context', 'signup')  # 'signup' or 'login'
        
        # Validate email
        if not email:
            return JsonResponse({'success': False, 'message': 'Email is required'})
        
        # Context-specific logic
        if context == 'login':
            # LOGIN: Get user by email
            try:
                user = User.objects.get(email=email)
                verification_first_name = 'LOGIN_2FA'
                verification_last_name = user.school_id
                email_subject = 'PauliCode - Login Verification Code'
                email_greeting = f"Hi {user.first_name},"
                email_body = "Someone is trying to log in to your PauliCode account. To complete the login process, please use the verification code below:"
            except User.DoesNotExist:
                return JsonResponse({'success': False, 'message': 'User not found'})
        
        else:  # context == 'signup'
            # SIGNUP: Get details from request
            first_name = data.get('first_name', '').strip()
            last_name = data.get('last_name', '').strip()
            
            # Check if email already exists
            if User.objects.filter(email=email).exists():
                return JsonResponse({'success': False, 'message': 'Email already registered'})
            
            verification_first_name = first_name
            verification_last_name = last_name
            email_subject = 'PauliCode - Email Verification Code'
            email_greeting = f"Hi {first_name},"
            email_body = "Thank you for signing up! To complete your registration, please use the verification code below:"
        
        # Generate 6-digit code
        code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        
        # Delete old unverified codes for this email/context
        EmailVerification.objects.filter(
            email=email,
            first_name=verification_first_name,
            is_used=False
        ).delete()
        
        # Create new verification record
        expiry_time = timezone.now() + timedelta(minutes=15)
        verification = EmailVerification.objects.create(
            email=email,
            code=code,
            first_name=verification_first_name,
            last_name=verification_last_name,
            expires_at=expiry_time
        )
        
        # Send email
        message = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h2 style="color: #4CD964; text-align: center;">{email_subject.split(' - ')[1]}</h2>
                    <p>{email_greeting}</p>
                    <p>{email_body}</p>
                    <div style="background-color: #f0f0f0; padding: 20px; text-align: center; border-radius: 8px; margin: 20px 0;">
                        <h1 style="color: #4CD964; letter-spacing: 5px; margin: 0;">{code}</h1>
                    </div>
                    <p>This code will expire in 15 minutes.</p>
                    <p>If you didn't request this code, please ignore this email.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    <p style="color: #888; font-size: 12px; text-align: center;">
                        PauliCode - Interactive Coding Platform<br>
                        © 2025 All rights reserved.
                    </p>
                </div>
            </body>
        </html>
        """
        
        try:
            send_mail(
                email_subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                html_message=message,
                fail_silently=False,
            )
            logger.info(f"Verification code sent to {email} for {context}")
        except Exception as e:
            verification.delete()
            error_msg = str(e)
            logger.error(f"Failed to send email to {email}: {error_msg}")
            return JsonResponse({'success': False, 'message': f'Failed to send email: {error_msg}'})
        
        return JsonResponse({'success': True, 'message': 'Verification code sent'})
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {str(e)}")
        return JsonResponse({'success': False, 'message': 'Invalid request format'})
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in send_verification_code: {error_msg}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({'success': False, 'message': f'Error: {error_msg}'})


@csrf_exempt
@rate_limit_api('verify_code', max_requests=10, time_window=60)
def verify_code(request):
    """
    Handles BOTH signup and login verification with rate limiting
    Detects context automatically based on request type
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Method not allowed'}, status=405)
    
    try:
        # Detect context: JSON = login, FormData = signup
        content_type = request.content_type or ''
        
        if 'application/json' in content_type:
            # ========== LOGIN VERIFICATION ==========
            data = json.loads(request.body)
            verification_code = ''.join(data.get('verification_code', '').strip().split())
            
            # Get session data
            email = request.session.get('login_email')
            school_id = request.session.get('login_school_id')
            
            if not email or not school_id:
                return JsonResponse({
                    'success': False,
                    'message': 'Session expired. Please try logging in again.'
                })
            
            # Look up verification
            verification = EmailVerification.objects.filter(
                email=email,
                first_name='LOGIN_2FA',
                last_name=school_id,
                is_used=False
            ).order_by('-created_at').first()
            
            if not verification:
                return JsonResponse({
                    'success': False,
                    'message': 'No verification code found. Please request a new one.'
                })
            
            # Verify code
            if verification.code != verification_code:
                return JsonResponse({
                    'success': False,
                    'message': 'Invalid verification code'
                })
            
            if not verification.is_valid():
                return JsonResponse({
                    'success': False,
                    'message': 'Verification code has expired'
                })
            
            # Complete login
            user = User.objects.get(school_id=school_id)
            user.backend = 'User.auth_backend.SchoolIDBackend'
            
            login(request, user)
            
            # Mark as used
            verification.is_used = True
            verification.save()
            
            # Set session
            request.session['school_id'] = user.school_id
            request.session['first_name'] = user.first_name
            request.session['last_name'] = user.last_name
            request.session['user_image'] = user.user_image.url if user.user_image else None
            request.session['user_type'] = user.user_type
            
            # Clear temp session
            request.session.pop('login_email', None)
            request.session.pop('login_school_id', None)
            
            redirect_url = reverse('dashboard') if user.user_type == 'Teacher' else reverse('StudentDashboard')
            
            return JsonResponse({
                'success': True,
                'message': 'Login successful',
                'redirect_url': redirect_url
            })
        
        else:
            # ========== SIGNUP VERIFICATION ==========
            email = request.POST.get('email', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            school = request.POST.get('school', '').strip()
            school_id = request.POST.get('school_id', '').strip()
            user_type = request.POST.get('user_type', '').strip()
            password = request.POST.get('password', '').strip()
            verification_code_raw = request.POST.get('verification_code', '').strip()
            confirmation_code = ''.join(verification_code_raw.split())
            
            # Validate
            if not all([email, first_name, last_name, school, school_id, user_type, password, confirmation_code]):
                return JsonResponse({
                    'success': False,
                    'message': 'Missing required fields'
                })
            
            # Email validation
            if school == 'spus' and not email.endswith('spus.edu.ph'):
                return JsonResponse({
                    'success': False,
                    'message': 'For St. Paul University, email must contain spus.edu.ph'
                })
            
            # Look up verification
            verification = EmailVerification.objects.filter(
                email=email,
                first_name=first_name,
                is_used=False
            ).order_by('-created_at').first()
            
            if not verification:
                return JsonResponse({
                    'success': False,
                    'message': 'No verification code found. Please request a new code.'
                })
            
            # Verify code
            if verification.code != confirmation_code:
                return JsonResponse({
                    'success': False,
                    'message': 'Invalid verification code'
                })
            
            if not verification.is_valid():
                return JsonResponse({
                    'success': False,
                    'message': 'Verification code has expired or already been used'
                })
            
            # Check duplicates
            if User.objects.filter(school_id=school_id).exists():
                return JsonResponse({
                    'success': False,
                    'message': 'School ID already exists'
                })
            
            if User.objects.filter(email=email).exists():
                return JsonResponse({
                    'success': False,
                    'message': 'Email already registered'
                })
            
            # Create user
            user = User.objects.create_user(
                school_id=school_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                school=school,
                password=password,
                user_type=user_type.capitalize(),
            )
            
            # Mark as used
            verification.is_used = True
            verification.save()
            
            logger.info(f"User created: {school_id} ({email})")
            
            # Send welcome email
            try:
                subject = 'Welcome to PauliCode'
                welcome_message = f"""
                <html>
                    <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                        <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                            <h2 style="color: #4CD964; text-align: center;">Welcome to PauliCode!</h2>
                            <p>Hi {first_name} {last_name},</p>
                            <p>Your account has been successfully created!</p>
                            <p><strong>Your Details:</strong></p>
                            <ul>
                                <li>School ID: {school_id}</li>
                                <li>Email: {email}</li>
                                <li>User Type: {user_type}</li>
                            </ul>
                            <p><a href="{request.build_absolute_uri('/')}" style="display: inline-block; background-color: #4CD964; color: black; padding: 12px 30px; text-decoration: none; border-radius: 5px; font-weight: bold;">Go to PauliCode</a></p>
                            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                            <p style="color: #888; font-size: 12px; text-align: center;">
                                PauliCode - Interactive Coding Platform<br>
                                © 2025 All rights reserved.
                            </p>
                        </div>
                    </body>
                </html>
                """
                
                send_mail(
                    subject,
                    welcome_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    html_message=welcome_message,
                    fail_silently=True,
                )
            except Exception as e:
                logger.warning(f"Failed to send welcome email: {e}")
            
            return JsonResponse({
                'success': True,
                'message': 'Account created successfully',
                'redirect_url': reverse('index')
            })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid request format'
        })
    except User.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'User not found'
        })
    except IntegrityError as e:
        logger.error(f"IntegrityError: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': 'An account with this information already exists'
        })
    except Exception as e:
        logger.error(f"Error in verify_code: {str(e)}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': 'An error occurred. Please try again.'
        })


@csrf_exempt
@rate_limit_api('resend_code', max_requests=3, time_window=300)
def resend_verification_code(request):
    """
    Resends codes for BOTH signup and login with strict rate limiting
    3 requests per 5 minutes
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        context = data.get('context', 'login')
        
        if context == 'login':
            # LOGIN: Get from session
            email = request.session.get('login_email')
            school_id = request.session.get('login_school_id')
            
            if not email or not school_id:
                return JsonResponse({
                    'success': False,
                    'message': 'Session expired. Please try logging in again.'
                })
            
            user = User.objects.get(school_id=school_id, email=email)
            
            verification_first_name = 'LOGIN_2FA'
            verification_last_name = school_id
            greeting = f"Hi {user.first_name},"
            subject = 'PauliCode - Login Verification Code (Resent)'
        
        else:  # signup
            email = data.get('email', '').strip()
            first_name = data.get('first_name', '').strip()
            last_name = data.get('last_name', '').strip()
            
            if not all([email, first_name]):
                return JsonResponse({
                    'success': False,
                    'message': 'Missing required information'
                })
            
            verification_first_name = first_name
            verification_last_name = last_name
            greeting = f"Hi {first_name},"
            subject = 'PauliCode - Email Verification Code (Resent)'
        
        # Generate new code
        code = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        
        # Delete old codes
        EmailVerification.objects.filter(
            email=email,
            first_name=verification_first_name,
            is_used=False
        ).delete()
        
        # Create new record
        expiry_time = timezone.now() + timedelta(minutes=15)
        EmailVerification.objects.create(
            email=email,
            code=code,
            first_name=verification_first_name,
            last_name=verification_last_name,
            expires_at=expiry_time
        )
        
        # Send email
        message = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h2 style="color: #4CD964; text-align: center;">Verification Code</h2>
                    <p>{greeting}</p>
                    <p>Here is your new verification code:</p>
                    <div style="background-color: #f0f0f0; padding: 20px; text-align: center; border-radius: 8px; margin: 20px 0;">
                        <h1 style="color: #4CD964; letter-spacing: 5px; margin: 0;">{code}</h1>
                    </div>
                    <p>This code will expire in 15 minutes.</p>
                    <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                    <p style="color: #888; font-size: 12px; text-align: center;">
                        PauliCode - Interactive Coding Platform<br>
                        © 2025 All rights reserved.
                    </p>
                </div>
            </body>
        </html>
        """
        
        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            html_message=message,
            fail_silently=False,
        )
        
        return JsonResponse({
            'success': True,
            'message': 'New verification code sent'
        })
    
    except User.DoesNotExist:
        return JsonResponse({
            'success': False,
            'message': 'User not found'
        })
    except Exception as e:
        logger.error(f"Error resending code: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': 'Failed to send code. Please try again.'
        })



#-------------TEACHER DASHBOARD----------------------#
@login_required(login_url='index')
def dashboard(request):
    """Teacher dashboard with classes and leaderboard"""
    user = request.user

    # Permission check
    if user.user_type != 'Teacher':
        messages.error(request, "Access denied. Teachers only.")
        return redirect('StudentDashboard')

    classes = (
        Class.objects
        .filter(teacher=user)
        .annotate(
            has_scores=Exists(
                Submission.objects.filter(problem_id__class_id=OuterRef('pk'))
            )
        )
        .order_by('-has_scores', '-class_id')
    )

    top_scorers = []
    for c in classes:
        top_submission = (
            Submission.objects
            .filter(problem_id__class_id=c)
            .values(
                'student_id__first_name',
                'student_id__last_name',
                'student_id__school_id'
            )
            .annotate(total_score=Sum('score'))
            .order_by('-total_score')
            .first()
        )
        top_scorers.append({
            'class': c,
            'top_scorer': top_submission
        })

    leaderboard = (
        Submission.objects
        .filter(problem_id__class_id__teacher=user)
        .values('student_id__first_name', 'student_id__last_name', 'student_id__school_id')
        .annotate(total_score=Sum('score'))
        .order_by('-total_score')[:10]
    )

    return render(request, 'User/dashboard.html', {
        'currentpage': 'dashboard',
        'user': user,
        'classes': classes,
        'leaderboard': leaderboard,
        'rank': None,
        'top_scorers': top_scorers,
        'sidebar': 'teacher'
    })



@login_required(login_url='index')
def teacher_progress_api(request):
    """
    API endpoint for teacher to view student progress
    Returns completion statistics filtered by class
    """
    user = request.user

    # Only teachers can access this
    if user.user_type != 'Teacher':
        return JsonResponse({'error': 'Access denied'}, status=403)

    class_id = request.GET.get('class_id', 'all')

    # Get all classes taught by this teacher
    teacher_classes = Class.objects.filter(teacher=user)

    # Filter by specific class if requested
    if class_id != 'all':
        try:
            selected_class = teacher_classes.get(class_id=class_id)
            teacher_classes = [selected_class]
        except Class.DoesNotExist:
            return JsonResponse({'error': 'Class not found'}, status=404)

    # Get all problems for these classes (exclude resources)
    all_problems = Problem.objects.filter(
        class_id__in=teacher_classes
    ).exclude(
        problem_title="Class Resources"
    )

    # Get all students enrolled in these classes
    enrolled_students = User.objects.filter(
        user_type='Student',
        enrollments__class_id__in=teacher_classes
    ).distinct()

    # Get all submissions for these classes
    all_submissions = Submission.objects.filter(
        problem_id__in=all_problems
    )

    # Calculate overall stats
    total_problems = all_problems.count()
    total_students = enrolled_students.count()
    total_possible_submissions = total_problems * total_students
    total_actual_submissions = all_submissions.values('student_id', 'problem_id').distinct().count()

    completion_percentage = 0
    if total_possible_submissions > 0:
        completion_percentage = round((total_actual_submissions / total_possible_submissions) * 100, 1)

    # Progress by class
    progress_by_class = []
    for cls in teacher_classes:
        class_problems = all_problems.filter(class_id=cls)
        class_students = enrolled_students.filter(enrollments__class_id=cls).distinct()
        class_submissions = all_submissions.filter(problem_id__in=class_problems)

        cls_total_problems = class_problems.count()
        cls_total_students = class_students.count()
        cls_possible = cls_total_problems * cls_total_students
        cls_actual = class_submissions.values('student_id', 'problem_id').distinct().count()

        cls_percentage = 0
        if cls_possible > 0:
            cls_percentage = round((cls_actual / cls_possible) * 100, 1)

        progress_by_class.append({
            'class_id': cls.class_id,
            'class_name': cls.title,
            'class_type': cls.class_type,
            'total_problems': cls_total_problems,
            'total_students': cls_total_students,
            'completed_submissions': cls_actual,
            'total_possible': cls_possible,
            'completion_percentage': cls_percentage
        })

    # Students who haven't completed all tasks
    incomplete_students = []
    for student in enrolled_students:
        student_submissions = all_submissions.filter(student_id=student)
        submitted_problem_ids = set(student_submissions.values_list('problem_id', flat=True))
        all_problem_ids = set(all_problems.values_list('problem_id', flat=True))

        missing_count = len(all_problem_ids - submitted_problem_ids)
        if missing_count > 0:
            completion_rate = 0
            if total_problems > 0:
                completion_rate = round(((total_problems - missing_count) / total_problems) * 100, 1)

            incomplete_students.append({
                'student_id': student.school_id,
                'first_name': student.first_name,
                'last_name': student.last_name,
                'user_image': student.user_image.url if student.user_image else None,
                'completed': total_problems - missing_count,
                'total': total_problems,
                'missing': missing_count,
                'completion_rate': completion_rate
            })

    # Sort by completion rate (lowest first)
    incomplete_students.sort(key=lambda x: x['completion_rate'])

    response_data = {
        'total_problems': total_problems,
        'total_students': total_students,
        'total_submissions': total_actual_submissions,
        'total_possible': total_possible_submissions,
        'completion_percentage': completion_percentage,
        'progress_by_class': progress_by_class,
        'incomplete_students': incomplete_students[:20]  # Top 20 students with incomplete work
    }

    return JsonResponse(response_data)


@login_required(login_url='index')
def teacher_tasks_api(request):
    """
    API endpoint for teacher to view all tasks they've created
    Returns list of tasks filtered by class
    """
    user = request.user

    # Only teachers can access this
    if user.user_type != 'Teacher':
        return JsonResponse({'error': 'Access denied'}, status=403)

    class_id = request.GET.get('class_id', 'all')

    # Get all classes taught by this teacher
    teacher_classes = Class.objects.filter(teacher=user)

    # Filter by specific class if requested
    if class_id != 'all':
        try:
            selected_class = teacher_classes.get(class_id=class_id)
            teacher_classes = [selected_class]
        except Class.DoesNotExist:
            return JsonResponse({'error': 'Class not found'}, status=404)

    # Get all problems for these classes (exclude resources)
    all_problems = Problem.objects.filter(
        class_id__in=teacher_classes
    ).exclude(
        problem_title="Class Resources"
    ).select_related('class_id').order_by('-due_date')

    # Format tasks data
    tasks = []
    for problem in all_problems:
        # Count submissions for this problem
        total_students = User.objects.filter(
            user_type='Student',
            enrollments__class_id=problem.class_id
        ).distinct().count()

        submissions_count = Submission.objects.filter(
            problem_id=problem
        ).values('student_id').distinct().count()

        completion_rate = 0
        if total_students > 0:
            completion_rate = round((submissions_count / total_students) * 100, 1)

        # Check if task is overdue
        is_overdue = timezone.now() > problem.due_date

        tasks.append({
            'problem_id': problem.problem_id,
            'title': problem.problem_title,
            'class_name': problem.class_id.title,
            'class_id': problem.class_id.class_id,
            'class_type': problem.class_id.class_type,
            'type': problem.problem_type,
            'due_date': problem.due_date.isoformat(),
            'score': problem.total_score,
            'total_students': total_students,
            'submissions_count': submissions_count,
            'completion_rate': completion_rate,
            'is_overdue': is_overdue
        })

    return JsonResponse({
        'tasks': tasks,
        'count': len(tasks)
    })


def logout_view(request):
    """Handle user logout"""
    logout(request)  # Django's logout clears the session
    messages.success(request, "You have been logged out successfully.")
    return redirect('index')


# ---------------- SIGNUP ---------------- #


@rate_limit('signup', max_requests=3, time_window=300)
def signup(request):
    """Handle user registration with rate limiting - 3 attempts per 5 minutes"""
    if request.method == 'GET':
        return render(request, 'Portal/sign-up.html', {'currentpage': 'sign-up'})
    
    # For POST requests, the frontend will handle the two-step process with AJAX
    return render(request, 'Portal/sign-up.html', {'currentpage': 'sign-up'})







# ---------------- CLASS MANAGEMENT ---------------- #

@login_required(login_url='index')
def create_class(request):
    """Create a new class with class_type support"""
    school_id = request.session.get('school_id')
    teacher = get_object_or_404(User, school_id=school_id)

    # Permission check
    if teacher.user_type != 'Teacher':
        messages.error(request, "Only teachers can create classes.")
        return redirect('StudentDashboard')

    if request.method == "POST":
        class_code = request.POST.get("class_code", "").strip()
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        upload_icon = request.FILES.get("upload_icon")

        # âœ… GET THE CLASS TYPE FROM FORM
        class_type = request.POST.get("class_type", "programming").strip()

        # Validation
        if not all([class_code, title]):
            messages.error(request, "Class code and title are required.")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        # Validate class_type
        if class_type not in ['programming', 'cybersecurity']:
            messages.error(request, "Invalid class type selected.")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        # Check for duplicate using ORM
        if Class.objects.filter(class_code=class_code).exists():
            messages.error(request, "Class code already exists.")
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        # âœ… CREATE THE CLASS WITH class_type
        Class.objects.create(
            class_code=class_code,
            title=title,
            description=description,
            upload_icon=upload_icon,
            teacher=teacher,
            class_type=class_type  # âœ… THIS WAS MISSING!
        )

        # Success message with class type
        class_type_display = "Programming" if class_type == "programming" else "Cybersecurity"
        messages.success(request, f"{class_type_display} class '{title}' created successfully!")

      

        previous_page = request.META.get('HTTP_REFERER', '')
        if 'MyClasses' in previous_page:
            return redirect('MyClasses')
        return redirect('dashboard')
    
        

    return redirect('dashboard') 
    
    


def MyClasses(request):
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    teacher = User.objects.filter(school_id=school_id).first()
    classes = Class.objects.filter(teacher=teacher).order_by('-class_id')  # use class_id

    return render(request, 'User/MyClasses.html', {
        'currentpage': 'MyClasses',
        'user': teacher,
        'classes': classes,
        'sidebar': 'teacher',
    })



@login_required(login_url='index')
def delete_class(request, class_id):
    school_id = request.session.get('school_id')
    teacher = get_object_or_404(User, school_id=school_id)

    # Use ORM with permission check
    class_obj = get_object_or_404(Class, pk=class_id, teacher=teacher)

    if class_obj.teacher != teacher:
        messages.error(request, "You don't have permission to delete this class.")
        return redirect('MyClasses')

    class_obj.delete()
    messages.success(request, 'Class deleted successfully!')

    referer = request.META.get('HTTP_REFERER', '')
    if 'report' in referer.lower():
        return redirect('report')
    else:
        return redirect('MyClasses')


# ---------------- CLASS DETAILS PAGE ---------------- #

@login_required(login_url='index')
def classDetails(request, class_id):
    """Teacher class details with resources - handles both programming and cybersecurity"""
    school_id = request.session.get('school_id')
    teacher = get_object_or_404(User, school_id=school_id)

    if teacher.user_type != 'Teacher':
        messages.error(request, "Access denied.")
        return redirect('StudentDashboard')

    class_obj = get_object_or_404(Class, class_id=class_id, teacher=teacher)

    # Exclude "Class Resources" problem from problems list
    problems = Problem.objects.filter(
        class_id=class_obj
    ).exclude(
        problem_title="Class Resources"
    ).order_by('-problem_id')

    # Get all resources for this class and group by title
    from .models import ProblemResource

    all_resources = ProblemResource.objects.filter(
        problem__class_id=class_obj
    ).select_related('problem').order_by('-uploaded_at')

    # Group resources by title
    grouped_resources = defaultdict(list)
    for resource in all_resources:
        grouped_resources[resource.title].append(resource)

    # Convert to list of dictionaries for template with JSON formatting
    resources_list = []
    for title, resource_list in grouped_resources.items():
        # Prepare files data as JSON-serializable dictionaries
        files_data = []
        for resource in resource_list:
            files_data.append({
                'file_url': resource.file.url,
                'original_filename': resource.original_filename,
                'file_size': resource.file_size,
                'uploaded_at': resource.uploaded_at.strftime('%Y-%m-%d %H:%M'),
                'file_extension': resource.file_extension,
                'resource_id': resource.resource_id
            })

        resources_list.append({
            'title': title,
            'description': resource_list[0].description,
            'files': mark_safe(json.dumps(files_data)),  # âœ… For JavaScript
            'file_count': len(resource_list),            # âœ… For Django template
            'uploaded_at': resource_list[0].uploaded_at,
            'resource_ids': [r.resource_id for r in resource_list]
        })

    # Search and filter handling
    query = request.GET.get('q', '').strip()
    filter_type = request.GET.get('filter', '').strip()

    last_filter = request.session.get('last_filter', '')
    if filter_type == last_filter:
        filter_type = ''
        request.session['last_filter'] = ''
    else:
        request.session['last_filter'] = filter_type

    if query:
        problems = problems.filter(problem_title__icontains=query)
    if filter_type == 'Assignment':
        problems = problems.filter(problem_type='Assignment')
    elif filter_type == 'Quiz':
        problems = problems.filter(problem_type='Quiz')

    # Student search and filtering
    student_query = request.GET.get('student_search', '').strip()
    students = Enrollment.objects.filter(class_id=class_obj).select_related('student_id').order_by('student_id__first_name')

    if student_query:
        students = students.filter(
            Q(student_id__first_name__icontains=student_query) |
            Q(student_id__last_name__icontains=student_query)
        )

    context = {
        'currentpage': 'MyClasses',
        'nav': 'classDetails',
        'user': teacher,
        'class': class_obj,
        'problems': problems,
        'students': students,
        'resources': resources_list,
        'query': query,
        'filter_type': filter_type,
        'student_query': student_query,
        'sidebar': 'teacher'
    }

    # âœ… Route to appropriate template based on class_type
    if hasattr(class_obj, 'class_type') and class_obj.class_type == 'cybersecurity':
        return render(request, 'User/cybersecurity_class_details.html', context)
    else:
        return render(request, 'User/classDetails.html', context)





    # ---- ADD PROBLEM------------

@login_required(login_url='index')
def add_problem(request, class_id):
    """Add problem with optional file resource"""
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    teacher = get_object_or_404(User, school_id=school_id)
    class_obj = get_object_or_404(Class, class_id=class_id, teacher=teacher)

    if request.method == "POST":
        title = request.POST.get("problem_title", "").strip()
        description = request.POST.get("problem_description", "").strip()
        problem_type = request.POST.get("problem_type", "").strip()
        total_score = request.POST.get("total_score", "").strip()
        time_limit = request.POST.get("time_limit", "").strip()
        due_date = request.POST.get("due_date", "").strip()

        # Get uploaded file
        resource_file = request.FILES.get("resource_file")

        # Test cases
        inputs = [request.POST.get(f"input{i}", "").strip() for i in range(1, 4)]
        outputs = [request.POST.get(f"output{i}", "").strip() for i in range(1, 4)]

        # Validation
        if not all([title, description, problem_type, total_score, time_limit, due_date]):
            messages.error(request, "Please fill in all required fields.")
            return redirect('classDetails', class_id=class_id)

        try:
            total_score = int(total_score)
            time_limit = int(time_limit)
            due_date = datetime.fromisoformat(due_date)
        except ValueError:
            messages.error(request, "Invalid input values.")
            return redirect('classDetails', class_id=class_id)

        # Validate file if provided
        if resource_file:
            # Check file size (10MB max)
            if resource_file.size > 10 * 1024 * 1024:
                messages.error(request, "File size exceeds 10MB limit.")
                return redirect('classDetails', class_id=class_id)

            # Check file extension
            allowed_extensions = ['pdf', 'png', 'jpg', 'jpeg', 'docx', 'pptx', 'ppt', 'zip', 'txt']
            file_ext = resource_file.name.split('.')[-1].lower()
            if file_ext not in allowed_extensions:
                messages.error(request, "Invalid file type. Allowed: PDF, Images, DOCX, PPTX, ZIP, TXT")
                return redirect('classDetails', class_id=class_id)

        # Create Problem
        problem = Problem.objects.create(
            class_id=class_obj,
            teacher_id=teacher,
            problem_title=title,
            problem_description=description,
            problem_type=problem_type,
            total_score=total_score,
            time_limit=time_limit,
            due_date=due_date,
        )

        # Add test cases
        for i in range(3):
            if inputs[i] or outputs[i]:
                ProblemTestCase.objects.create(
                    problem_id=problem,
                    input_data=inputs[i],
                    expected_output=outputs[i]
                )

        # Add resource file if provided
        if resource_file:
            from .models import ProblemResource  # Import here to avoid circular import
            ProblemResource.objects.create(
                problem=problem,
                file=resource_file
            )
            messages.success(request, f"Problem '{title}' created with resource file!")
        else:
            messages.success(request, f"Problem '{title}' created successfully!")

        return redirect('classDetails', class_id=class_id)

    return redirect('classDetails', class_id=class_id)

#delete resources
@login_required(login_url='index')
@csrf_exempt
def delete_resource(request, resource_id):
    """Delete a problem resource"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    school_id = request.session.get('school_id')
    teacher = get_object_or_404(User, school_id=school_id)

    from .models import ProblemResource
    resource = get_object_or_404(ProblemResource, resource_id=resource_id)

    # Check if teacher owns the class
    if resource.problem.class_id.teacher != teacher:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        resource.delete()
        return JsonResponse({'success': True, 'message': 'Resource deleted successfully'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

#add resources
@login_required(login_url='index')
def add_resources(request, class_id):
    """Add resources to a class with multiple file upload support"""
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    teacher = get_object_or_404(User, school_id=school_id)
    class_obj = get_object_or_404(Class, class_id=class_id, teacher=teacher)

    if request.method == "POST":
        title = request.POST.get("resource_title", "").strip()
        description = request.POST.get("resource_description", "").strip()
        files = request.FILES.getlist("resource_files")

        # Validation
        if not title:
            messages.error(request, "Please provide a resource title.")
            return redirect('classDetails', class_id=class_id)

        if not files:
            messages.error(request, "Please select at least one file.")
            return redirect('classDetails', class_id=class_id)

        # Validate each file
        max_size = 50 * 1024 * 1024  # 50MB
        errors = []
        valid_files = []

        for file in files:
            if file.size > max_size:
                errors.append(f"{file.name} exceeds 50MB limit")
            else:
                valid_files.append(file)

        if errors:
            messages.error(request, "File validation errors:\n" + "\n".join(errors))

        if not valid_files:
            messages.error(request, "No valid files to upload.")
            return redirect('classDetails', class_id=class_id)

        # Find or create a resources problem for this class
        resources_problem, created = Problem.objects.get_or_create(
            class_id=class_obj,
            teacher_id=teacher,
            problem_title=f"Class Resources",
            defaults={
                'problem_description': f"Resources for {class_obj.title}",
                'problem_type': 'Assignment',
                'total_score': 0,
                'due_date': timezone.now() + timedelta(days=365),
                'time_limit': None,
            }
        )

        # Create resources for each file with the same title (grouped)
        created_count = 0
        for file in valid_files:
            try:
                ProblemResource.objects.create(
                    problem=resources_problem,
                    title=title,  # Same title for all files in this upload
                    description=description,
                    file=file
                )
                created_count += 1
            except Exception as e:
                messages.error(request, f"Error uploading {file.name}: {str(e)}")

        if created_count > 0:
            messages.success(request, f"Successfully uploaded {created_count} resource(s)!")
        else:
            messages.error(request, "No resources were uploaded.")

        return redirect('classDetails', class_id=class_id)

    return redirect('classDetails', class_id=class_id)

#delete resources
@login_required(login_url='index')
def delete_resource(request, resource_id):
    """Delete a resource"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    school_id = request.session.get('school_id')
    teacher = get_object_or_404(User, school_id=school_id)

    resource = get_object_or_404(ProblemResource, resource_id=resource_id)

    # Check if teacher owns the class
    if resource.problem.class_id.teacher != teacher:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        resource.delete()
        return JsonResponse({'success': True, 'message': 'Resource deleted successfully'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def get_resource_details(request, resource_id):
    """Get resource details for the modal"""
    resource = get_object_or_404(ProblemResource, resource_id=resource_id)

    # Check permissions
    school_id = request.session.get('school_id')
    if not school_id:
        return JsonResponse({'error': 'Please log in first'}, status=401)

    user = get_object_or_404(User, school_id=school_id)

    # Check if user has access to this resource
    if user.user_type == 'Teacher':
        if resource.problem.class_id.teacher != user:
            return JsonResponse({'error': 'Permission denied'}, status=403)
    else:  # Student
        if not Enrollment.objects.filter(
            student_id=user,
            class_id=resource.problem.class_id
        ).exists():
            return JsonResponse({'error': 'Permission denied'}, status=403)

    data = {
        'resource_id': resource.resource_id,
        'title': resource.title,
        'description': resource.description,
        'file_url': resource.file.url,
        'original_filename': resource.original_filename,
        'file_size': resource.file_size,
        'file_extension': resource.file_extension,
        'uploaded_at': resource.uploaded_at.strftime('%Y-%m-%d %H:%M'),
        'problem_title': resource.problem.problem_title,
        'class_title': resource.problem.class_id.title
    }

    return JsonResponse(data)
#--------------------------Problem Details---------------------------------#

                #Works for both Students and Teachers

def get_problem_details(request, problem_id):
    """Return problem details as JSON (handles both programming and cybersecurity)"""
    problem = get_object_or_404(Problem, pk=problem_id)

    # Check if it's a cybersecurity challenge
    if problem.class_id.class_type == 'cybersecurity':
        return get_cybersecurity_problem_details(request, problem_id)

    # Original code for programming challenges
    test_cases = ProblemTestCase.objects.filter(problem_id=problem)

    # Default value
    answered = False

    # Check if the logged-in user is a student and has submitted this problem
    school_id = request.session.get('school_id')
    if school_id:
        user = User.objects.filter(school_id=school_id).first()
        if user and user.user_type == "Student":
            answered = Submission.objects.filter(problem_id=problem, student_id=user).exists()

    # Prepare data
    data = {
        "problem_id": problem.problem_id,
        "title": problem.problem_title,
        "description": problem.problem_description,
        "type": problem.problem_type,
        "score": problem.total_score,
        "time_limit": problem.time_limit,
        "due_date": problem.due_date.strftime("%Y-%m-%d %H:%M"),
        "answered": answered,
        "test_cases": [
            {"input": tc.input_data, "output": tc.expected_output} for tc in test_cases
        ]
    }

    return JsonResponse(data)


#----------------------Problem Deletion------------------------------------#

@login_required(login_url='index')
def delete_problem(request, problem_id):
    from .models import Problem
    problem = get_object_or_404(Problem, pk=problem_id)
 
    # Ownership check — teacher can only delete their own problems
    school_id = request.session.get('school_id')
    if not school_id or problem.class_id.teacher.school_id != school_id:
        return JsonResponse({'error': 'Permission denied'}, status=403)
 
    class_id = problem.class_id.class_id
    problem.delete()
    from django.contrib import messages
    messages.success(request, "Problem deleted successfully.")
    return redirect('classDetails', class_id=class_id)

#----------------------Edit Problem-----------------------------------------#
@login_required(login_url='index')
def edit_problem(request, problem_id):
    from .models import Problem, ProblemTestCase
    from datetime import datetime
    from django.contrib import messages
 
    problem = get_object_or_404(Problem, pk=problem_id)
 
    # Ownership check
    school_id = request.session.get('school_id')
    if not school_id or problem.class_id.teacher.school_id != school_id:
        return JsonResponse({'error': 'Permission denied'}, status=403)
 
    class_id = problem.class_id.class_id
 
    if request.method == "POST":
        title        = request.POST.get("problem_title", "").strip()
        description  = request.POST.get("problem_description", "").strip()
        problem_type = request.POST.get("problem_type", "").strip()
        total_score  = request.POST.get("total_score", "").strip()
        time_limit   = request.POST.get("time_limit", "").strip()
        due_date     = request.POST.get("due_date", "").strip()
 
        try:
            problem.problem_title       = title
            problem.problem_description = description
            problem.problem_type        = problem_type
            problem.total_score         = int(total_score)
            problem.time_limit          = int(time_limit)
            problem.due_date            = datetime.fromisoformat(due_date)
            problem.save()
 
            ProblemTestCase.objects.filter(problem_id=problem.problem_id).delete()
            for i in range(3):
                input_data  = request.POST.get(f"input{i+1}", "").strip()
                output_data = request.POST.get(f"output{i+1}", "").strip()
                if input_data or output_data:
                    ProblemTestCase.objects.create(
                        problem_id=problem,
                        input_data=input_data,
                        expected_output=output_data,
                    )
 
            messages.success(request, f"Problem '{problem.problem_title}' updated successfully!")
        except Exception as e:
            messages.error(request, f"Update failed: {e}")
 
    return redirect('classDetails', class_id=class_id)


# ---------- REPORT DASHBOARD ----------
@login_required(login_url='index')
def report(request):
    from .models import User, Class, Problem, Submission, Enrollment
    from django.db.models import Q
 
    user = request.user   # request.user is reliable after @login_required
    search_query = request.GET.get('search', '').strip()
 
    if user.user_type.lower() == 'teacher':
        classes = Class.objects.filter(teacher=user).order_by('class_id')
    else:
        classes = Class.objects.filter(
            enrollments__student_id=user
        ).distinct().order_by('class_id')
 
    if search_query:
        classes = classes.filter(
            Q(title__icontains=search_query) |
            Q(class_code__icontains=search_query)
        )
 
    problems = Problem.objects.filter(
        class_id__in=classes
    ).exclude(
        problem_title="Class Resources"
    ).select_related('class_id').order_by('problem_id')
 
    submissions = Submission.objects.select_related(
        'student_id', 'problem_id', 'problem_id__class_id'
    ).filter(
        problem_id__class_id__in=classes
    ).order_by('student_id__school_id', '-submitted_at')
 
    students = User.objects.filter(
        user_type__iexact='student',
        enrollments__class_id__in=classes
    ).distinct().order_by('school_id')
 
    context = {
        'user':              user,
        'classes':           classes,
        'problems':          problems,
        'students':          students,
        'submissions':       submissions,
        'total_students':    students.count(),
        'total_submissions': submissions.count(),
        'pending_reviews':   submissions.filter(score__isnull=True).count(),
        'currentpage':       'report',
        'search_query':      search_query,
        'sidebar':           'teacher',
    }
    return render(request, 'User/report.html', context)

# ---------- Unenroll STUDENT ----------
@login_required(login_url='index')
def delete_student(request, school_id, class_id):
    current_user_id = request.session.get('school_id')
    current_user = get_object_or_404(User, school_id=current_user_id)

    # Permission check - only teachers can unenroll students
    if current_user.user_type != 'Teacher':
        messages.error(request, "Only teachers can unenroll students.")
        return redirect('StudentDashboard')

    student = get_object_or_404(User, school_id=school_id, user_type__iexact='student')
    class_obj = get_object_or_404(Class, class_id=class_id, teacher=current_user)

    # Use ORM get_or_404
    enrollments = get_object_or_404(Enrollment, student_id=student, class_id=class_obj)
    enrollments.delete()
    messages.success(request, f'{student.first_name} has been unenrolled from {class_obj.title}.')

    return redirect('report')





# ---------------- REVIEW SUBMISSION ---------------- #
def review_submission(request, submission_id):
    submission = get_object_or_404(Submission, submission_id=submission_id)

    if request.method == 'POST':
        new_status = request.POST.get('status')
        feedback = request.POST.get('feedback')
        submission.status = new_status
        submission.feedback = feedback
        submission.save()
        messages.success(request, 'âœ… Submission review updated successfully!')
        return redirect('report')

    return render(request, 'review_submission.html', {'submission': submission})


# ---------- DELETE SUBMISSION ----------
@login_required(login_url='index')
def delete_submission(request, submission_id):
    submission = get_object_or_404(Submission, submission_id=submission_id)
    submission.delete()
    messages.success(request, 'Student submission deleted successfully!')
    return redirect('report')

#----------VIEW STUDENT CODE---------------
def view_submission_code(request, submission_id):
    submission = get_object_or_404(Submission, pk=submission_id)
    code = submission.code or ""

    # HTML-safe rendering of code
    html_content = f"""
    <html>
      <head>
        <title>View Code - {escape(submission.student_id.first_name)} {escape(submission.student_id.last_name)}</title>
        <style>
          body {{
            background-color: #0f172a;
            color: #e2e8f0;
            font-family: 'Fira Code', monospace;
            padding: 20px;
          }}
          pre {{
            background: #1e293b;
            padding: 15px;
            border-radius: 8px;
            white-space: pre-wrap;
            word-wrap: break-word;
            line-height: 1.4;
          }}
          h2 {{
            color: #38bdf8;
          }}
        </style>
      </head>
      <body>
        <h2>{escape(submission.problem_id.problem_title)}</h2>
        <pre>{escape(code)}</pre>
      </body>
    </html>
    """

    # Important: specify content_type as text/html
    return HttpResponse(html_content, content_type="text/html")




#---------------Student Part-------------------------#

#---------------Student Dashboard--------------------#
@login_required(login_url='index')
def StudentDashboard(request):
    """Student dashboard"""
    student = request.user

    # Permission check
    if student.user_type != 'Student':
        messages.error(request, "Access denied. Students only.")
        return redirect('dashboard')

    enrolled_classes = (
        Class.objects
        .filter(enrollments__student_id=student)
        .order_by('-class_id')
        .distinct()
    )

    top_scorers = []
    for c in enrolled_classes:
        top_submission = (
            Submission.objects
            .filter(problem_id__class_id=c)
            .values(
                'student_id__first_name',
                'student_id__last_name',
                'student_id__school_id'
            )
            .annotate(total_score=Sum('score'))
            .order_by('-total_score')
            .first()
        )
        top_scorers.append({
            'class': c,
            'top_scorer': top_submission
        })

    leaderboard = (
        Submission.objects
        .values('student_id__first_name', 'student_id__last_name', 'student_id__school_id')
        .annotate(total_score=Sum('score'))
        .order_by('-total_score')[:10]
    )

    # Calculate rank
    all_scores = (
        Submission.objects
        .values('student_id__school_id')
        .annotate(total_score=Sum('score'))
        .order_by('-total_score')
    )
    rank = next((i + 1 for i, s in enumerate(all_scores) if s['student_id__school_id'] == student.school_id), None)

    return render(request, 'Students/StudentDashboard.html', {
        'currentpage': 'StudentDashboard',
        'user': student,
        'classes': enrolled_classes,
        'leaderboard': leaderboard,
        'rank': rank,
        'top_scorers': top_scorers,
    })



#---------------Student Enrolled Classes----------------#
def StudentClass(request):
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    student = User.objects.filter(school_id=school_id).first()

    # Get classes the student is enrolled in
    enrolled_classes = Class.objects.filter(
        enrollments__student_id=student
    ).order_by('-class_id').distinct()

    return render(request, 'Students/StudentClass.html', {
        'currentpage': 'StudentClass',
        'user': student,
        'classes': enrolled_classes,
    })

# ---------------- JOIN CLASS (STUDENT) ---------------- #
@login_required(login_url='index')
def join_class(request):
    if request.method == "POST":
        school_id = request.session.get('school_id')
        student = get_object_or_404(User, school_id=school_id)

        # Permission check
        if student.user_type != 'Student':
            messages.error(request, "Only students can join classes.")
            return redirect('dashboard')

        class_code = request.POST.get('class_code', '').strip()

        if not class_code:
            messages.error(request, "Please enter a class code.")
            return redirect('StudentClass')

        # Use ORM
        try:
            class_obj = Class.objects.get(class_code=class_code)
        except Class.DoesNotExist:
            messages.error(request, "Class not found. Please check the code.")
            return redirect('StudentClass')

        # Check if already enrolled using ORM
        if Enrollment.objects.filter(class_id=class_obj, student_id=student).exists():
            messages.warning(request, f"You are already enrolled in {class_obj.title}.")
            return redirect('StudentClass')

        # Create enrollment
        Enrollment.objects.create(class_id=class_obj, student_id=student)
        messages.success(request, f"Successfully joined {class_obj.title}!")
        return redirect('StudentClass')

    return redirect('StudentClass')
#---------------STUDENT CLASS DETAILS PAGE---------------------#

def student_class_details(request, class_id):
    """Student class details - routes to appropriate template based on class type"""
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    # Get student and class instance
    enrollment = get_object_or_404(Enrollment, student_id__school_id=school_id, class_id=class_id)
    student = enrollment.student_id
    class_instance = get_object_or_404(Class, pk=class_id)

    # Search and filter handling
    query = request.GET.get('q', '').strip()
    filter_type = request.GET.get('filter', '').strip()

    problems = Problem.objects.filter(class_id=class_instance).exclude(
        problem_title="Class Resources"  # Exclude resources problem
    ).order_by('-problem_id')

    if query:
        problems = problems.filter(problem_title__icontains=query)
    if filter_type:
        problems = problems.filter(problem_type=filter_type)

    # Prepare problems + submission info
    problem_data = []
    for p in problems:
        submission = Submission.objects.filter(
            student_id=student,
            problem_id=p
        ).order_by('-submission_id').first()

        half_score = p.total_score / 2

        problem_data.append({
            'problem': p,
            'score': submission.score if submission else None,
            'answered': submission is not None,
            'half_score': half_score,
        })

    # âœ… Get all resources for this class and group by title (same as teacher view)
    all_resources = ProblemResource.objects.filter(
        problem__class_id=class_instance
    ).select_related('problem').order_by('-uploaded_at')

    # Group resources by title
    grouped_resources = defaultdict(list)
    for resource in all_resources:
        grouped_resources[resource.title].append(resource)

    # Convert to list of dictionaries for template with JSON formatting
    resources_list = []
    for title, resource_list in grouped_resources.items():
        # Prepare files data as JSON-serializable dictionaries
        files_data = []
        for resource in resource_list:
            files_data.append({
                'file_url': resource.file.url,
                'original_filename': resource.original_filename,
                'file_size': resource.file_size,
                'uploaded_at': resource.uploaded_at.strftime('%Y-%m-%d %H:%M'),
                'file_extension': resource.file_extension,
                'resource_id': resource.resource_id
            })

        resources_list.append({
            'title': title,
            'description': resource_list[0].description,
            'files': mark_safe(json.dumps(files_data)),  # For JavaScript
            'file_count': len(resource_list),
            'uploaded_at': resource_list[0].uploaded_at,
            'resource_ids': [r.resource_id for r in resource_list]
        })

    context = {
        'user': student,
        'class': class_instance,
        'problems': problem_data,
        'resources': resources_list,  # âœ… Added resources
        'query': query,
        'filter_type': filter_type,
        'currentpage': 'StudentClass',
        'nav': 'student_class_details',
    }

    # âœ… Route to appropriate template based on class_type
    if hasattr(class_instance, 'class_type') and class_instance.class_type == 'cybersecurity':
        return render(request, 'Students/cybersecurity_student_class_details.html', context)
    else:
        return render(request, 'Students/student_class_details.html', context)

#------------------Unenroll Function--------------------#
@login_required(login_url='index')
def unenroll_class(request, class_id):
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    student = get_object_or_404(User, school_id=school_id)
    enrollment = Enrollment.objects.filter(class_id=class_id, student_id=student).first()
    if enrollment:
        enrollment.delete()
        messages.success(request, "You have unenrolled from the class.")
    else:
        messages.error(request, "You are not enrolled in this class.")

    return redirect('StudentClass')

@login_required(login_url='index')
def student_progress_api(request):
    """
    API endpoint for student progress data
    Returns completion statistics filtered by class with weekly progress
    """
    user = request.user

    # Only students can access this
    if user.user_type != 'Student':
        return JsonResponse({'error': 'Access denied'}, status=403)

    class_id = request.GET.get('class_id', 'all')

    # Get all classes student is enrolled in
    enrolled_classes = Class.objects.filter(
        enrollments__student_id=user
    ).distinct()

    # Filter by specific class if requested
    if class_id != 'all':
        try:
            selected_class = enrolled_classes.get(class_id=class_id)
            enrolled_classes = [selected_class]
        except Class.DoesNotExist:
            return JsonResponse({'error': 'Class not found'}, status=404)

    # Get all problems for these classes (exclude resources)
    all_problems = Problem.objects.filter(
        class_id__in=enrolled_classes
    ).exclude(
        problem_title="Class Resources"
    )

    # Get student's submissions
    submissions = Submission.objects.filter(
        student_id=user,
        problem_id__in=all_problems
    )

    # Calculate overall stats
    total_challenges = all_problems.count()
    completed_challenges = submissions.values('problem_id').distinct().count()
    total_score = submissions.aggregate(Sum('score'))['score__sum'] or 0
    max_score = all_problems.aggregate(Sum('total_score'))['total_score__sum'] or 0

    # Progress by class
    progress_by_class = []
    for cls in enrolled_classes:
        class_problems = all_problems.filter(class_id=cls)
        class_submissions = submissions.filter(problem_id__in=class_problems)

        cls_total = class_problems.count()
        cls_completed = class_submissions.values('problem_id').distinct().count()
        cls_score = class_submissions.aggregate(Sum('score'))['score__sum'] or 0
        cls_max_score = class_problems.aggregate(Sum('total_score'))['total_score__sum'] or 0

        if cls_total > 0:  # Only include classes with problems
            progress_by_class.append({
                'class_name': cls.title,
                'class_type': cls.class_type,
                'completed': cls_completed,
                'total': cls_total,
                'score': cls_score,
                'max_score': cls_max_score
            })

    # Calculate weekly progress (last 4 weeks)
    weekly_progress = []
    now = timezone.now()

    for i in range(3, -1, -1):  # Last 4 weeks
        week_start = now - timedelta(weeks=i+1)
        week_end = now - timedelta(weeks=i)

        week_submissions = submissions.filter(
            submitted_at__gte=week_start,
            submitted_at__lt=week_end
        ).values('problem_id').distinct().count()

        week_label = f"Week {4-i}"
        if i == 0:
            week_label = "This Week"

        weekly_progress.append({
            'week': week_label,
            'completed': week_submissions
        })

    response_data = {
        'completed': completed_challenges,
        'total': total_challenges,
        'total_score': total_score,
        'max_score': max_score,
        'by_class': progress_by_class,
        'weekly_progress': weekly_progress
    }

    return JsonResponse(response_data)


@login_required(login_url='index')
def student_pending_tasks_api(request):
    """
    API endpoint for pending tasks
    Returns unanswered problems filtered by class
    """
    user = request.user

    # Only students can access this
    if user.user_type != 'Student':
        return JsonResponse({'error': 'Access denied'}, status=403)

    class_id = request.GET.get('class_id', 'all')

    # Get all classes student is enrolled in
    enrolled_classes = Class.objects.filter(
        enrollments__student_id=user
    ).distinct()

    # Filter by specific class if requested
    if class_id != 'all':
        try:
            selected_class = enrolled_classes.get(class_id=class_id)
            enrolled_classes = [selected_class]
        except Class.DoesNotExist:
            return JsonResponse({'error': 'Class not found'}, status=404)

    # Get all problems for these classes (exclude resources)
    all_problems = Problem.objects.filter(
        class_id__in=enrolled_classes
    ).exclude(
        problem_title="Class Resources"
    ).select_related('class_id')

    # Get problems that student hasn't completed yet
    submitted_problem_ids = Submission.objects.filter(
        student_id=user
    ).values_list('problem_id', flat=True)

    pending_problems = all_problems.exclude(
        problem_id__in=submitted_problem_ids
    ).order_by('due_date')

    # Format tasks data
    tasks = []
    for problem in pending_problems:
        # Determine difficulty based on score (simple heuristic)
        difficulty = 'Easy'
        if problem.total_score >= 100:
            difficulty = 'Medium'
        if problem.total_score >= 150:
            difficulty = 'Hard'

        tasks.append({
            'id': problem.problem_id,
            'title': problem.problem_title,
            'class_name': problem.class_id.title,
            'class_id': problem.class_id.class_id,
            'class_type': problem.class_id.class_type,
            'type': problem.problem_type,
            'due_date': problem.due_date.isoformat(),
            'score': problem.total_score,
            'difficulty': difficulty
        })

    return JsonResponse({
        'tasks': tasks,
        'count': len(tasks)
    })

# External code runner API
JUDGE0_URL = "https://judge0-ce.p.rapidapi.com"

JUDGE0_HEADERS = {
    "x-rapidapi-host": "judge0-ce.p.rapidapi.com",
    'x-rapidapi-key': "51eff1ec15mshf4256cee0f36011p10093cjsn45272a73fb25",  # <-- Replace this
    "Content-Type": "application/json",
}

JUDGE0_LANG_MAP = {
    "python":  71,   # Python 3.8.1
    "python3": 71,
    "c":       50,   # C (GCC 9.2.0)
    "cpp":     54,   # C++ (GCC 9.2.0)
    "java":    62,   # Java (OpenJDK 13.0.1)
}

def judge0_execute(language: str, code: str, stdin: str = "", timeout_sec: int = 5) -> dict:
    """
    Submit code to Judge0, poll until complete, return result dict:
      {
        "stdout": str,
        "stderr": str,
        "compile_error": str,
        "error": str        # network / unexpected errors
      }
    """
    lang_id = JUDGE0_LANG_MAP.get(language.lower(), 71)  # default Python
 
    # Step 1 — Create submission (async, no wait)
    payload = {
        "language_id": lang_id,
        "source_code": code,
        "stdin": stdin or "",
        "cpu_time_limit": timeout_sec,
        "wall_time_limit": timeout_sec + 2,
    }
 
    try:
        create_resp = requests.post(
            f"{JUDGE0_URL}/submissions?base64_encoded=false&wait=false",
            json=payload,
            headers=JUDGE0_HEADERS,
            timeout=15,
        )
        create_resp.raise_for_status()
        token = create_resp.json().get("token")
 
        if not token:
            return _err("Judge0 did not return a submission token.")
 
    except requests.RequestException as e:
        return _err(f"Network error submitting to Judge0: {e}")
 
    # Step 2 — Poll until finished (status id > 2 means done)
    poll_url = f"{JUDGE0_URL}/submissions/{token}?base64_encoded=false&fields=stdout,stderr,compile_output,status,time,memory"
    max_polls = 10
    poll_interval = 0.8  # seconds
 
    for _ in range(max_polls):
        time.sleep(poll_interval)
        try:
            poll_resp = requests.get(poll_url, headers=JUDGE0_HEADERS, timeout=10)
            poll_resp.raise_for_status()
            result = poll_resp.json()
        except requests.RequestException as e:
            return _err(f"Network error polling Judge0: {e}")
 
        status_id = result.get("status", {}).get("id", 0)
 
        if status_id <= 2:
            # 1 = In Queue, 2 = Processing — keep waiting
            continue
 
        # Done
        stdout        = result.get("stdout") or ""
        stderr        = result.get("stderr") or ""
        compile_error = result.get("compile_output") or ""
 
        # status_id 3 = Accepted; others are errors/TLE/MLE etc.
        if status_id == 5:
            return _err("Time Limit Exceeded.")
        if status_id == 6:
            return {"stdout": "", "stderr": "", "compile_error": compile_error, "error": ""}
        if status_id in (7, 8, 9, 10, 11, 12):
            return {"stdout": stdout, "stderr": stderr, "compile_error": compile_error, "error": ""}
 
        return {"stdout": stdout, "stderr": stderr, "compile_error": compile_error, "error": ""}
 
    return _err("Judge0 timed out waiting for result.")
 
 
def _err(msg: str) -> dict:
    return {"stdout": "", "stderr": "", "compile_error": "", "error": msg}
# ---------------- PLAYGROUND PAGE ---------------- #
@login_required(login_url='index')
def playground(request, problem_id):
    school_id = request.session.get('school_id')
    student = get_object_or_404(User, school_id=school_id)

    # Permission check
    if student.user_type != 'Student':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    problem = get_object_or_404(Problem, pk=problem_id)

    # Check if already submitted using ORM
    if Submission.objects.filter(problem_id=problem, student_id=student).exists():
        messages.warning(request, "You already submitted this problem.")
        return redirect('student_class_details', problem.class_id.class_id)

    context = {
        'user': student,
        'problem': problem,
        'nav': 'StudentPlayground',
        'clear_session_flag': True,
    }

    return render(request, 'Students/StudentPlayGround.html', context)


# ---------------- SUBMIT CODE (UNIFIED) ---------------- #
logger = logging.getLogger(__name__)

@csrf_exempt
def submit_problem(request, problem_id):
    """Handles BOTH manual and auto-submit — Judge0 edition"""
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)
 
    school_id = request.session.get("school_id")
    if not school_id:
        return JsonResponse(
            {"error": "Please log in first.", "redirect_url": reverse("index")},
            status=401,
        )
 
    student = get_object_or_404(User, school_id=school_id)
    problem = get_object_or_404(Problem, pk=problem_id)
 
    # Block duplicate submissions early
    existing = Submission.objects.filter(problem_id=problem, student_id=student).first()
    if existing:
        return JsonResponse(
            {
                "success": False,
                "error": "You have already submitted this problem.",
                "score": existing.score,
                "already_submitted": True,
                "redirect_url": reverse("student_class_details", args=[problem.class_id.class_id]),
            },
            status=400,
        )
 
    # Parse body
    try:
        data = json.loads(request.body.decode("utf-8"))
        code           = (data.get("code") or "").strip()
        language       = (data.get("language") or "python").lower()
        is_auto_submit = data.get("auto_submit", False)
        reason         = data.get("reason", "Manual submission")
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return JsonResponse({"error": f"Invalid data format: {e}"}, status=400)
 
    # Handle empty code (auto-submit only)
    if not code:
        if is_auto_submit:
            Submission.objects.create(
                problem_id=problem,
                student_id=student,
                code="// Auto-submitted with no code",
                score=0,
                submitted_at=timezone.now(),
            )
            return JsonResponse(
                {
                    "success": True,
                    "message": f"Auto-submitted ({reason}): No code provided",
                    "score": 0,
                    "passed": 0,
                    "total": 0,
                    "result_summary": "No code was submitted.",
                    "redirect_url": reverse("student_class_details", args=[problem.class_id.class_id]),
                }
            )
        return JsonResponse({"error": "Code cannot be empty."}, status=400)
 
    test_cases = ProblemTestCase.objects.filter(problem_id=problem)
    if not test_cases.exists():
        return JsonResponse({"error": "No test cases found for this problem."}, status=404)
 
    # Run each test case through Judge0
    passed = 0
    total  = test_cases.count()
    result_lines = []
 
    for i, tc in enumerate(test_cases, start=1):
        result = judge0_execute(language, code, stdin=tc.input_data or "")
 
        if result["error"]:
            result_lines.append(f"❌ Test {i}: Error — {result['error']}")
            continue
 
        if result["compile_error"]:
            result_lines.append(f"❌ Test {i}: Compilation Error")
            continue
 
        output   = (result["stdout"] or "").strip()
        expected = (tc.expected_output or "").strip()
 
        if output == expected:
            passed += 1
            result_lines.append(f"✅ Test {i}: Passed")
        else:
            result_lines.append(f"❌ Test {i}: Failed")
 
    score          = int((passed / total) * problem.total_score) if total else 0
    result_summary = "\n".join(result_lines)
 
    # Save (guard against race condition)
    try:
        submission, created = Submission.objects.get_or_create(
            problem_id=problem,
            student_id=student,
            defaults={"code": code, "score": score, "submitted_at": timezone.now()},
        )
        if not created:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Submission already exists (race condition).",
                    "score": submission.score,
                    "already_submitted": True,
                    "redirect_url": reverse("student_class_details", args=[problem.class_id.class_id]),
                },
                status=400,
            )
    except Exception as e:
        return JsonResponse({"error": f"Database error: {e}"}, status=500)
 
    submit_type = "Auto-submitted" if is_auto_submit else "Submitted"
    return JsonResponse(
        {
            "success": True,
            "message": f"{submit_type} successfully. Score: {score}/{problem.total_score}",
            "score": score,
            "passed": passed,
            "total": total,
            "result_summary": result_summary,
            "redirect_url": reverse("student_class_details", args=[problem.class_id.class_id]),
        }
    )

# ---------------- RUN & CHECK CODE (Testing only) ---------------- #


@csrf_exempt
def run_playground_code(request):
    """Test code without submitting — Judge0 edition"""
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)
 
    tmp_dir = None
    try:
        data       = json.loads(request.body)
        code       = data.get("code", "")
        language   = (data.get("language", "python") or "python").lower()
        check_mode = data.get("check_mode", False)
        problem_id = data.get("problem_id")
        stdin_data = data.get("stdin", "")
 
        if not code.strip():
            return JsonResponse({"error": "Code cannot be empty."}, status=400)
        if not problem_id:
            return JsonResponse({"error": "Problem ID is required."}, status=400)
 
        problem = get_object_or_404(Problem, pk=problem_id)
 
        # Write to temp file so execute_source can read it (keeps signature compatible)
        import tempfile, os, shutil
        tmp_dir = tempfile.mkdtemp(prefix="code_run_")
        extensions = {"python": "main.py", "c": "main.c", "cpp": "main.cpp", "java": "Main.java"}
        source_path = os.path.join(tmp_dir, extensions.get(language, "main.py"))
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(code)
 
        # ── CHECK MODE (run against test cases) ──────────────────────────────
        if check_mode:
            testcases   = list(ProblemTestCase.objects.filter(problem_id=problem))
            total_cases = len(testcases)
 
            if not total_cases:
                return JsonResponse({"success": False, "error": "No test cases found."}, status=404)
 
            results      = []
            passed_count = 0
 
            for i, tc in enumerate(testcases, start=1):
                raw_input = (tc.input_data or "").strip()
                expected  = (tc.expected_output or "").strip()
 
                exec_res = execute_source(language, source_path, stdin_data=raw_input)
 
                if exec_res.get("error"):
                    results.append(f"❌ Test {i}: {exec_res['error']}")
                    continue
                if exec_res.get("compile_error"):
                    results.append(f"❌ Test {i}: Compilation Error\n{exec_res['compile_error']}")
                    continue
                if exec_res.get("stderr"):
                    results.append(f"❌ Test {i}: Runtime Error\n{exec_res['stderr']}")
                    continue
 
                output = (exec_res.get("stdout") or "").strip()
 
                is_hidden = (
                    (total_cases == 1) or
                    (total_cases == 2 and i == 2) or
                    (total_cases == 3 and i == 3)
                )
 
                if output == expected:
                    passed_count += 1
                    label = "Passed (Hidden Case)" if is_hidden else "Passed"
                    results.append(f"✅ Test {i}: {label}")
                else:
                    if is_hidden:
                        results.append(f"❌ Test {i}: Failed (Hidden Case)")
                    else:
                        results.append(
                            f"❌ Test {i}: Failed\n"
                            f"Input: {raw_input}\n"
                            f"Expected: {expected}\n"
                            f"Got: {output}"
                        )
 
            return JsonResponse(
                {
                    "success": True,
                    "result_summary": "\n".join(results),
                    "total_score": passed_count * 10,
                    "passed": passed_count,
                    "total": total_cases,
                }
            )
 
        # ── MANUAL RUN MODE ───────────────────────────────────────────────────
        exec_res = execute_source(language, source_path, stdin_data=stdin_data)
        return JsonResponse(
            {
                "success": True,
                "output": exec_res.get("stdout") or "No output.",
                "stderr": exec_res.get("stderr", ""),
                "compile_error": exec_res.get("compile_error", ""),
                "error": exec_res.get("error", ""),
            }
        )
 
    except json.JSONDecodeError as e:
        return JsonResponse({"success": False, "error": f"Invalid JSON: {e}"}, status=400)
    except Problem.DoesNotExist:
        return JsonResponse({"success": False, "error": "Problem not found."}, status=404)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"success": False, "error": f"Server error: {e}"}, status=500)
    finally:
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def execute_source(language, source_path, stdin_data="", timeout_sec=5):

    try:
        with open(source_path, "r", encoding="utf-8") as f:
            code = f.read()
    except OSError as e:
        return _err(f"Could not read source file: {e}")
 
    return judge0_execute(language, code, stdin=stdin_data, timeout_sec=timeout_sec)


#----------------------Universal Playground--------------------------#

def code_testing_playground(request):
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    user = get_object_or_404(User, school_id=school_id)
    
    # Get classes for the user
    if user.user_type.lower() == 'teacher':
        user_classes = Class.objects.filter(teacher=user)
    else:
        user_classes = Class.objects.filter(enrollments__student_id=user).distinct()

    context = {
        'user': user,
        'user_classes': user_classes,
        'nav': 'Playground',
        'currentpage': 'Playground',
    }

    if user.user_type.lower() == 'teacher':
        return render(request, 'Universal/Playground.html', {**context, 'sidebar': 'teacher'})
    else:
        return render(request, 'Universal/Playground.html', {**context, 'sidebar': 'student'})


def get_playground_resources(request):
    """API endpoint to get study resources for the playground"""
    school_id = request.session.get('school_id')
    if not school_id:
        return JsonResponse({'success': False, 'error': 'Not authenticated'}, status=401)
    
    try:
        user = get_object_or_404(User, school_id=school_id)
        
        # Get classes the user is enrolled in/teaching
        if user.user_type.lower() == 'teacher':
            user_classes = Class.objects.filter(teacher=user)
        else:
            user_classes = Class.objects.filter(enrollments__student_id=user).distinct()
        
        # Get all resources for these classes
        resources = ProblemResource.objects.filter(
            problem__class_id__in=user_classes
        ).select_related('problem').order_by('-uploaded_at')
        
        # Group resources by title
        grouped_resources = {}
        for resource in resources:
            title = resource.title
            if title not in grouped_resources:
                grouped_resources[title] = {
                    'resource_id': resource.resource_id,
                    'title': resource.title,
                    'description': resource.description,
                    'class_id': resource.problem.class_id.class_id,
                    'class_title': resource.problem.class_id.title,
                    'uploaded_at': resource.uploaded_at.isoformat(),
                    'file_extension': resource.file_extension,
                    'files': []
                }
            
            grouped_resources[title]['files'].append({
                'resource_id': resource.resource_id,
                'original_filename': resource.original_filename,
                'file_url': resource.file.url,
                'file_size': resource.file_size,
                'file_extension': resource.file_extension,
                'uploaded_at': resource.uploaded_at.isoformat()
            })
        
        # Convert to list
        resources_list = list(grouped_resources.values())
        
        return JsonResponse({
            'success': True,
            'resources': resources_list,
            'count': len(resources_list)
        })
    
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def run_test_code(request):
    """Universal playground code execution — Judge0 edition"""
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)
 
    try:
        data       = json.loads(request.body)
        code       = data.get("code", "").strip()
        language   = (data.get("language", "python") or "python").lower()
        stdin_data = data.get("stdin", "")
 
        if not code:
            return JsonResponse({"error": "Code cannot be empty."}, status=400)
 
        # Fix Java class name before sending
        if language == "java":
            code = fix_java_class_name(code)
 
        # Parse input values for CodeChum-style formatting
        input_values = []
        if stdin_data:
            if "\n" in stdin_data:
                input_values = [v.strip() for v in stdin_data.split("\n") if v.strip()]
            else:
                input_values = [v.strip() for v in stdin_data.split() if v.strip()]
 
        result = judge0_execute(language, code, stdin=stdin_data)
 
        if result["error"]:
            return JsonResponse({"error": result["error"]}, status=500)
 
        raw_output     = result.get("stdout", "")
        formatted_output = format_codechum_style(raw_output, input_values, code, language)
 
        return JsonResponse(
            {
                "success": True,
                "output": formatted_output,
                "stderr": result.get("stderr", ""),
                "compile_error": result.get("compile_error", ""),
                "exit_code": 0,
            }
        )
 
    except requests.Timeout:
        return JsonResponse({"error": "Code execution timed out."}, status=408)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON data."}, status=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": f"Server error: {e}"}, status=500)


def fix_java_class_name(code):
    """Fix Java class name to Main"""
    lines = code.split('\n')
    modified_lines = []
    for line in lines:
        if 'public class' in line and '{' in line:
            before = line.split('public class')[0]
            after_parts = line.split('public class')[1].split('{', 1)
            if len(after_parts) == 2:
                modified_lines.append(before + 'public class Main {' + after_parts[1])
                continue
        modified_lines.append(line)
    return '\n'.join(modified_lines)


def format_codechum_style(raw_output, input_values, code, language):
    """Format output exactly like CodeChum - show user's exact code output"""
    if not raw_output:
        return ""

    if not input_values:
        return raw_output

    lines = raw_output.split('\n')
    result = []
    input_index = 0
    num_expected_inputs = count_inputs_in_code(code, language)
    prompt_lines = []

    # Strategy 1: Lines ending with : or ?
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if (stripped.endswith(':') or stripped.endswith('?')) and input_index < len(input_values):
            prompt_lines.append(i)
            input_index += 1

    # Strategy 2: Keyword-based detection
    if len(prompt_lines) < min(num_expected_inputs, len(input_values)):
        input_index = len(prompt_lines)
        keywords = ['enter', 'input', 'type', 'give', 'provide', 'number', 'value', 'element', 'size', 'name', 'age', 'data', 'num']

        for i, line in enumerate(lines):
            if i not in prompt_lines:
                if any(kw in line.strip().lower() for kw in keywords) and input_index < len(input_values):
                    prompt_lines.append(i)
                    input_index += 1

    # Strategy 3: First N non-output lines
    if len(prompt_lines) < min(num_expected_inputs, len(input_values)):
        input_index = len(prompt_lines)
        output_words = ['sum', 'result', 'total', 'answer', 'output', 'product', 'difference', 'quotient']

        for i, line in enumerate(lines):
            if i not in prompt_lines and line.strip() and input_index < len(input_values):
                is_output = (
                    re.match(r'^\d+(\.\d+)?$', line.strip()) or
                    any(w in line.lower() for w in output_words) or
                    re.match(r'^[A-Z][a-z]+\s+is\s+', line.strip())
                )
                if not is_output:
                    prompt_lines.append(i)
                    input_index += 1

    prompt_lines.sort()
    input_index = 0

    # Format output
    for i, line in enumerate(lines):
        if i in prompt_lines and input_index < len(input_values):
            result.append(line)
            input_index += 1
        else:
            result.append(line)

    return '\n'.join(result)


def count_inputs_in_code(code, language):
    """Count expected inputs"""
    count = 0
    for line in code.split('\n'):
        if language == 'python' and 'input(' in line:
            count += 1
        elif language == 'c' and 'scanf' in line:
            count += len(re.findall(r'%[dfscilfg]', line))
        elif language == 'cpp' and 'cin' in line and '>>' in line:
            count += line.count('>>')
        elif language == 'java' and re.search(r'\.next(Int|Line|Double|Float|Boolean|Long)\s*\(', line):
            count += 1
    return count

#---------------END OF CONSOLE CODE------------------------------------#


#---------------------------AI-----------------------------------------# Initialize client
client = genai.Client(api_key="AIzaSyDOpX6P-bHaqdiXNX8S2mXbQwrGj_EB5w4")
# API Rate Limits for Gemini 2.5 Flash-Lite
API_LIMITS = {
    'requests_per_minute': 15,      # RPM: 15
    'requests_per_day': 1000,       # RPD: 1000
    'tokens_per_minute': 250000,    # TPM: 250,000
}

def get_total_users():
    """Get total number of registered users"""
    from .models import User
    count = User.objects.count()
    return max(count, 1)  # Minimum 1 to avoid division by zero

def get_per_user_limits():
    """Calculate per-user limits by dividing API limits by total users"""
    total_users = get_total_users()

    return {
        'requests_per_minute': max(1, API_LIMITS['requests_per_minute'] // total_users),
        'requests_per_day': max(1, API_LIMITS['requests_per_day'] // total_users),
        'tokens_per_minute': max(1000, API_LIMITS['tokens_per_minute'] // total_users),
        'total_users': total_users,
    }

def check_rate_limit(user_id):
    """
    Check if user has exceeded their allocated rate limits
    Returns: (allowed: bool, message: str, wait_seconds: int)
    """
    now = datetime.now()
    limits = get_per_user_limits()

    # Per-minute check
    minute_key = f"ai_rate_limit_minute_{user_id}_{now.strftime('%Y%m%d%H%M')}"
    minute_count = cache.get(minute_key, 0)

    if minute_count >= limits['requests_per_minute']:
        wait_seconds = 60 - now.second
        return False, f"Rate limit: {limits['requests_per_minute']} requests/minute (shared among {limits['total_users']} users). Wait {wait_seconds}s.", wait_seconds

    # Per-day check
    day_key = f"ai_rate_limit_day_{user_id}_{now.strftime('%Y%m%d')}"
    day_count = cache.get(day_key, 0)

    if day_count >= limits['requests_per_day']:
        return False, f"Daily limit reached ({limits['requests_per_day']} requests per user). Try tomorrow.", 0

    return True, "OK", 0


def increment_rate_limit(user_id):
    """Increment rate limit counters"""
    now = datetime.now()

    # Increment minute counter
    minute_key = f"ai_rate_limit_minute_{user_id}_{now.strftime('%Y%m%d%H%M')}"
    minute_count = cache.get(minute_key, 0)
    cache.set(minute_key, minute_count + 1, 70)  # Expire after 70 seconds

    # Increment day counter
    day_key = f"ai_rate_limit_day_{user_id}_{now.strftime('%Y%m%d')}"
    day_count = cache.get(day_key, 0)
    cache.set(day_key, day_count + 1, 86400)  # Expire after 24 hours


def get_user_usage_stats(user_id):
    """Get current usage statistics for user with dynamic limits"""
    now = datetime.now()
    limits = get_per_user_limits()

    minute_key = f"ai_rate_limit_minute_{user_id}_{now.strftime('%Y%m%d%H%M')}"
    day_key = f"ai_rate_limit_day_{user_id}_{now.strftime('%Y%m%d')}"

    minute_count = cache.get(minute_key, 0)
    day_count = cache.get(day_key, 0)

    return {
        'requests_this_minute': minute_count,
        'requests_today': day_count,
        'minute_limit': limits['requests_per_minute'],
        'daily_limit': limits['requests_per_day'],
        'minute_remaining': max(0, limits['requests_per_minute'] - minute_count),
        'daily_remaining': max(0, limits['requests_per_day'] - day_count),
        'total_users': limits['total_users'],
        'tokens_per_minute_limit': limits['tokens_per_minute'],
        # API totals for reference
        'api_limits': {
            'rpm': API_LIMITS['requests_per_minute'],
            'rpd': API_LIMITS['requests_per_day'],
            'tpm': API_LIMITS['tokens_per_minute'],
        }
    }


# Optimized system prompt (same as before)
SYSTEM_PROMPT_OPTIMIZED = """You are Paulibot, an intelligent AI assistant exclusive to the Paulicode platform.

**Your Identity:**
- Name: Paulibot
- Purpose: Personal AI coding assistant for students
- Created by: A Paulinian IT student named 'Bryan Kim Calipes'
- Platform: Paulicode - an educational coding platform
- Expertise: Programming, algorithms, debugging, code explanation, and Computer Science/IT concepts ONLY

**CRITICAL RESTRICTIONS:**

1. **TOPIC FILTERING:**
You MUST ONLY answer questions related to:
 Programming (Python, C, C++, Java, JavaScript, etc.),
 Computer Science concepts (algorithms, data structures, complexity, etc.),
 Software development (debugging, testing, version control, etc.),
 Web development (HTML, CSS, frameworks, databases, etc.),
 IT concepts (networks, security, systems, DevOps, etc.)

 REFUSE to answer questions about:
- General knowledge, trivia, entertainment, news, health, history, or non-technical topics

**Response when asked off-topic:**
"I can only help with programming and computer science topics. Please ask me something about coding! 💻"

2. **RESPONSE LENGTH (CRITICAL - LOW TOKEN LIMIT):**
   **KEEP ALL RESPONSES CONCISE** - You have limited tokens
- Maximum 200 words per response
- Use short sentences
- Get straight to the point
- For code examples: Keep them minimal (10-15 lines max)
- Avoid lengthy explanations
- No repetition

**Response Guidelines:**
- Be direct and concise
- Use markdown code blocks for code (```language)
- Break complex concepts into digestible parts
- Provide SHORT code examples when helpful
- Always be encouraging but brief

Remember: CONCISE, TECHNICAL, CODING-FOCUSED ONLY!
"""


@csrf_exempt
def ai_chat_stream(request):
    """
    Streams AI responses with dynamic per-user rate limiting
    Uses Gemini 2.5 Flash-Lite: 15 RPM, 250K TPM, 1000 RPD
    Limits are divided equally among all registered users
    """
  #  from .models import User, ChatHistory
   # from google import genai

    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=400)

    school_id = request.session.get('school_id')
    if not school_id:
        return JsonResponse({"error": "Please log in first."}, status=401)

    try:
        user = User.objects.get(school_id=school_id)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found."}, status=404)

    # âœ… Check dynamic rate limits
    allowed, message, wait_seconds = check_rate_limit(user.school_id)
    if not allowed:
        return JsonResponse({
            "error": message,
            "rate_limited": True,
            "wait_seconds": wait_seconds
        }, status=429)

    try:
        data = json.loads(request.body)
        user_message = data.get("message", "").strip()

        if not user_message:
            return JsonResponse({"error": "Message cannot be empty."}, status=400)

        # Calculate dynamic max message length based on per-user token limit
        limits = get_per_user_limits()
        max_message_length = min(500, limits['tokens_per_minute'] // 500)  # Conservative estimate

        if len(user_message) > max_message_length:
            return JsonResponse({
                "error": f"Message too long. Please keep it under {max_message_length} characters."
            }, status=400)

        # âœ… Increment rate limit counter
        increment_rate_limit(user.school_id)

        # Save user's message
        ChatHistory.objects.create(
            school_id=user,
            sender='user',
            message=user_message
        )

        # Get reduced chat history (last 4 messages)
        conversation_history = ChatHistory.objects.filter(
            school_id=user
        ).order_by('-timestamp')[:4]

        conversation_history = list(reversed(conversation_history))

        def stream_response():
            ai_response = ""
            try:
                # Calculate max tokens based on per-user TPM allocation
                per_user_limits = get_per_user_limits()
                max_output_tokens = min(800, per_user_limits['tokens_per_minute'] // 20)

                # Build minimal prompt
                full_prompt = SYSTEM_PROMPT_OPTIMIZED + "\n\n"

                if conversation_history:
                    full_prompt += "Recent context:\n"
                    for msg in conversation_history[-4:]:
                        prefix = "Student" if msg.sender == "user" else "Paulibot"
                        msg_text = msg.message[:150] + "..." if len(msg.message) > 150 else msg.message
                        full_prompt += f"{prefix}: {msg_text}\n"
                    full_prompt += "\n"

                full_prompt += f"Student: {user_message}\nPaulibot: (Keep response under 200 words)"


                # âœ… Stream using Gemini 2.5 Flash-Lite
                for chunk in client.models.generate_content_stream(
                    model="gemini-2.5-flash-lite",
                    contents=full_prompt,
                    config={
                        "temperature": 0.7,
                        "top_p": 0.95,
                        "top_k": 40,
                        "max_output_tokens": max_output_tokens,
                        "stop_sequences": ["\n\nStudent:", "Student:"],
                    }
                ):
                    if hasattr(chunk, "text") and chunk.text:
                        ai_response += chunk.text
                        yield json.dumps({"ai_message_partial": chunk.text}) + "\n"

                # Save AI's complete response
                if ai_response:
                    ChatHistory.objects.create(
                        school_id=user,
                        sender='ai',
                        message=ai_response
                    )

            except Exception as e:
                error_msg = f"Error: {str(e)}"
                yield json.dumps({"ai_message_partial": error_msg}) + "\n"

                ChatHistory.objects.create(
                    school_id=user,
                    sender='ai',
                    message=error_msg
                )

        return StreamingHttpResponse(
            stream_response(),
            content_type="text/event-stream"
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def get_usage_stats(request):
    """
    API endpoint to get current user's usage statistics with dynamic limits
    """
   # from .models import User

    school_id = request.session.get('school_id')
    if not school_id:
        return JsonResponse({"error": "Not logged in"}, status=401)

    try:
        user = User.objects.get(school_id=school_id)
        stats = get_user_usage_stats(user.school_id)
        return JsonResponse(stats)
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)


def load_chat_history(request):
    """Load user's private chat history from database"""
   # from .models import User, ChatHistory

    school_id_value = request.session.get('school_id')
    if not school_id_value:
        return JsonResponse({"error": "Please log in first."}, status=401)

    try:
        user = User.objects.get(school_id=school_id_value)

        # Get user's chat history (last 30 messages)
        chat_history = ChatHistory.objects.filter(
            school_id=user
        ).order_by('timestamp')[:30]

        # Format for frontend
        messages = [
            {
                "sender": msg.sender,
                "text": msg.message,
                "timestamp": msg.timestamp.isoformat()
            }
            for msg in chat_history
        ]

        return JsonResponse({"messages": messages})

    except User.DoesNotExist:
        return JsonResponse({"error": "User not found."}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def clear_chat_history(request):
    """Clear user's private chat history"""
    #from .models import User, ChatHistory

    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)

    school_id_value = request.session.get('school_id')
    if not school_id_value:
        return JsonResponse({"error": "Please log in first."}, status=401)

    try:
        user = User.objects.get(school_id=school_id_value)

        # Delete all chat history for this user
        deleted_count = ChatHistory.objects.filter(school_id=user).delete()[0]

        return JsonResponse({
            "success": True,
            "message": f"Deleted {deleted_count} messages."
        })

    except User.DoesNotExist:
        return JsonResponse({"error": "User not found."}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)



# ============================================
# CYBERSECURITY-SPECIFIC VIEWS
# ============================================

@login_required(login_url='index')
def add_cybersecurity_challenge(request, class_id):
    """Add a cybersecurity challenge with file upload support (including HTML) and correct answer"""
    school_id = request.session.get('school_id')
    if not school_id:
        messages.warning(request, "Please log in first.")
        return redirect('index')

    teacher = get_object_or_404(User, school_id=school_id)
    class_obj = get_object_or_404(Class, class_id=class_id, teacher=teacher)

    # Verify it's a cybersecurity class
    if class_obj.class_type != 'cybersecurity':
        messages.error(request, "This endpoint is only for cybersecurity classes.")
        return redirect('classDetails', class_id=class_id)

    if request.method == "POST":
        title = request.POST.get("problem_title", "").strip()
        description = request.POST.get("problem_description", "").strip()
        problem_type = request.POST.get("problem_type", "").strip()
        total_score = request.POST.get("total_score", "").strip()
        due_date = request.POST.get("due_date", "").strip()
        challenge_file = request.FILES.get("challenge_file")
        correct_answer = request.POST.get("correct_answer", "").strip()

        # Validation
        if not all([title, description, problem_type, total_score, due_date, correct_answer]):
            messages.error(request, "Please fill in all required fields including correct answer.")
            return redirect('classDetails', class_id=class_id)

        try:
            total_score = int(total_score)
            due_date = datetime.fromisoformat(due_date)
        except ValueError:
            messages.error(request, "Invalid input values.")
            return redirect('classDetails', class_id=class_id)

        # Handle file upload (including HTML files)
        if challenge_file:
            # Check file size (10MB max)
            if challenge_file.size > 10 * 1024 * 1024:
                messages.error(request, "File size exceeds 10MB limit.")
                return redirect('classDetails', class_id=class_id)

            # Get file extension
            file_ext = challenge_file.name.split('.')[-1].lower()

            # Check file extension
            allowed_extensions = ['pdf', 'png', 'jpg', 'jpeg', 'docx', 'pptx', 'ppt', 'zip', 'txt', 'html', 'htm']
            if file_ext not in allowed_extensions:
                messages.error(request, "Invalid file type. Allowed: PDF, Images, DOCX, PPTX, ZIP, TXT, HTML")
                return redirect('classDetails', class_id=class_id)

            # Special handling for HTML files
            if file_ext in ['html', 'htm']:
                # Use MEDIA_ROOT instead of hardcoded path
                challenges_dir = os.path.join(settings.MEDIA_ROOT, 'challenges')

                # Create directory if it doesn't exist
                os.makedirs(challenges_dir, exist_ok=True)

                # Generate unique filename to avoid conflicts
                import uuid
                unique_filename = f"{uuid.uuid4().hex}_{challenge_file.name}"
                file_path = os.path.join(challenges_dir, unique_filename)

                # Save HTML file to media/challenges
                with open(file_path, 'wb+') as destination:
                    for chunk in challenge_file.chunks():
                        destination.write(chunk)

                # Store relative path for the model
                challenge_file_path = f'challenges/{unique_filename}'

                # Create Problem with HTML file path
                problem = Problem.objects.create(
                    class_id=class_obj,
                    teacher_id=teacher,
                    problem_title=title,
                    problem_description=description,
                    problem_type=problem_type,
                    total_score=total_score,
                    time_limit=None,
                    due_date=due_date,
                    challenge_file=challenge_file_path,  # Store path as string
                    correct_answer=correct_answer,
                )

                messages.success(request, f"Challenge '{title}' created with HTML file '{challenge_file.name}'!")
                return redirect('classDetails', class_id=class_id)

            # Regular file upload (non-HTML)
            else:
                problem = Problem.objects.create(
                    class_id=class_obj,
                    teacher_id=teacher,
                    problem_title=title,
                    problem_description=description,
                    problem_type=problem_type,
                    total_score=total_score,
                    time_limit=None,
                    due_date=due_date,
                    challenge_file=challenge_file,  # Django handles regular uploads
                    correct_answer=correct_answer,
                )
        else:
            # No file uploaded
            problem = Problem.objects.create(
                class_id=class_obj,
                teacher_id=teacher,
                problem_title=title,
                problem_description=description,
                problem_type=problem_type,
                total_score=total_score,
                time_limit=None,
                due_date=due_date,
                correct_answer=correct_answer,
            )

        messages.success(request, f"Challenge '{title}' created successfully!")
        return redirect('classDetails', class_id=class_id)

    return redirect('classDetails', class_id=class_id)


def get_challenge_file_url(problem):
    """
    Returns the appropriate URL for challenge files
    All files are now in media/challenges
    """
    if not problem.challenge_file:
        return None

    file_path = str(problem.challenge_file)

    # All challenge files use MEDIA_URL
    if file_path.startswith('challenges/'):
        return f"{settings.MEDIA_URL}{file_path}"

    # Fallback for old files or regular uploads
    try:
        return problem.challenge_file.url
    except:
        return None

@login_required(login_url='index')
def edit_cybersecurity_challenge(request, problem_id):
    """Edit a cybersecurity challenge"""
    problem = get_object_or_404(Problem, pk=problem_id)
    class_id = problem.class_id.class_id

    # Verify it's a cybersecurity challenge
    if problem.class_id.class_type != 'cybersecurity':
        messages.error(request, "This endpoint is only for cybersecurity challenges.")
        return redirect('classDetails', class_id=class_id)

    if request.method == "POST":
        title = request.POST.get("problem_title", "").strip()
        description = request.POST.get("problem_description", "").strip()
        problem_type = request.POST.get("problem_type", "").strip()
        total_score = request.POST.get("total_score", "").strip()
        due_date = request.POST.get("due_date", "").strip()
        challenge_file = request.FILES.get("challenge_file")
        correct_answer = request.POST.get("correct_answer", "").strip()

        try:
            problem.problem_title = title
            problem.problem_description = description
            problem.problem_type = problem_type
            problem.total_score = int(total_score)
            problem.due_date = datetime.fromisoformat(due_date)

            # Update correct answer if provided
            if correct_answer:
                problem.correct_answer = correct_answer

            # Update file only if a new one is provided
            if challenge_file:
                # Delete old file first if exists
                if problem.challenge_file:
                    old_file_path = str(problem.challenge_file)
                    if old_file_path.startswith('challenges/'):
                        full_path = os.path.join(settings.MEDIA_ROOT, old_file_path)
                        if os.path.exists(full_path):
                            os.remove(full_path)
                    else:
                        try:
                            problem.challenge_file.delete(save=False)
                        except:
                            pass

                # Check file size
                if challenge_file.size > 10 * 1024 * 1024:
                    messages.error(request, "File size exceeds 10MB limit.")
                    return redirect('classDetails', class_id=class_id)

                # Get file extension
                file_ext = challenge_file.name.split('.')[-1].lower()
                allowed_extensions = ['pdf', 'png', 'jpg', 'jpeg', 'docx', 'pptx', 'ppt', 'zip', 'txt', 'html', 'htm']

                if file_ext not in allowed_extensions:
                    messages.error(request, "Invalid file type.")
                    return redirect('classDetails', class_id=class_id)

                # Handle HTML files
                if file_ext in ['html', 'htm']:
                    challenges_dir = os.path.join(settings.MEDIA_ROOT, 'challenges')
                    os.makedirs(challenges_dir, exist_ok=True)

                    import uuid
                    unique_filename = f"{uuid.uuid4().hex}_{challenge_file.name}"
                    file_path = os.path.join(challenges_dir, unique_filename)

                    with open(file_path, 'wb+') as destination:
                        for chunk in challenge_file.chunks():
                            destination.write(chunk)

                    problem.challenge_file = f'challenges/{unique_filename}'
                else:
                    # Regular file
                    problem.challenge_file = challenge_file

            problem.save()

            messages.success(request, f"Challenge '{problem.problem_title}' updated successfully!")
        except Exception as e:
            messages.error(request, f"Update failed: {e}")

    return redirect('classDetails', class_id=class_id)


def get_cybersecurity_problem_details(request, problem_id):
    """Return cybersecurity challenge details as JSON with proper HTML file handling"""
    problem = get_object_or_404(Problem, pk=problem_id)

    # Verify it's a cybersecurity challenge
    if problem.class_id.class_type != 'cybersecurity':
        return JsonResponse({"error": "Not a cybersecurity challenge"}, status=400)

    # Check if student has submitted a CORRECT answer
    answered_correctly = False
    school_id = request.session.get('school_id')
    if school_id:
        user = User.objects.filter(school_id=school_id).first()
        if user and user.user_type == "Student":
            answered_correctly = Submission.objects.filter(
                problem_id=problem,
                student_id=user,
                score=problem.total_score
            ).exists()

    # Handle challenge file URL properly (including HTML files)
    challenge_file_url = None
    challenge_file_name = None

    if problem.challenge_file:
        file_path = str(problem.challenge_file)

        # All files now use MEDIA_URL
        if file_path.startswith('challenges/'):
            challenge_file_url = f'{settings.MEDIA_URL}{file_path}'
            challenge_file_name = file_path.split('/')[-1]
            # Remove UUID prefix from display name if present
            if '_' in challenge_file_name:
                parts = challenge_file_name.split('_', 1)
                if len(parts) == 2 and len(parts[0]) == 32:  # UUID is 32 chars
                    challenge_file_name = parts[1]
        else:
            # Regular uploaded file (uses MEDIA_URL)
            try:
                challenge_file_url = problem.challenge_file.url
                challenge_file_name = problem.challenge_file.name.split('/')[-1]
            except:
                challenge_file_url = None
                challenge_file_name = file_path.split('/')[-1]

    # Prepare data
    data = {
        "problem_id": problem.problem_id,
        "title": problem.problem_title,
        "description": problem.problem_description,
        "type": problem.problem_type,
        "score": problem.total_score,
        "due_date": problem.due_date.strftime("%Y-%m-%d %H:%M"),
        "answered": answered_correctly,
        "challenge_file": challenge_file_url,
        "challenge_file_name": challenge_file_name,
        "currentpage": "cybersec"
    }

    return JsonResponse(data)


@csrf_exempt
@login_required(login_url='index')
def submit_cybersecurity_answer(request, problem_id):
    """Submit answer for cybersecurity challenge - ONLY saves correct answers"""
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request method."}, status=400)

    school_id = request.session.get("school_id")
    if not school_id:
        return JsonResponse({
            "error": "Please log in first.",
            "redirect_url": reverse("index")
        }, status=401)

    student = get_object_or_404(User, school_id=school_id)
    problem = get_object_or_404(Problem, pk=problem_id)

    # Verify it's a cybersecurity challenge
    if problem.class_id.class_type != 'cybersecurity':
        return JsonResponse({"error": "Not a cybersecurity challenge"}, status=400)

    # Parse request body
    try:
        data = json.loads(request.body)
        answer_text = (data.get("answer") or "").strip()

        if not answer_text:
            return JsonResponse({
                "error": "Answer cannot be empty.",
            }, status=400)

        # Check if already answered correctly
        existing_correct_submission = Submission.objects.filter(
            problem_id=problem,
            student_id=student,
            score=problem.total_score  # Full score = correct answer
        ).first()

        if existing_correct_submission:
            return JsonResponse({
                "success": False,
                "is_correct": True,
                "score": problem.total_score,
                "total_score": problem.total_score,
                "message": "You have already solved this challenge correctly!",
                "already_solved": True
            })

        # Check if answer is correct (case-insensitive, strip whitespace)
        correct_answer = (problem.correct_answer or "").strip().lower()
        student_answer = answer_text.strip().lower()
        is_correct = (student_answer == correct_answer)

        # Calculate score based on correctness
        score = problem.total_score if is_correct else 0

        # ONLY SAVE TO DATABASE IF CORRECT
        if is_correct:
            submission = Submission.objects.create(
                problem_id=problem,
                student_id=student,
                answer_text=answer_text,
                code=None,  # No code for cybersecurity
                score=score,
                status='Graded',
                submitted_at=timezone.now()
            )

            return JsonResponse({
                "success": True,
                "is_correct": True,
                "score": score,
                "total_score": problem.total_score,
                "message": "Correct! Answer saved successfully!",
            })
        else:
            # Wrong answer - DO NOT SAVE, just return feedback
            return JsonResponse({
                "success": True,
                "is_correct": False,
                "score": 0,
                "total_score": problem.total_score,
                "message": "Incorrect answer. Try again!",
            })

    except json.JSONDecodeError as e:
        return JsonResponse({
            "error": f"Invalid data format: {str(e)}",
        }, status=400)
    except Exception as e:
        return JsonResponse({
            "error": f"Server error: {str(e)}",
        }, status=500)


@login_required(login_url='index')
def get_cybersecurity_submissions(request, problem_id):
    """Get all submissions for a student for a specific cybersecurity challenge"""
    school_id = request.session.get("school_id")
    if not school_id:
        return JsonResponse({"error": "Please log in first."}, status=401)

    student = get_object_or_404(User, school_id=school_id)
    problem = get_object_or_404(Problem, pk=problem_id)

    # Get all submissions for this student and problem
    submissions = Submission.objects.filter(
        problem_id=problem,
        student_id=student
    ).order_by('-submitted_at')

    submissions_data = [{
        'submission_id': sub.submission_id,
        'answer_text': sub.answer_text,
        'score': sub.score,
        'total_score': problem.total_score,
        'is_correct': sub.score == problem.total_score,
        'submitted_at': sub.submitted_at.isoformat(),
    } for sub in submissions]

    return JsonResponse({
        "submissions": submissions_data
    })


@login_required(login_url='index')
def leaderboard(request):
    """
    Display leaderboard with filtering by class
    Works for both teachers and students
    """
    user = request.user
    selected_class_id = request.GET.get('class_id', None)

    # Get classes based on user type
    if user.user_type == 'Teacher':
        user_classes = Class.objects.filter(teacher=user).order_by('title')
    else:  # Student
        user_classes = Class.objects.filter(
            enrollments__student_id=user
        ).distinct().order_by('title')

    # Get selected class if class_id is provided
    selected_class = None
    if selected_class_id:
        try:
            if user.user_type == 'Teacher':
                selected_class = Class.objects.get(
                    class_id=selected_class_id,
                    teacher=user
                )
            else:
                selected_class = Class.objects.get(
                    class_id=selected_class_id,
                    enrollments__student_id=user
                )
        except Class.DoesNotExist:
            selected_class = None

    # Build leaderboard query - using student_id instead of values()
    leaderboard_query = Submission.objects.select_related('student_id')

    # Filter by class if selected
    if selected_class:
        leaderboard_query = leaderboard_query.filter(
            problem_id__class_id=selected_class
        )
    else:
        # Filter by user's accessible classes
        if user.user_type == 'Teacher':
            leaderboard_query = leaderboard_query.filter(
                problem_id__class_id__teacher=user
            )
        else:
            leaderboard_query = leaderboard_query.filter(
                problem_id__class_id__in=user_classes
            )

    # Aggregate scores - Get distinct students with their total scores


    # Get all students with submissions
    student_scores = {}
    for submission in leaderboard_query:
        student_id = submission.student_id.school_id
        if student_id not in student_scores:
            student_scores[student_id] = {
                'student': submission.student_id,
                'total_score': 0
            }
        student_scores[student_id]['total_score'] += submission.score or 0

    # Convert to list and sort by score
    leaderboard_data = []
    for student_id, data in student_scores.items():
        student = data['student']
        leaderboard_data.append({
            'student_id__school_id': student.school_id,
            'student_id__first_name': student.first_name,
            'student_id__last_name': student.last_name,
            'student_id__user_image': student.user_image.url if student.user_image else None,
            'total_score': data['total_score']
        })

    # Sort by total score descending
    leaderboard_data.sort(key=lambda x: x['total_score'], reverse=True)

    # Calculate current user's rank (only for students)
    user_rank = None
    if user.user_type == 'Student':
        for index, entry in enumerate(leaderboard_data, start=1):
            if entry['student_id__school_id'] == user.school_id:
                user_rank = index
                break

    context = {
        'currentpage': 'leaderboard',
        'user': user,
        'user_classes': user_classes,
        'selected_class': selected_class,
        'leaderboard_data': leaderboard_data,
        'user_rank': user_rank,
        'sidebar': 'teacher' if user.user_type == 'Teacher' else 'student',
    }

    return render(request, 'Universal/leaderboard.html', context)

@login_required(login_url='index')
def leaderboard_data_api(request):
    """
    API endpoint that returns leaderboard data as JSON for real-time updates
    """
    user = request.user
    selected_class_id = request.GET.get('class_id', None)

    # Get classes based on user type
    if user.user_type == 'Teacher':
        user_classes = Class.objects.filter(teacher=user).order_by('title')
    else:  # Student
        user_classes = Class.objects.filter(
            enrollments__student_id=user
        ).distinct().order_by('title')

    # Get selected class if class_id is provided
    selected_class = None
    if selected_class_id:
        try:
            if user.user_type == 'Teacher':
                selected_class = Class.objects.get(
                    class_id=selected_class_id,
                    teacher=user
                )
            else:
                selected_class = Class.objects.get(
                    class_id=selected_class_id,
                    enrollments__student_id=user
                )
        except Class.DoesNotExist:
            selected_class = None

    # Build leaderboard query
    leaderboard_query = Submission.objects.select_related('student_id')

    # Filter by class if selected
    if selected_class:
        leaderboard_query = leaderboard_query.filter(
            problem_id__class_id=selected_class
        )
    else:
        # Filter by user's accessible classes
        if user.user_type == 'Teacher':
            leaderboard_query = leaderboard_query.filter(
                problem_id__class_id__teacher=user
            )
        else:
            leaderboard_query = leaderboard_query.filter(
                problem_id__class_id__in=user_classes
            )

    # Aggregate scores - Get distinct students with their total scores
    student_scores = {}
    for submission in leaderboard_query:
        student_id = submission.student_id.school_id
        if student_id not in student_scores:
            student_scores[student_id] = {
                'student': submission.student_id,
                'total_score': 0
            }
        student_scores[student_id]['total_score'] += submission.score or 0

    # Convert to list and sort by score
    leaderboard_data = []
    for student_id, data in student_scores.items():
        student = data['student']
        leaderboard_data.append({
            'student_id__school_id': student.school_id,
            'student_id__first_name': student.first_name,
            'student_id__last_name': student.last_name,
            'student_id__user_image': student.user_image.url if student.user_image else None,
            'total_score': data['total_score']
        })

    # Sort by total score descending
    leaderboard_data.sort(key=lambda x: x['total_score'], reverse=True)

    # Calculate current user's rank (only for students)
    user_rank = None
    if user.user_type == 'Student':
        for index, entry in enumerate(leaderboard_data, start=1):
            if entry['student_id__school_id'] == user.school_id:
                user_rank = index
                break

    # Prepare response data
    response_data = {
        'leaderboard_data': leaderboard_data,
        'user_rank': user_rank,
        'selected_class': {
            'class_id': selected_class.class_id,
            'title': selected_class.title
        } if selected_class else None,
        'timestamp': timezone.now().isoformat()
    }

    return JsonResponse(response_data, safe=False)

@login_required(login_url='index')
def get_student_total_exp(request):
    """
    API endpoint to get student's total EXP (sum of all submission scores)
    """
    user = request.user

    # Only allow students to access this endpoint
    if user.user_type != 'Student':
        return JsonResponse({"error": "Only students can access this endpoint"}, status=403)

    # Calculate total EXP from all submissions
    total_exp = Submission.objects.filter(
        student_id=user
    ).aggregate(
        total=Sum('score')
    )['total'] or 0

    return JsonResponse({
        'total_exp': total_exp,
        'student_name': f"{user.first_name} {user.last_name}",
        'school_id': user.school_id
    })

@login_required(login_url='index')
@csrf_exempt
def delete_challenge_file(request, problem_id):
    """Delete the challenge file from a cybersecurity problem"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    school_id = request.session.get('school_id')
    teacher = get_object_or_404(User, school_id=school_id)

    # Get the problem and verify ownership
    problem = get_object_or_404(Problem, pk=problem_id)

    if problem.class_id.teacher != teacher:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    if not problem.challenge_file:
        return JsonResponse({'error': 'No file to delete'}, status=400)

    try:
        file_path = str(problem.challenge_file)

        # All files are now in media/challenges
        if file_path.startswith('challenges/'):
            full_path = os.path.join(settings.MEDIA_ROOT, file_path)
            if os.path.exists(full_path):
                os.remove(full_path)
        else:
            # Delete regular uploaded file
            if problem.challenge_file:
                try:
                    problem.challenge_file.delete(save=False)
                except:
                    pass

        # Clear the challenge_file field
        problem.challenge_file = None
        problem.save()

        return JsonResponse({
            'success': True,
            'message': 'File deleted successfully'
        })

    except Exception as e:
        return JsonResponse({
            'error': f'Failed to delete file: {str(e)}'
        }, status=500)