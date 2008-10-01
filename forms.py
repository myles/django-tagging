"""
Tagging components for Django's ``newforms`` form library.
"""
from django import forms
from django.utils.translation import ugettext as _

from tagging import settings
from tagging.validators import is_tag, is_tag_list
from tagging.utils import parse_tag_input
from tagging.models import Tag

class AdminTagForm(forms.ModelForm):
	class Meta:
		model = Tag
	
	def clean_name(self):
		value = self.cleaned_data["name"]
		return is_tag(value)

class TagField(forms.CharField):
	"""
	A ``CharField`` which validates that its input is a valid list of
	tag names.
	"""
	def clean(self, value):
		value = super(TagField, self).clean(value)
		if value == u'':
			return value
		# for tag_name in parse_tag_input(value):
		# 	if len(tag_name) > settings.MAX_TAG_LENGTH:
		# 		raise forms.ValidationError(
		# 	        _('Each tag may be no more than %s characters long.') % settings.MAX_TAG_LENGTH)
		# return value
		return is_tag_list(value)
