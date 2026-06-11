# User/models.py
# Complete User model with Django auth integration

from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.conf import settings # Import settings
from django.utils import timezone
import os


# ============================================
# Admin Dashboard
# ============================================

class Exam(models.Model):
    title      = models.CharField(max_length=200)
    start_time = models.DateTimeField()
    end_time   = models.DateTimeField()
    instructor = models.ForeignKey(User, on_delete=models.CASCADE)
    instructor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

class ExamSession(models.Model):
    exam        = models.ForeignKey(Exam, on_delete=models.CASCADE)
    student     = models.ForeignKey(User, on_delete=models.CASCADE)
    student     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    submitted   = models.BooleanField(default=False)
    tab_switches = models.IntegerField(default=0)
    risk_level  = models.CharField(
        max_length=10,
        choices=[('low','Low'), ('medium','Medium'), ('high','High')],
        default='low'
    )
    flagged_at  = models.DateTimeField(null=True, blank=True)

class CTFChallenge(models.Model):
    title       = models.CharField(max_length=200)
    is_active   = models.BooleanField(default=True)
    icon        = models.CharField(max_length=50, default='lock')        # Tabler icon name
    color_class = models.CharField(max_length=20, default='blue')        # blue/amber/teal/coral/purple

class CTFSolve(models.Model):
    challenge = models.ForeignKey(CTFChallenge, on_delete=models.CASCADE)
    student   = models.ForeignKey(User, on_delete=models.CASCADE)
    challenge = models.ForeignKey(CTFChallenge, on_delete=models.CASCADE) # This is fine, CTFChallenge is defined above
    student   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    solved_at = models.DateTimeField(auto_now_add=True)

class ActivityLog(models.Model):
    TYPE_CHOICES = [('flag','Flag'), ('solve','Solve'), ('join','Join'), ('exam','Exam')]
    user        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    type        = models.CharField(max_length=20, choices=TYPE_CHOICES)
    description = models.TextField()
    timestamp   = models.DateTimeField(auto_now_add=True)

class XPGrant(models.Model):
    user      = models.ForeignKey(User, on_delete=models.CASCADE)
    points    = models.IntegerField()
    granted_at = models.DateTimeField(auto_now_add=True)
    user      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

# ============================================
# Custom User Manager
# ============================================
class UserManager(BaseUserManager):
    """Manager for User model with custom authentication"""
    
    def create_user(self, school_id, first_name, last_name, password=None, user_type='Student', **extra_fields):
        """Create and save a regular user"""
        if not school_id:
            raise ValueError('Users must have a school ID')
        
        user = self.model(
            school_id=school_id,
            first_name=first_name,
            last_name=last_name,
            user_type=user_type,
            **extra_fields
        )
        user.set_password(password)  # This hashes the password
        user.save(using=self._db)
        return user
    
    def create_superuser(self, school_id, first_name, last_name, password=None, **extra_fields):
        """Create and save a superuser (Teacher with admin access)"""
        extra_fields.setdefault('user_type', 'Teacher')
        
        user = self.create_user(
            school_id=school_id,
            first_name=first_name,
            last_name=last_name,
            password=password,
            **extra_fields
        )
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user


