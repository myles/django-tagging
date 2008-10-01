# Admin registrations
from django.contrib import admin
from django.contrib.admin.options import ModelAdmin
from django.utils.translation import ugettext as _

from tagging.models import Tag, TaggedItem

admin.site.register(Tag)
admin.site.register(TaggedItem)
