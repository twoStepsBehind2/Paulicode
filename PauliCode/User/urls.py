from django.urls import path, include
from . import views

urlpatterns = [


    #--------------Admin Dashboard---------------------------#
    
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    # Other URLs the template links to:
    path('admin-dashboard/exams/',      views.your_exams_view,     name='admin_exams'),
    path('admin-dashboard/risk-flags/', views.your_riskflags_view, name='admin_risk_flags'),
    path('admin-dashboard/activity/',   views.your_activity_view,  name='admin_activity'),
          

    #--------------Portal---------------------------#
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('signup/', views.signup, name='signup'),
    path('send-verification-code/', views.send_verification_code, name='send-verification-code'),
    path('verify-code/', views.verify_code, name='verify-code'),
    path('resend-verification-code/', views.resend_verification_code, name='resend-verification-code'),

    #--------------Teacher Part--------------------#
    path('dashboard/', views.dashboard, name='dashboard'),
    path('create-class/', views.create_class, name='create_class'),
    path('MyClasses/', views.MyClasses, name='MyClasses'),
    path('delete-class/<int:class_id>/', views.delete_class, name='delete_class'),
    path('class/<int:class_id>/', views.classDetails, name='classDetails'),
    path('class/<int:class_id>/add-problem/', views.add_problem, name='add_problem'),
    path("problem/<int:problem_id>/details/", views.get_problem_details, name="get_problem_details"),
    path("problem/<int:problem_id>/edit/", views.edit_problem, name="edit_problem"),
    path("problem/<int:problem_id>/delete/", views.delete_problem, name="delete_problem"),
    path('report/', views.report, name='report'),
    path('delete_student/<str:school_id>/<int:class_id>/', views.delete_student, name='delete_student'),
    path('delete_submission/<int:submission_id>/', views.delete_submission, name='delete_submission'),
    path('api/teacher/progress/', views.teacher_progress_api, name='teacher_progress_api'),
    path('api/teacher/tasks/', views.teacher_tasks_api, name='teacher_tasks_api'),
    

    path('submission/<int:submission_id>/view-code/', views.view_submission_code, name='view_submission_code'),
    path('class/<int:class_id>/add-resources/', views.add_resources, name='add_resources'),
    path('resource/<int:resource_id>/details/', views.get_resource_details, name='get_resource_details'),
    path('resource/<int:resource_id>/delete/', views.delete_resource, name='delete_resource'),

    #--------------Cybersecurity-Specific Routes----#
    path('class/<int:class_id>/add-cybersecurity-challenge/', views.add_cybersecurity_challenge, name='add_cybersecurity_challenge'),
    path('cybersecurity/problem/<int:problem_id>/edit/', views.edit_cybersecurity_challenge, name='edit_cybersecurity_challenge'),
    path('cybersecurity/submit/<int:problem_id>/', views.submit_cybersecurity_answer, name='submit_cybersecurity_answer'),
    path('cybersecurity/submissions/<int:problem_id>/', views.get_cybersecurity_submissions, name='get_cybersecurity_submissions'),
    path('cybersecurity/problem/<int:problem_id>/delete-file/', views.delete_challenge_file, name='delete_challenge_file'),

    #---------------Student Part--------------------#
    path('StudentDashboard/', views.StudentDashboard, name='StudentDashboard'),
    path('classes/', views.StudentClass, name='StudentClass'),
    path('student/join-class/', views.join_class, name='join_class'),
    path('student/class/<int:class_id>/', views.student_class_details, name='student_class_details'),
    path('student/class/<int:class_id>/unenroll/', views.unenroll_class, name='unenroll_class'),
    path('api/student/progress/', views.student_progress_api, name='student_progress_api'),
    path('api/student/pending-tasks/', views.student_pending_tasks_api, name='student_pending_tasks_api'),

    #--------------Student Playground (Programming Only)---------------#
    path('playground/<int:problem_id>/', views.playground, name='playground'),
    path('run_playground_code/', views.run_playground_code, name='run_playground_code'),
    path('submit_problem/<int:problem_id>/', views.submit_problem, name='submit_problem'),
    

    #--------------Universal---------------#
    path('code-playground/', views.code_testing_playground, name='code_testing_playground'),
    path('run-test-code/', views.run_test_code, name='run_test_code'),
    path('api/playground-resources/', views.get_playground_resources, name='get_playground_resources'),
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('api/leaderboard-data/', views.leaderboard_data_api, name='leaderboard_data_api'),
    path('api/student/total-exp/', views.get_student_total_exp, name='get_student_total_exp'),

    #--------------AI----------------------------------#
    path('ai-chat-stream/', views.ai_chat_stream, name='ai_chat_stream'),
    path('load-chat-history/', views.load_chat_history, name='load_chat_history'),
    path('clear-chat-history/', views.clear_chat_history, name='clear_chat_history'),
    path('get-usage-stats/', views.get_usage_stats, name='get_usage_stats'),
]