# ============================================
# Custom User Model
# ============================================
class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model for Paulicode platform with Django auth integration"""
    
    USER_TYPE_CHOICES = [
        ('Teacher', 'Teacher'),
        ('Student', 'Student'),
    ]
    
    SCHOOL_CHOICES = [
        ('spus', 'St. Paul University'),
        ('others', 'Others'),
    ]
    
    # Primary Key - school_id
    school_id = models.CharField(max_length=50, unique=True, primary_key=True)
    
    # User Information
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(max_length=191, unique=True, null=True, blank=True) 
    school = models.CharField(max_length=20, choices=SCHOOL_CHOICES, default='others')
    user_type = models.CharField(max_length=10, choices=USER_TYPE_CHOICES)
    user_image = models.ImageField(upload_to='profile_pic/', blank=True, null=True, default='profile_pic/default.png')
    
    # Django Auth Required Fields
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    
    # Specify custom manager
    objects = UserManager()
    
    # Tell Django to use school_id as the username field
    USERNAME_FIELD = 'school_id'
    REQUIRED_FIELDS = ['first_name', 'last_name']  # Required for createsuperuser command
    
    # ============================================
    # Custom Methods
    # ============================================
    
    def get_full_name(self):
        """Return the full name of the user"""
        return f"{self.first_name} {self.last_name}"
    
    def get_short_name(self):
        """Return the short name (first name)"""
        return self.first_name
    
    def is_teacher(self):
        """Check if user is a teacher"""
        return self.user_type == 'Teacher'
    
    def is_student(self):
        """Check if user is a student"""
        return self.user_type == 'Student'
    
    def save(self, *args, **kwargs):
        """Override save to set is_staff based on user_type"""
        if self.user_type == 'Teacher':
            self.is_staff = True
        super().save(*args, **kwargs)
    
    class Meta:
        db_table = 'user'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['school_id']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.school_id})"


class Class(models.Model):
    """Class model for organizing courses"""
    
    CLASS_TYPE_CHOICES = [
        ('programming', 'Programming'),
        ('cybersecurity', 'Cybersecurity'),
    ]
    
    class_id = models.AutoField(primary_key=True)
    class_code = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, null=True)
    upload_icon = models.ImageField(upload_to='class_icons/', blank=True, null=True)
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name='classes_taught')
    created_at = models.DateTimeField(default=timezone.now)
    
    # ✅ NEW: Class type field to distinguish between programming and cybersecurity
    class_type = models.CharField(
        max_length=20, 
        choices=CLASS_TYPE_CHOICES, 
        default='programming',
        help_text="Type of class: Programming or Cybersecurity"
    )
    
    
    class Meta:
        db_table = 'class'
        verbose_name = 'Class'
        verbose_name_plural = 'Classes'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} ({self.class_code})"
    
    def is_cybersecurity(self):
        """Check if this is a cybersecurity class"""
        return self.class_type == 'cybersecurity'
    
    def is_programming(self):
        """Check if this is a programming class"""
        return self.class_type == 'programming'


class Enrollment(models.Model):
    """Enrollment model linking students to classes"""
    
    enrollment_id = models.AutoField(primary_key=True)
    class_id = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='enrollments')
    student_id = models.ForeignKey(User, on_delete=models.CASCADE, related_name='enrollments')
    enrolled_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'enrollment'
        verbose_name = 'Enrollment'
        verbose_name_plural = 'Enrollments'
        unique_together = ('class_id', 'student_id')
        ordering = ['-enrolled_at']
    
    def __str__(self):
        return f"{self.student_id.get_full_name()} enrolled in {self.class_id.title}"


class Problem(models.Model):
    """Problem/Assignment model"""
    
    PROBLEM_TYPE_CHOICES = [
        ('Assignment', 'Assignment'),
        ('Quiz', 'Quiz'),
    ]
    
    problem_id = models.AutoField(primary_key=True)
    class_id = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='problems')
    teacher_id = models.ForeignKey(User, on_delete=models.CASCADE, related_name='problems_created')
    problem_title = models.CharField(max_length=100)
    problem_description = models.TextField()
    problem_type = models.CharField(max_length=20, choices=PROBLEM_TYPE_CHOICES)
    total_score = models.IntegerField(default=100)
    time_limit = models.IntegerField(help_text="Time limit in minutes", null=True, blank=True)
    due_date = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    
    # ✅ File upload for cybersecurity challenges
    challenge_file = models.FileField(
        upload_to='challenge_files/', 
        blank=True, 
        null=True,
        help_text="Upload challenge files (PDF, ZIP, etc.)"
    )
    
    # ✅ NEW: Correct answer for automatic checking
    correct_answer = models.TextField(
        blank=True,
        null=True,
        help_text="Correct answer for cybersecurity challenges (case-insensitive comparison)"
    )
    
    class Meta:
        db_table = 'problem'
        verbose_name = 'Problem'
        verbose_name_plural = 'Problems'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.problem_title} - {self.class_id.title}"
    
    def is_cybersecurity_challenge(self):
        """Check if this problem belongs to a cybersecurity class"""
        return self.class_id.is_cybersecurity()



class ProblemTestCase(models.Model):
    """Test cases for problems"""
    
    test_case_id = models.AutoField(primary_key=True)
    problem_id = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name='test_cases')
    input_data = models.TextField(blank=True, null=True)
    expected_output = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'problem_test_case'
        verbose_name = 'Test Case'
        verbose_name_plural = 'Test Cases'
        ordering = ['test_case_id']
    
    def __str__(self):
        return f"Test Case {self.test_case_id} for {self.problem_id.problem_title}"


class Submission(models.Model):
    """Student submission model"""
    
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Graded', 'Graded'),
        ('Reviewed', 'Reviewed'),
    ]
    
    submission_id = models.AutoField(primary_key=True)
    problem_id = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name='submissions')
    student_id = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submissions')
    code = models.TextField(blank=True, null=True)  # ✅ Made optional for cybersecurity
    
    # ✅ NEW: Text answer for cybersecurity challenges
    answer_text = models.TextField(
        blank=True, 
        null=True,
        help_text="Written answer for cybersecurity challenges"
    )
    
    score = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    feedback = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'submission'
        verbose_name = 'Submission'
        verbose_name_plural = 'Submissions'
        ordering = ['-submitted_at']
    
    def __str__(self):
        return f"Submission by {self.student_id.get_full_name()} for {self.problem_id.problem_title}"
    
    def is_cybersecurity_submission(self):
        """Check if this is a cybersecurity challenge submission"""
        return self.problem_id.is_cybersecurity_challenge()


class ChatHistory(models.Model):
    """AI chat history model"""
    
    SENDER_CHOICES = [
        ('user', 'User'),
        ('ai', 'AI'),
    ]
    
    chat_id = models.AutoField(primary_key=True)
    school_id = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_history')
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES)
    message = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'chat_history'
        verbose_name = 'Chat History'
        verbose_name_plural = 'Chat Histories'
        ordering = ['timestamp']
    
    def __str__(self):
        return f"{self.sender} - {self.school_id.school_id} at {self.timestamp}"

# Add this to your models.py
class ProblemResource(models.Model):
    """File resources attached to problems (PDF, images, documents, etc.)"""
    
    resource_id = models.AutoField(primary_key=True)
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name='resources')
    title = models.CharField(max_length=200, help_text="Title of the resource")
    description = models.TextField(blank=True, null=True, help_text="Description of the resource")
    file = models.FileField(
        upload_to='problem_resources/',
        help_text="Upload problem resources (PDF, DOCX, PPTX, images, etc.)"
    )
    original_filename = models.CharField(max_length=255)
    file_size = models.CharField(max_length=20, blank=True)
    file_extension = models.CharField(max_length=10, blank=True)
    uploaded_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        db_table = 'problem_resource'
        verbose_name = 'Problem Resource'
        verbose_name_plural = 'Problem Resources'
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.title} - {self.problem.problem_title}"
    
    def save(self, *args, **kwargs):
        """Override save to store file metadata"""
        if self.file:
            # Get original filename
            self.original_filename = os.path.basename(self.file.name)
            
            # Get file extension
            self.file_extension = os.path.splitext(self.original_filename)[1][1:].lower()
            
            # Get file size in human-readable format
            file_size_bytes = self.file.size
            if file_size_bytes < 1024:
                self.file_size = f"{file_size_bytes} B"
            elif file_size_bytes < 1024 * 1024:
                self.file_size = f"{file_size_bytes / 1024:.1f} KB"
            else:
                self.file_size = f"{file_size_bytes / (1024 * 1024):.1f} MB"
        
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        """Override delete to remove file from storage"""
        if self.file:
            if os.path.isfile(self.file.path):
                os.remove(self.file.path)
        super().delete(*args, **kwargs)


# ============================================
# Email Verification Model
# ============================================
# Email Verification Model
class EmailVerification(models.Model):
    """Model to store email verification codes"""
    
    email = models.EmailField(max_length=191)  # <= 191 for utf8mb4 safe index
    code = models.CharField(max_length=6)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Verification for {self.email}"
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def is_valid(self):
        return not self.is_expired() and not self.is_used