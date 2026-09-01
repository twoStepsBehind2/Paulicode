from django.contrib.admin import AdminSite
from django.db.models import Count, Sum, Avg
from django.utils.timezone import now
from datetime import timedelta


class PauliCodeAdminSite(AdminSite):
    site_header = "PauliCode Administration"
    site_title = "PauliCode Admin Portal"
    index_title = "Welcome to PauliCode Admin Dashboard"

    # Custom CSS for admin theme
    class Media:
        css = {
            'all': ('admin/css/custom_admin.css',)
        }

    def index(self, request, extra_context=None):
        from .models import User, Class, Enrollment, Problem, Submission

        extra_context = extra_context or {}

        # Time periods
        week_ago = now() - timedelta(days=7)
        month_ago = now() - timedelta(days=30)

        # ============ USER STATISTICS ============
        total_users = User.objects.count()
        total_teachers = User.objects.filter(user_type='Teacher').count()
        total_students = User.objects.filter(user_type='Student').count()
        recent_users = User.objects.filter(date_joined__gte=week_ago).count()
        users_30_days = User.objects.filter(date_joined__gte=month_ago).count()

        # ============ CLASS STATISTICS ============
        total_classes = Class.objects.count()
        programming_classes = Class.objects.filter(class_type='programming').count()
        cybersecurity_classes = Class.objects.filter(class_type='cybersecurity').count()
        active_classes = Class.objects.annotate(
            enrollment_count=Count('enrollments')
        ).filter(enrollment_count__gt=0).count()

        # ============ PROBLEM STATISTICS ============
        total_problems = Problem.objects.count()
        total_assignments = Problem.objects.filter(problem_type='Assignment').count()
        total_quizzes = Problem.objects.filter(problem_type='Quiz').count()
        pending_problems = Problem.objects.filter(due_date__gte=now()).count()
        overdue_problems = Problem.objects.filter(due_date__lt=now()).count()

        # ============ ENROLLMENT STATISTICS ============
        total_enrollments = Enrollment.objects.count()
        recent_enrollments = Enrollment.objects.filter(enrolled_at__gte=week_ago).count()

        # ============ SUBMISSION STATISTICS ============
        total_submissions = Submission.objects.count()
        recent_submissions = Submission.objects.filter(submitted_at__gte=week_ago).count()
        avg_score = Submission.objects.aggregate(avg=Avg('score'))['avg'] or 0
        pending_submissions = Submission.objects.filter(status='Pending').count()
        graded_submissions = Submission.objects.filter(status='Graded').count()

        # Submission pass rate (where score >= 50)
        passed = Submission.objects.filter(score__gte=50).count()
        pass_rate = (passed / total_submissions * 100) if total_submissions > 0 else 0

        # ============ TOP PERFORMERS ============
        top_students = (
            Submission.objects
            .values('student_id__school_id', 'student_id__first_name', 'student_id__last_name')
            .annotate(
                total_score=Sum('score'),
                submission_count=Count('submission_id')
            )
            .order_by('-total_score')[:5]
        )

        # ============ MOST ACTIVE CLASSES ============
        active_classes_list = (
            Submission.objects
            .values('problem_id__class_id__title', 'problem_id__class_id__class_id')
            .annotate(submission_count=Count('submission_id'))
            .order_by('-submission_count')[:5]
        )

        # ============ PROBLEM COMPLETION RATE ============
        problem_completion = (
            Problem.objects
            .annotate(
                total_submissions=Count('submissions'),
                avg_submission_score=Avg('submissions__score')
            )
            .order_by('-total_submissions')[:5]
        )

        # ============ RECENT ACTIVITY ============
        recent_activity = (
            Submission.objects
            .select_related('student_id', 'problem_id')
            .order_by('-submitted_at')[:15]
        )

        # ============ STUDENT PERFORMANCE BREAKDOWN ============
        performance_data = {
            'excellent': Submission.objects.filter(score__gte=90).count(),
            'very_good': Submission.objects.filter(score__gte=80, score__lt=90).count(),
            'good': Submission.objects.filter(score__gte=70, score__lt=80).count(),
            'satisfactory': Submission.objects.filter(score__gte=50, score__lt=70).count(),
            'needs_improvement': Submission.objects.filter(score__lt=50).count(),
        }

        # ============ UPDATE CONTEXT ============
        extra_context.update({
            # User Stats
            'total_users': total_users,
            'total_teachers': total_teachers,
            'total_students': total_students,
            'recent_users': recent_users,
            'users_30_days': users_30_days,

            # Class Stats
            'total_classes': total_classes,
            'programming_classes': programming_classes,
            'cybersecurity_classes': cybersecurity_classes,
            'active_classes': active_classes,

            # Problem Stats
            'total_problems': total_problems,
            'total_assignments': total_assignments,
            'total_quizzes': total_quizzes,
            'pending_problems': pending_problems,
            'overdue_problems': overdue_problems,

            # Enrollment Stats
            'total_enrollments': total_enrollments,
            'recent_enrollments': recent_enrollments,

            # Submission Stats
            'total_submissions': total_submissions,
            'recent_submissions': recent_submissions,
            'avg_score': round(avg_score, 2),
            'pending_submissions': pending_submissions,
            'graded_submissions': graded_submissions,
            'pass_rate': round(pass_rate, 2),

            # Performance Data
            'performance_data': performance_data,

            # Lists
            'top_students': top_students,
            'active_classes_list': active_classes_list,
            'problem_completion': problem_completion,
            'recent_activity': recent_activity,
        })

        return super().index(request, extra_context)
