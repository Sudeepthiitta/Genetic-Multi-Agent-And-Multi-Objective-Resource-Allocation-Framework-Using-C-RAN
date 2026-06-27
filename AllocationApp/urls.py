from django.urls import path

from . import views

urlpatterns = [path("index.html", views.index, name="index"),
	             path("UserLogin.html", views.UserLogin, name="UserLogin"),
	             path("UserScreen.html", views.UserScreen, name="UserScreen"),
		     path("UserLoginAction", views.UserLoginAction, name="UserLoginAction"),
		     path("AdminLogin.html", views.AdminLogin, name="AdminLogin"),
		     path("AdminLoginAction", views.AdminLoginAction, name="AdminLoginAction"),
		     path("AdminDashboard.html", views.AdminDashboard, name="AdminDashboard"),
		     path("AdminView.html", views.AdminView, name="AdminView"),
		     path("Register.html", views.Register, name="Register"),
		     path("RegisterAction", views.RegisterAction, name="RegisterAction"),
		     path("RunFixed", views.RunFixed, name="RunFixed"),
		     path("RunHybrid", views.RunHybrid, name="RunHybrid"),
		     path("RunGenetic", views.RunGenetic, name="RunGenetic"),
		     path("Graph", views.Graph, name="Graph"),
		     path('AddDemand', views.AddDemand, name='AddDemand'),
		     path('SendUserChat', views.SendUserChat, name='SendUserChat'),
		     path('UserChatState', views.UserChatState, name='UserChatState'),
		     path('MarkUserChatRead', views.MarkUserChatRead, name='MarkUserChatRead'),
		     path('UpdateProfile', views.UpdateProfile, name='UpdateProfile'),
		     path('SendAdminChat', views.SendAdminChat, name='SendAdminChat'),
		     path('SubmitResourceOffer', views.SubmitResourceOffer, name='SubmitResourceOffer'),
		     path('SubmitAllocationFeedback/<int:request_id>', views.SubmitAllocationFeedback, name='SubmitAllocationFeedback'),
		     path('DeleteUserRequest/<int:request_id>', views.DeleteUserRequest, name='DeleteUserRequest'),
		     path('SendForUserApproval/<int:request_id>', views.SendForUserApproval, name='SendForUserApproval'),
		     path('AllocateApprovedRequest/<int:request_id>', views.AllocateApprovedRequest, name='AllocateApprovedRequest'),
		     path('RejectRequest/<int:request_id>', views.RejectRequest, name='RejectRequest'),
		     path('ResetAllocation', views.ResetAllocation, name='ResetAllocation'),


		      ]
