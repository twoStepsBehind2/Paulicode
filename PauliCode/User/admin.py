# User/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.http import HttpResponse
from .models import User, Class, Enrollment, Problem, ProblemTestCase, Submission, ChatHistory, ProblemResource
from django.contrib.admin import SimpleListFilter
from django.db.models import Count, Sum, Avg, Q
import csv

# NOTE: PauliCodeAdminSite lives in User/admin_site.py and is wired up as the
# default admin site via User.admin_config.PauliCodeAdminConfig (default_site).
# Do NOT reassign admin.site here — that would create an empty site and hide
# all models from /admin/.

# ============ CUSTOM FILTERS ============
class UserTypeFilter(SimpleListFilter):
    title = 'User Type'
    parameter_name = 'user_type'
    
    def lookups(self, request, model_admin):
        return [
            ('Teacher', 'Teachers'),
            ('Student', 'Students'),
        ]
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(user_type=self.value())
        return queryset


class SubmissionStatusFilter(SimpleListFilter):
    title = 'Submission Status'
    parameter_name = 'status'
    
    def lookups(self, request, model_admin):
        return [
            ('Pending', 'Pending Review'),
            ('Graded', 'Graded'),
            ('Reviewed', 'Reviewed'),
        ]
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class SubmissionScoreFilter(SimpleListFilter):
    title = 'Score Range'
    parameter_name = 'score_range'
    
    def lookups(self, request, model_admin):
        return [
            ('90-100', 'Excellent (90-100)'),
            ('80-89', 'Very Good (80-89)'),
            ('70-79', 'Good (70-79)'),
            ('50-69', 'Satisfactory (50-69)'),
            ('0-49', 'Needs Improvement (0-49)'),
        ]
    
    def queryset(self, request, queryset):
        if self.value() == '90-100':
            return queryset.filter(score__gte=90, score__lte=100)
        elif self.value() == '80-89':
            return queryset.filter(score__gte=80, score__lt=90)
        elif self.value() == '70-79':
            return queryset.filter(score__gte=70, score__lt=80)
        elif self.value() == '50-69':
            return queryset.filter(score__gte=50, score__lt=70)
        elif self.value() == '0-49':
            return queryset.filter(score__lt=50)
        return queryset


class ClassTypeFilter(SimpleListFilter):
    title = 'Class Type'
    parameter_name = 'class_type'
    
    def lookups(self, request, model_admin):
        return [
            ('programming', 'Programming'),
            ('cybersecurity', 'Cybersecurity'),
        ]
    
    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(class_type=self.value())
        return queryset


# ============ CUSTOM ACTIONS ============
@admin.action(description='Export selected users to CSV')
def export_users_to_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="users.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['School ID', 'First Name', 'Last Name', 'User Type', 'Date Joined'])
    
    for user in queryset:
        writer.writerow([
            user.school_id,
            user.first_name,
            user.last_name,
            user.user_type,
            user.date_joined.strftime('%Y-%m-%d %H:%M:%S')
        ])
    
    return response


@admin.action(description='Export selected submissions to CSV')
def export_submissions_to_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="submissions.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Student ID', 'Student Name', 'Problem', 'Score', 'Status', 'Submitted At'])
    
    for sub in queryset:
        writer.writerow([
            sub.student_id.school_id,
            sub.student_id.get_full_name(),
            sub.problem_id.problem_title,
            sub.score,
            sub.status,
            sub.submitted_at.strftime('%Y-%m-%d %H:%M:%S')
        ])
    
    return response


@admin.action(description='Mark submissions as graded')
def mark_as_graded(modeladmin, request, queryset):
    updated = queryset.update(status='Graded')
    modeladmin.message_user(request, f"{updated} submissions marked as graded.")


@admin.action(description='Mark submissions as reviewed')
def mark_as_reviewed(modeladmin, request, queryset):
    updated = queryset.update(status='Reviewed')
    modeladmin.message_user(request, f"{updated} submissions marked as reviewed.")


