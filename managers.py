"""
Custom managers for generic tagging models.
"""
from django.db import connection
from django.db.models import Manager
from django.db.models.query import QuerySet
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import ugettext as _

from tagging import settings
from tagging.utils import calculate_cloud, get_tag_list, parse_tag_input
from tagging.utils import LOGARITHMIC

# Python 2.3 compatibility
if not hasattr(__builtins__, 'set'):
	from sets import Set as set

qn = connection.ops.quote_name

try:
	from django.db.models.query import parse_lookup
except ImportError:
	parse_lookup = None

class TagManager(Manager):
	def update_tags(self, obj, tag_names):
		"""
		Update tags associated with an object.
		"""
		ctype = ContentType.objects.get_for_model(obj)
		current_tags = list(self.filter(items__content_type__pk=ctype.pk, items__object_id=obj.pk))
		updated_tag_names = parse_tag_input(tag_names)
		if settings.FORCE_LOWERCASE_TAGS:
			updated_tag_names = [t.lower() for t in updated_tag_names]
		
		TaggedItemModel = self._get_related_model_by_accessor('items')
		
		# Remove tags which no longer apply
		tags_for_removal = [tag for tag in current_tags if tag.name not in updated_tag_names]
		if len(tags_for_removal):
			TaggedItemModel._default_manager.filter(content_type__pk=ctype.pk, object_id=obj.pk, tag__in=tags_for_removal).delete()
		
		# Add new tags
		current_tag_names = [tag.name for tag in current_tags]
		for tag_name in updated_tag_names:
			if tag_name not in current_tag_names:
				tag, created = self.get_or_create(name=tag_name)
				TaggedItemModel._default_manager.create(tag=tag, object=obj)
	
	def add_tag(self, obj, tag_name):
		"""
		Associates the given object with a tag.
		"""
		tag_names = parse_tag_input(tag_name)
		if not len(tag_names):
			raise AttributeError(_('No tags were given: "%s".') % tag_name)
		if len(tag_names) > 1:
			raise AttributeError(_('Multiple tags were given: "%s".') % tag_name)
		tag_name = tag_names[0]
		if settings.FORCE_LOWERCASE_TAGS:
			tag_name = tag_name.lower()
		tag, created = self.get_or_create(name=tag_name)
		ctype = ContentType.objects.get_for_model(obj)
		TaggedItemModel = self._get_related_model_by_accessor('items')
		TaggedItemModel._default_manager.get_or_create(tag=tag, content_type=ctype, object_id=obj.pk)
	
	def get_for_object(self, obj):
		"""
		Create a queryset matching all tags associated with the given
		object.
		"""
		ctype = ContentType.objects.get_for_model(obj)
		return self.filter(items__content_type__pk=ctype.pk, items__object_id=obj.pk)
	
	def _get_usage(self, model, counts=False, min_count=None, extra_joins=None, extra_criteria=None, params=None):
		"""
		Perform the custom SQL query for ``usage_for_model`` and
		``usage_for_queryset``.
		"""
		
		if min_count is not None: counts = True
		
		model_table = qn(model._meta.db_table)
		model_pk = '%s.%s' % (model_table, qn(model._meta.pk.column))
		query = """
		SELECT DISTINCT %(tag)s.id, %(tag)s.name%(count_sql)s
		FROM
			%(tag)s
			INNER JOIN %(tagged_item)s
				ON %(tag)s.id = %(tagged_item)s.tag_id
			INNER JOIN %(model)s
				ON %(tagged_item)s.object_id = %(model_pk)s
			%%s
		WHERE %(tagged_item)s.content_type_id = %(content_type_id)s
			%%s
		GROUP BY %(tag)s.id, %(tag)s.name
		%%s
		ORDER BY %(tag)s.name ASC""" % {
			'tag': qn(self.model._meta.db_table),
			'count_sql': counts and (', COUNT(%s)' % model_pk) or '',
			'tagged_item': qn(self._get_related_model_by_accessor('items')._meta.db_table),
			'model': model_table,
			'model_pk': model_pk,
			'content_type_id': ContentType.objects.get_for_model(model).pk,
		}
		
		min_count_sql = ''
		if min_count is not None:
			min_count_sql = 'HAVING COUNT(%s) >= %%s' % model_pk
			params.append(min_count)
		
		cursor = connection.cursor()
		cursor.execute(query % (extra_joins, extra_criteria, min_count_sql), params)
		tags = []
		for row in cursor.fetchall():
			t = self.model(*row[:2])
			if counts:
				t.count = row[2]
			tags.append(t)
		return tags
	
	def usage_for_model(self, model, counts=False, min_count=None, filters=None):
		"""
		Obtain a list of tags associated with instances of the given
		Model class.
		
		If ``counts`` is True, a ``count`` attribute will be added to
		each tag, indicating how many times it has been used against
		the Model class in question.
		
		If ``min_count`` is given, only tags which have a ``count``
		greater than or equal to ``min_count`` will be returned.
		Passing a value for ``min_count`` implies ``counts=True``.
		
		To limit the tags (and counts, if specified) returned to those
		used by a subset of the Model's instances, pass a dictionary
		of field lookups to be applied to the given Model as the
		``filters`` argument.
		"""
		if filters is None: filters = {}
		if not parse_lookup:
			# post-queryset-refactor (hand off to usage_for_queryset)
			queryset = model._default_manager.filter()
			for f in filters.items():
				queryset.query.add_filter(f)
			usage = self.usage_for_queryset(queryset, counts, min_count)
		else:
			# pre-queryset-refactor
			extra_joins = ''
			extra_criteria = ''
			params = []
			if len(filters) > 0:
				joins, where, params = parse_lookup(filters.items(), model._meta)
				extra_joins = ' '.join(['%s %s AS %s ON %s' % (join_type, table, alias, condition) for (alias, (table, join_type, condition)) in joins.items()])
				extra_criteria = 'AND %s' % (' AND '.join(where))
			usage = self._get_usage(model, counts, min_count, extra_joins, extra_criteria, params)
		
		return usage
	
	def usage_for_queryset(self, queryset, counts=False, min_count=None):
		"""
		Obtain a list of tags associated with instances of a model
		contained in the given queryset.
		
		If ``counts`` is True, a ``count`` attribute will be added to
		each tag, indicating how many times it has been used against
		the Model class in question.
		
		If ``min_count`` is given, only tags which have a ``count``
		greater than or equal to ``min_count`` will be returned.
		Passing a value for ``min_count`` implies ``counts=True``.
		"""
		if parse_lookup:
			raise AttributeError("'TagManager.usage_for_queryset' is not compatible with pre-queryset-refactor versions of Django.")
		
		extra_joins = ' '.join(queryset.query.get_from_clause()[0][1:])
		where, params = queryset.query.where.as_sql()
		if where:
			extra_criteria = 'AND %s' % where
		else:
			extra_criteria = ''
		return self._get_usage(queryset.model, counts, min_count, extra_joins, extra_criteria, params)
	
	def related_for_model(self, tags, model, counts=False, min_count=None):
		"""
		Obtain a list of tags related to a given list of tags - that
		is, other tags used by items which have all the given tags.
		
		If ``counts`` is True, a ``count`` attribute will be added to
		each tag, indicating the number of items which have it in
		addition to the given list of tags.
		
		If ``min_count`` is given, only tags which have a ``count``
		greater than or equal to ``min_count`` will be returned.
		Passing a value for ``min_count`` implies ``counts=True``.
		"""
		if min_count is not None: counts = True
		tags = get_tag_list(tags)
		tag_count = len(tags)
		tagged_item_table = qn(self._get_related_model_by_accessor('items')._meta.db_table)
		query = """
		SELECT %(tag)s.id, %(tag)s.name%(count_sql)s
		FROM %(tagged_item)s INNER JOIN %(tag)s ON %(tagged_item)s.tag_id = %(tag)s.id
		WHERE %(tagged_item)s.content_type_id = %(content_type_id)s
			AND %(tagged_item)s.object_id IN
			(
			    SELECT %(tagged_item)s.object_id
			    FROM %(tagged_item)s, %(tag)s
			    WHERE %(tagged_item)s.content_type_id = %(content_type_id)s
			      AND %(tag)s.id = %(tagged_item)s.tag_id
			      AND %(tag)s.id IN (%(tag_id_placeholders)s)
			    GROUP BY %(tagged_item)s.object_id
			    HAVING COUNT(%(tagged_item)s.object_id) = %(tag_count)s
			)
			AND %(tag)s.id NOT IN (%(tag_id_placeholders)s)
		GROUP BY %(tag)s.id, %(tag)s.name
		%(min_count_sql)s
		ORDER BY %(tag)s.name ASC""" % {
			'tag': qn(self.model._meta.db_table),
			'count_sql': counts and ', COUNT(%s.object_id)' % tagged_item_table or '',
			'tagged_item': tagged_item_table,
			'content_type_id': ContentType.objects.get_for_model(model).pk,
			'tag_id_placeholders': ','.join(['%s'] * tag_count),
			'tag_count': tag_count,
			'min_count_sql': min_count is not None and ('HAVING COUNT(%s.object_id) >= %%s' % tagged_item_table) or '',
		}
		
		params = [tag.pk for tag in tags] * 2
		if min_count is not None:
			params.append(min_count)
		
		cursor = connection.cursor()
		cursor.execute(query, params)
		related = []
		for row in cursor.fetchall():
			tag = self.model(*row[:2])
			if counts is True:
				tag.count = row[2]
			related.append(tag)
		return related
	
	def cloud_for_model(self, model, steps=4, distribution=LOGARITHMIC, filters=None, min_count=None):
		"""
		Obtain a list of tags associated with instances of the given
		Model, giving each tag a ``count`` attribute indicating how
		many times it has been used and a ``font_size`` attribute for
		use in displaying a tag cloud.
		
		``steps`` defines the range of font sizes - ``font_size`` will
		be an integer between 1 and ``steps`` (inclusive).
		
		``distribution`` defines the type of font size distribution
		algorithm which will be used - logarithmic or linear. It must
		be either ``tagging.utils.LOGARITHMIC`` or
		``tagging.utils.LINEAR``.
		
		To limit the tags displayed in the cloud to those associated
		with a subset of the Model's instances, pass a dictionary of
		field lookups to be applied to the given Model as the
		``filters`` argument.
		
		To limit the tags displayed in the cloud to those with a
		``count`` greater than or equal to ``min_count``, pass a value
		for the ``min_count`` argument.
		"""
		tags = list(self.usage_for_model(model, counts=True, filters=filters, min_count=min_count))
		return calculate_cloud(tags, steps, distribution)
	
	def _get_related_model_by_accessor(self, accessor):
		"""
		Returns the model for the related object accessed by the
		given attribute name.
		
		Since we sometimes need to access the ``TaggedItem`` model
		when managing tagging and the``Tag`` model does not have a
		field representing this relationship, this method is used to
		retrieve the ``TaggedItem`` model, avoiding circular imports
		betweeen the ``models`` and ``managers`` modules.
		"""
		for rel_obj in self.model._meta.get_all_related_objects():
			if rel_obj.get_accessor_name() == accessor:
				return rel_obj.model
		raise ValueError(_('Could not find a related object with the accessor "%s".') % accessor)

