from django.contrib import admin
from .models import Forum, ForumPost, LMSModule, LearningResource, LiveClassSession

admin.site.register(LMSModule)
admin.site.register(LearningResource)
admin.site.register(Forum)
admin.site.register(ForumPost)
admin.site.register(LiveClassSession)