@admin.action(description='Activate selected users')
def activate_users(modeladmin, request, queryset):
    updated = queryset.update(is_active=True)
    modeladmin.message_user(request, f"{updated} users activated.")


@admin.action(description='Deactivate selected users')
def deactivate_users(modeladmin, request, queryset):
    updated = queryset.update(is_active=False)
    modeladmin.message_user(request, f"{updated} users deactivated.")
    
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('school_id', 'full_name', 'user_type_badge', 'is_active_badge', 'image_preview', 'date_joined')
    list_filter = (UserTypeFilter, 'is_active', 'date_joined')
    search_fields = ('school_id', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    readonly_fields = ('date_joined', 'last_login', 'image_preview')
    actions = [export_users_to_csv, activate_users, deactivate_users]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('school_id', 'first_name', 'last_name', 'user_type')
        }),
        ('Authentication', {
            'fields': ('password', 'is_active', 'is_staff', 'is_superuser'),
            'description': 'Password is stored as a hashed value for security.'
        }),
        ('Profile', {
            'fields': ('user_image', 'image_preview')
        }),
        ('Permissions', {
            'fields': ('groups', 'user_permissions'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('date_joined', 'last_login'),
            'classes': ('collapse',)
        }),
    )
    
    def full_name(self, obj):
        return obj.get_full_name()
    full_name.short_description = 'Full Name'
    
    def user_type_badge(self, obj):
        """Display user type as a colored badge"""
        colors = {
            'Teacher': '#667eea',
            'Student': '#11998e'
        }
        color = colors.get(obj.user_type, '#999999')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{}</span>',
            color,
            obj.user_type
        )
    user_type_badge.short_description = 'Type'
    
    def is_active_badge(self, obj):
        """Display active status as a colored badge"""
        color = '#38ef7d' if obj.is_active else '#f5576c'
        status = 'Active' if obj.is_active else 'Inactive'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{}</span>',
            color,
            status
        )
    is_active_badge.short_description = 'Status'
    
    def image_preview(self, obj):
        """Display user profile image in admin"""
        if obj.user_image:
            return format_html(
                '<img src="{}" width="50" height="50" style="border-radius: 50%; object-fit: cover;" />',
                obj.user_image.url
            )
        return "No Image"
    image_preview.short_description = 'Profile Picture'


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    list_display = ('class_code', 'title', 'teacher', 'class_type_badge', 'enrollment_count', 'icon_preview', 'created_at')
    list_filter = (ClassTypeFilter, 'teacher', 'created_at')
    search_fields = ('class_code', 'title', 'teacher__first_name', 'teacher__last_name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'icon_preview', 'enrollment_count')
    
    fieldsets = (
        ('Class Information', {
            'fields': ('class_code', 'title', 'description', 'teacher', 'class_type')
        }),
        ('Visual', {
            'fields': ('upload_icon', 'icon_preview')
        }),
        ('Statistics', {
            'fields': ('enrollment_count',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def class_type_badge(self, obj):
        """Display class type as a colored badge"""
        colors = {
            'programming': '#667eea',
            'cybersecurity': '#f093fb'
        }
        color = colors.get(obj.class_type, '#999999')
        label = 'Programming' if obj.class_type == 'programming' else 'Cybersecurity'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{}</span>',
            color,
            label
        )
    class_type_badge.short_description = 'Type'
    
    def enrollment_count(self, obj):
        """Display total enrolled students"""
        count = obj.enrollments.count()
        return format_html(
            '<span style="background-color: #4facfe; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{} students</span>',
            count
        )
    enrollment_count.short_description = 'Enrollments'
    
    def icon_preview(self, obj):
        """Display class icon in admin"""
        if obj.upload_icon:
            return format_html(
                '<img src="{}" width="50" height="50" style="border-radius: 8px; object-fit: cover;" />',
                obj.upload_icon.url
            )
        return "No Icon"
    icon_preview.short_description = 'Class Icon'


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('enrollment_id', 'student_name', 'class_name', 'enrolled_at')
    list_filter = ('enrolled_at', 'class_id')
    search_fields = ('student_id__first_name', 'student_id__last_name', 'class_id__title')
    ordering = ('-enrolled_at',)
    readonly_fields = ('enrolled_at',)
    
    def student_name(self, obj):
        return obj.student_id.get_full_name()
    student_name.short_description = 'Student'
    
    def class_name(self, obj):
        return obj.class_id.title
    class_name.short_description = 'Class'


@admin.register(Problem)
class ProblemAdmin(admin.ModelAdmin):
    list_display = ('problem_title', 'problem_type', 'class_name', 'teacher_name', 'total_score', 'submission_count', 'due_date', 'created_at')
    list_filter = ('problem_type', 'created_at', 'due_date', 'class_id')
    search_fields = ('problem_title', 'class_id__title', 'teacher_id__first_name', 'teacher_id__last_name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'submission_count')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Problem Details', {
            'fields': ('problem_title', 'problem_description', 'problem_type')
        }),
        ('Assignment', {
            'fields': ('class_id', 'teacher_id')
        }),
        ('Grading & Deadlines', {
            'fields': ('total_score', 'time_limit', 'due_date')
        }),
        ('Challenge Files (Cybersecurity)', {
            'fields': ('challenge_file', 'correct_answer'),
            'classes': ('collapse',)
        }),
        ('Statistics', {
            'fields': ('submission_count',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def class_name(self, obj):
        return obj.class_id.title
    class_name.short_description = 'Class'
    
    def teacher_name(self, obj):
        return obj.teacher_id.get_full_name()
    teacher_name.short_description = 'Teacher'
    
    def submission_count(self, obj):
        """Display submission count"""
        count = obj.submissions.count()
        return format_html(
            '<span style="background-color: #4facfe; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{} submissions</span>',
            count
        )
    submission_count.short_description = 'Submissions'


@admin.register(ProblemTestCase)
class ProblemTestCaseAdmin(admin.ModelAdmin):
    list_display = ('test_case_id', 'problem_title', 'input_preview', 'output_preview', 'created_at')
    list_filter = ('created_at', 'problem_id')
    search_fields = ('problem_id__problem_title',)
    ordering = ('problem_id', 'test_case_id')
    readonly_fields = ('created_at',)
    
    def problem_title(self, obj):
        return obj.problem_id.problem_title
    problem_title.short_description = 'Problem'
    
    def input_preview(self, obj):
        if obj.input_data:
            preview = obj.input_data[:50]
            return preview + '...' if len(obj.input_data) > 50 else preview
        return "No Input"
    input_preview.short_description = 'Input'
    
    def output_preview(self, obj):
        if obj.expected_output:
            preview = obj.expected_output[:50]
            return preview + '...' if len(obj.expected_output) > 50 else preview
        return "No Output"
    output_preview.short_description = 'Expected Output'


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('submission_id', 'student_name', 'problem_title', 'score_display', 'status_badge', 'submitted_at')
    list_filter = (SubmissionStatusFilter, SubmissionScoreFilter, 'submitted_at', 'problem_id__class_id')
    search_fields = ('student_id__first_name', 'student_id__last_name', 'student_id__school_id', 'problem_id__problem_title')
    ordering = ('-submitted_at',)
    readonly_fields = ('submitted_at', 'code_preview', 'submission_id')
    actions = [mark_as_graded, mark_as_reviewed, export_submissions_to_csv]
    date_hierarchy = 'submitted_at'
    
    fieldsets = (
        ('Submission Info', {
            'fields': ('submission_id', 'problem_id', 'student_id', 'submitted_at')
        }),
        ('Submission Content', {
            'fields': ('code_preview', 'answer_text') if True else ('code_preview',)
        }),
        ('Grading', {
            'fields': ('score', 'status', 'feedback')
        }),
    )
    
    def student_name(self, obj):
        return obj.student_id.get_full_name()
    student_name.short_description = 'Student'
    
    def problem_title(self, obj):
        return obj.problem_id.problem_title
    problem_title.short_description = 'Problem'
    
    def score_display(self, obj):
        """Display score with color coding"""
        if obj.score is None:
            return format_html('<span style="color: #999;">Not Graded</span>')
        
        if obj.score >= 90:
            color = '#38ef7d'  # Green - Excellent
        elif obj.score >= 80:
            color = '#11998e'  # Teal - Very Good
        elif obj.score >= 70:
            color = '#4facfe'  # Blue - Good
        elif obj.score >= 50:
            color = '#f093fb'  # Pink - Satisfactory
        else:
            color = '#f5576c'  # Red - Needs Improvement
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{}/100</span>',
            color,
            obj.score
        )
    score_display.short_description = 'Score'
    
    def status_badge(self, obj):
        """Display status as a colored badge"""
        colors = {
            'Pending': '#f093fb',
            'Graded': '#38ef7d',
            'Reviewed': '#4facfe'
        }
        color = colors.get(obj.status, '#999999')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">{}</span>',
            color,
            obj.status
        )
    status_badge.short_description = 'Status'
    
    def code_preview(self, obj):
        """Display code in a readable format"""
        if obj.code:
            return format_html(
                '<pre style="background: #f5f5f5; padding: 10px; border-radius: 5px; max-height: 300px; overflow-y: auto; font-family: monospace;">{}</pre>',
                obj.code[:500] + ('...' if len(obj.code) > 500 else '')
            )
        return "No Code Submitted"
    code_preview.short_description = 'Code Preview'


@admin.register(ChatHistory)
class ChatHistoryAdmin(admin.ModelAdmin):
    list_display = ('chat_id', 'user_name', 'sender', 'message_preview', 'timestamp')
    list_filter = ('sender', 'timestamp')
    search_fields = ('school_id__first_name', 'school_id__last_name', 'message')
    ordering = ('-timestamp',)
    readonly_fields = ('timestamp',)
    
    def user_name(self, obj):
        return obj.school_id.get_full_name()
    user_name.short_description = 'User'
    
    def message_preview(self, obj):
        preview = obj.message[:100]
        return preview + '...' if len(obj.message) > 100 else preview
    message_preview.short_description = 'Message'


@admin.register(ProblemResource)
class ProblemResourceAdmin(admin.ModelAdmin):
    list_display = ('title', 'problem_link', 'file_type_badge', 'file_size', 'uploaded_at')
    list_filter = ('file_extension', 'uploaded_at', 'problem__class_id')
    search_fields = ('title', 'description', 'problem__problem_title')
    ordering = ('-uploaded_at',)
    readonly_fields = ('uploaded_at', 'original_filename', 'file_size', 'file_extension')
    
    fieldsets = (
        ('Resource Information', {
            'fields': ('problem', 'title', 'description')
        }),
        ('File', {
            'fields': ('file',)
        }),
        ('File Details', {
            'fields': ('original_filename', 'file_size', 'file_extension'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('uploaded_at',),
            'classes': ('collapse',)
        }),
    )
    
    def problem_link(self, obj):
        """Display problem link"""
        return format_html(
            '<a href="/admin/User/problem/{}/change/">{}</a>',
            obj.problem.problem_id,
            obj.problem.problem_title[:50]
        )
    problem_link.short_description = 'Problem'
    
    def file_type_badge(self, obj):
        """Display file type as a badge"""
        ext = obj.file_extension.upper()
        color_map = {
            'PDF': '#f5576c',
            'DOCX': '#667eea',
            'PPTX': '#f093fb',
            'PNG': '#4facfe',
            'JPG': '#11998e',
            'ZIP': '#fa709a',
        }
        color = color_map.get(ext, '#999999')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 5px 10px; border-radius: 5px; font-weight: bold;">.{}</span>',
            color,
            ext
        )
    file_type_badge.short_description = 'Type'