class TaggedItemManager(Manager):
	def get_by_model(self, model, tags):
		"""
		Create a queryset matching instances of the given Model
		associated with a given Tag or list of Tags.
		"""
		tags = get_tag_list(tags)
		tag_count = len(tags)
		if tag_count == 0:
			# No existing tags were given
			return model._default_manager.none()
		elif tag_count == 1:
			# Optimisation for single tag - fall through to the simpler
			# query below.
			tag = tags[0]
		else:
			return self.get_intersection_by_model(model, tags)
		
		ctype = ContentType.objects.get_for_model(model)
		opts = self.model._meta
		tagged_item_table = qn(opts.db_table)
		return model._default_manager.extra(
			tables=[opts.db_table],
			where=[
				'%s.content_type_id = %%s' % tagged_item_table,
				'%s.tag_id = %%s' % tagged_item_table,
				'%s.%s = %s.object_id' % (qn(model._meta.db_table), qn(model._meta.pk.column), tagged_item_table)
			],
			params=[ctype.pk, tag.pk],
		)
	
	def get_intersection_by_model(self, model, tags):
		"""
		Create a queryset matching instances of the given Model
		associated with all the given list of Tags.
		
		FIXME The query currently used to grab the ids of objects
		      which have all the tags should be all that we need run,
		      using a non-explicit join for the QuerySet returned, as
		      in get_by_model, but there's currently no way to get the
		      required GROUP BY and HAVING clauses into Django's ORM.
		
		      Once the ORM is capable of this, we should have a
		      solution which requires only a single query and won't
		      have the problem where the number of ids in the IN
		      clause in the QuerySet could exceed the length allowed,
		      as could currently happen.
		"""
		tags = get_tag_list(tags)
		tag_count = len(tags)
		model_table = qn(model._meta.db_table)
		# This query selects the ids of all objects which have all the
		# given tags.
		query = """
		SELECT %(model_pk)s
		FROM %(model)s, %(tagged_item)s
		WHERE %(tagged_item)s.content_type_id = %(content_type_id)s
			AND %(tagged_item)s.tag_id IN (%(tag_id_placeholders)s)
			AND %(model_pk)s = %(tagged_item)s.object_id
		GROUP BY %(model_pk)s
		HAVING COUNT(%(model_pk)s) = %(tag_count)s""" % {
			'model_pk': '%s.%s' % (model_table, qn(model._meta.pk.column)),
			'model': model_table,
			'tagged_item': qn(self.model._meta.db_table),
			'content_type_id': ContentType.objects.get_for_model(model).pk,
			'tag_id_placeholders': ','.join(['%s'] * tag_count),
			'tag_count': tag_count,
		}
		
		cursor = connection.cursor()
		cursor.execute(query, [tag.pk for tag in tags])
		object_ids = [row[0] for row in cursor.fetchall()]
		if len(object_ids) > 0:
			return model._default_manager.filter(pk__in=object_ids)
		else:
			return model._default_manager.none()
	
	def get_union_by_model(self, model, tags):
		"""
		Create a queryset matching instances of the given Model
		associated with any of the given list of Tags.
		"""
		tags = get_tag_list(tags)
		tag_count = len(tags)
		model_table = qn(model._meta.db_table)
		# This query selects the ids of all objects which have any of
		# the given tags.
		query = """
		SELECT %(model_pk)s
		FROM %(model)s, %(tagged_item)s
		WHERE %(tagged_item)s.content_type_id = %(content_type_id)s
		  AND %(tagged_item)s.tag_id IN (%(tag_id_placeholders)s)
		  AND %(model_pk)s = %(tagged_item)s.object_id
		GROUP BY %(model_pk)s""" % {
			'model_pk': '%s.%s' % (model_table, qn(model._meta.pk.column)),
			'model': model_table,
			'tagged_item': qn(self.model._meta.db_table),
			'content_type_id': ContentType.objects.get_for_model(model).pk,
			'tag_id_placeholders': ','.join(['%s'] * tag_count),
		}
		
		cursor = connection.cursor()
		cursor.execute(query, [tag.pk for tag in tags])
		object_ids = [row[0] for row in cursor.fetchall()]
		if len(object_ids) > 0:
			return model._default_manager.filter(pk__in=object_ids)
		else:
			return model._default_manager.none()
	
	def get_related(self, obj, model, num=None):
		"""
		Retrieve instances of ``model`` which share tags with the
		model instance ``obj``, ordered by the number of shared tags
		in descending order.
		
		If ``num`` is given, a maximum of ``num`` instances will be
		returned.
		"""
		model_table = qn(model._meta.db_table)
		content_type = ContentType.objects.get_for_model(obj)
		related_content_type = ContentType.objects.get_for_model(model)
		query = """
		SELECT %(model_pk)s, COUNT(related_tagged_item.object_id) AS %(count)s
		FROM %(model)s, %(tagged_item)s, %(tag)s, %(tagged_item)s related_tagged_item
		WHERE %(tagged_item)s.object_id = %%s
			AND %(tagged_item)s.content_type_id = %(content_type_id)s
			AND %(tag)s.id = %(tagged_item)s.tag_id
			AND related_tagged_item.content_type_id = %(related_content_type_id)s
			AND related_tagged_item.tag_id = %(tagged_item)s.tag_id
			AND %(model_pk)s = related_tagged_item.object_id"""
		if content_type.pk == related_content_type.pk:
			# Exclude the given instance itself if determining related
			# instances for the same model.
			query += """
				AND related_tagged_item.object_id != %(tagged_item)s.object_id"""
		query += """
		GROUP BY %(model_pk)s
		ORDER BY %(count)s DESC
		%(limit_offset)s"""
		query = query % {
			'model_pk': '%s.%s' % (model_table, qn(model._meta.pk.column)),
			'count': qn('count'),
			'model': model_table,
			'tagged_item': qn(self.model._meta.db_table),
			'tag': qn(self.model._meta.get_field('tag').rel.to._meta.db_table),
			'content_type_id': content_type.pk,
			'related_content_type_id': related_content_type.pk,
			'limit_offset': num is not None and connection.ops.limit_offset_sql(num) or '',
		}
		
		cursor = connection.cursor()
		cursor.execute(query, [obj.pk])
		object_ids = [row[0] for row in cursor.fetchall()]
		if len(object_ids) > 0:
			# Use in_bulk here instead of an id__in lookup, because id__in would
			# clobber the ordering.
			object_dict = model._default_manager.in_bulk(object_ids)
			return [object_dict[object_id] for object_id in object_ids]
		else:
			return model._default_manager.none()
