import ast
import contextlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import timezone
from typing import Any

from dateutil import parser
from dateutil.tz import gettz
from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.exceptions import (
    FieldDoesNotExist,
    ObjectDoesNotExist,
    ValidationError,
)
from django.db import DEFAULT_DB_ALIAS, models
from django.db.models import Q, QuerySet
from django.utils import formats
from django.utils import timezone as django_timezone
from django.utils.encoding import smart_str
from django.utils.translation import gettext_lazy as _

from auditlog import get_logentry_model
from auditlog.diff import get_mask_function

DEFAULT_OBJECT_REPR = "<error forming object repr>"


class LogEntryManager(models.Manager):
    """
    Custom manager for the :py:class:`LogEntry` model.
    """

    def log_create(self, instance, force_log: bool = False, **kwargs):
        """
        Helper method to create a new log entry. This method automatically populates some fields when no
        explicit value is given.

        :param instance: The model instance to log a change for.
        :type instance: Model
        :param force_log: Create a LogEntry even if no changes exist.
        :type force_log: bool
        :param kwargs: Field overrides for the :py:class:`LogEntry` object.
        :return: The new log entry or `None` if there were no changes.
        :rtype: LogEntry
        """
        from auditlog.cid import get_cid

        changes = kwargs.get("changes", None)
        pk = self._get_pk_value(instance)

        if changes is not None or force_log:
            kwargs.setdefault(
                "content_type", ContentType.objects.get_for_model(instance)
            )
            kwargs.setdefault("object_pk", pk)
            try:
                object_repr = smart_str(instance)
            except ObjectDoesNotExist:
                object_repr = DEFAULT_OBJECT_REPR
            kwargs.setdefault("object_repr", object_repr)
            kwargs.setdefault(
                "serialized_data", self._get_serialized_data_or_none(instance)
            )

            if isinstance(pk, int):
                kwargs.setdefault("object_id", pk)

            get_additional_data = getattr(instance, "get_additional_data", None)
            if callable(get_additional_data):
                kwargs.setdefault("additional_data", get_additional_data())

            # set correlation id
            kwargs.setdefault("cid", get_cid())
            return self.create(**kwargs)
        return None

    def log_m2m_changes(
        self, changed_queryset, instance, operation, field_name, **kwargs
    ):
        """Create a new "changed" log entry from m2m record.

        :param changed_queryset: The added or removed related objects.
        :type changed_queryset: QuerySet
        :param instance: The model instance to log a change for.
        :type instance: Model
        :param operation: "add" or "delete".
        :type action: str
        :param field_name: The name of the changed m2m field.
        :type field_name: str
        :param kwargs: Field overrides for the :py:class:`LogEntry` object.
        :return: The new log entry or `None` if there were no changes.
        :rtype: LogEntry
        """
        from auditlog.cid import get_cid

        pk = self._get_pk_value(instance)
        if changed_queryset:
            kwargs.setdefault(
                "content_type", ContentType.objects.get_for_model(instance)
            )
            kwargs.setdefault("object_pk", pk)
            try:
                object_repr = smart_str(instance)
            except ObjectDoesNotExist:
                object_repr = DEFAULT_OBJECT_REPR
            kwargs.setdefault("object_repr", object_repr)
            kwargs.setdefault("action", LogEntry.Action.UPDATE)

            if isinstance(pk, int):
                kwargs.setdefault("object_id", pk)

            get_additional_data = getattr(instance, "get_additional_data", None)
            if callable(get_additional_data):
                kwargs.setdefault("additional_data", get_additional_data())

            objects = [smart_str(instance) for instance in changed_queryset]
            kwargs["changes"] = {
                field_name: {
                    "type": "m2m",
                    "operation": operation,
                    "objects": objects,
                }
            }

            kwargs.setdefault("cid", get_cid())
            return self.create(**kwargs)

        return None

    def get_for_object(self, instance):
        """
        Get log entries for the specified model instance.

        :param instance: The model instance to get log entries for.
        :type instance: Model
        :return: QuerySet of log entries for the given model instance.
        :rtype: QuerySet
        """
        # Return empty queryset if the given model instance is not a model instance.
        if not isinstance(instance, models.Model):
            return self.none()

        content_type = ContentType.objects.get_for_model(instance.__class__)
        pk = self._get_pk_value(instance)

        if isinstance(pk, int):
            return self.filter(content_type=content_type, object_id=pk)
        else:
            return self.filter(content_type=content_type, object_pk=smart_str(pk))

    def get_for_objects(self, queryset):
        """
        Get log entries for the objects in the specified queryset.

        :param queryset: The queryset to get the log entries for.
        :type queryset: QuerySet
        :return: The LogEntry objects for the objects in the given queryset.
        :rtype: QuerySet
        """
        if not isinstance(queryset, QuerySet) or queryset.count() == 0:
            return self.none()

        content_type = ContentType.objects.get_for_model(queryset.model)
        primary_keys = list(
            queryset.values_list(queryset.model._meta.pk.name, flat=True)
        )

        if isinstance(primary_keys[0], int):
            return (
                self.filter(content_type=content_type)
                .filter(Q(object_id__in=primary_keys))
                .distinct()
            )
        elif isinstance(queryset.model._meta.pk, models.UUIDField):
            primary_keys = [smart_str(pk) for pk in primary_keys]
            return (
                self.filter(content_type=content_type)
                .filter(Q(object_pk__in=primary_keys))
                .distinct()
            )
        else:
            return (
                self.filter(content_type=content_type)
                .filter(Q(object_pk__in=primary_keys))
                .distinct()
            )

    def get_for_model(self, model):
        """
        Get log entries for all objects of a specified type.

        :param model: The model to get log entries for.
        :type model: class
        :return: QuerySet of log entries for the given model.
        :rtype: QuerySet
        """
        # Return empty queryset if the given object is not valid.
        if not issubclass(model, models.Model):
            return self.none()

        content_type = ContentType.objects.get_for_model(model)

        return self.filter(content_type=content_type)

    def _get_pk_value(self, instance):
        """
        Get the primary key field value for a model instance.

        :param instance: The model instance to get the primary key for.
        :type instance: Model
        :return: The primary key value of the given model instance.
        """
        # Should be equivalent to `instance.pk`.
        pk_field = instance._meta.pk.attname
        pk = getattr(instance, pk_field, None)

        # Check to make sure that we got a pk not a model object.
        # Should be guaranteed as we used `attname` above, not `name`.
        assert not isinstance(pk, models.Model)
        return pk

    def _get_serialized_data_or_none(self, instance):
        from auditlog.registry import auditlog

        if not auditlog.contains(instance.__class__):
            return None

        opts = auditlog.get_serialize_options(instance.__class__)
        if not opts["serialize_data"]:
            return None

        model_fields = auditlog.get_model_fields(instance.__class__)
        kwargs = opts.get("serialize_kwargs", {})

        if opts["serialize_auditlog_fields_only"]:
            kwargs.setdefault(
                "fields", self._get_applicable_model_fields(instance, model_fields)
            )

        instance_copy = self._get_copy_with_python_typed_fields(instance)
        data = dict(
            json.loads(serializers.serialize("json", (instance_copy,), **kwargs))[0]
        )

        mask_fields = model_fields["mask_fields"]
        if mask_fields:
            data = self._mask_serialized_fields(data, mask_fields, model_fields)

        return data

    def _get_copy_with_python_typed_fields(self, instance):
        """
        Attempt to create copy of instance and coerce types on instance fields

        The Django core serializer assumes that the values on object fields are
        correctly typed to their respective fields. Updates made to an object's
        in-memory state may not meet this assumption. To prevent this violation, values
        are typed by calling `to_python` from the field object, the result is set on a
        copy of the instance and the copy is sent to the serializer.
        """
        try:
            instance_copy = deepcopy(instance)
        except TypeError:
            instance_copy = instance
        for field in instance_copy._meta.fields:
            if not field.is_relation:
                value = getattr(instance_copy, field.name)
                try:
                    setattr(instance_copy, field.name, field.to_python(value))
                except ValidationError:
                    continue
        return instance_copy

    def _get_applicable_model_fields(
        self, instance, model_fields: dict[str, list[str]]
    ) -> list[str]:
        include_fields = model_fields["include_fields"]
        exclude_fields = model_fields["exclude_fields"]
        all_field_names = [field.name for field in instance._meta.fields]

        if not include_fields and not exclude_fields:
            return all_field_names

        return list(set(include_fields or all_field_names).difference(exclude_fields))

    def _mask_serialized_fields(
        self, data: dict[str, Any], mask_fields: list[str], model_fields: dict[str, Any]
    ) -> dict[str, Any]:
        all_field_data = data.pop("fields")
        mask_func = get_mask_function(model_fields.get("mask_callable"))

        masked_field_data = {}
        for key, value in all_field_data.items():
            if isinstance(value, str) and key in mask_fields:
                masked_field_data[key] = mask_func(value)
            else:
                masked_field_data[key] = value

        data["fields"] = masked_field_data
        return data


class AbstractLogEntry(models.Model):
    """
    Represents an entry in the audit log. The content type is saved along with the textual and numeric
    (if available) primary key, as well as the textual representation of the object when it was saved.
    It holds the action performed and the fields that were changed in the transaction.

    If AuditlogMiddleware is used, the actor will be set automatically. Keep in mind that
    editing / re-saving LogEntry instances may set the actor to a wrong value - editing LogEntry
    instances is not recommended (and it should not be necessary).
    """

    class Action:
        """
        The actions that Auditlog distinguishes: creating, updating, deleting and accessing objects.

        The valid actions are :py:attr:`Action.CREATE`, :py:attr:`Action.UPDATE`,
        :py:attr:`Action.DELETE` and :py:attr:`Action.ACCESS`.
        """

        CREATE = 0
        UPDATE = 1
        DELETE = 2
        ACCESS = 3

        choices = (
            (CREATE, _("create")),
            (UPDATE, _("update")),
            (DELETE, _("delete")),
            (ACCESS, _("access")),
        )

    content_type = models.ForeignKey(
        to="contenttypes.ContentType",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("content type"),
    )
    object_pk = models.CharField(
        db_index=True, max_length=255, verbose_name=_("object pk")
    )
    object_id = models.BigIntegerField(
        blank=True, db_index=True, null=True, verbose_name=_("object id")
    )
    object_repr = models.TextField(verbose_name=_("object representation"))
    serialized_data = models.JSONField(null=True)
    action = models.PositiveSmallIntegerField(
        choices=Action.choices, verbose_name=_("action"), db_index=True
    )
    changes_text = models.TextField(blank=True, verbose_name=_("change message"))
    changes = models.JSONField(null=True, verbose_name=_("change message"))
    actor = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
        verbose_name=_("actor"),
    )
    cid = models.CharField(
        max_length=255,
        db_index=True,
        blank=True,
        null=True,
        verbose_name=_("Correlation ID"),
    )
    remote_addr = models.GenericIPAddressField(
        blank=True, null=True, verbose_name=_("remote address")
    )
    remote_port = models.PositiveIntegerField(
        blank=True, null=True, verbose_name=_("remote port")
    )
    timestamp = models.DateTimeField(
        default=django_timezone.now,
        db_index=True,
        verbose_name=_("timestamp"),
    )
    additional_data = models.JSONField(
        blank=True, null=True, verbose_name=_("additional data")
    )
    actor_email = models.CharField(
        blank=True, null=True, max_length=254, verbose_name=_("actor email")
    )

    objects = LogEntryManager()

    class Meta:
        abstract = True
        get_latest_by = "timestamp"
        ordering = ["-timestamp"]
        verbose_name = _("log entry")
        verbose_name_plural = _("log entries")

    def __str__(self):
        if self.action == self.Action.CREATE:
            fstring = _("Created {repr:s}")
        elif self.action == self.Action.UPDATE:
            fstring = _("Updated {repr:s}")
        elif self.action == self.Action.DELETE:
            fstring = _("Deleted {repr:s}")
        else:
            fstring = _("Logged {repr:s}")

        return fstring.format(repr=self.object_repr)

    @property
    def changes_dict(self):
        """
        :return: The changes recorded in this log entry as a dictionary object.
        """
        return changes_func(self)

    @property
    def changes_str(self, colon=": ", arrow=" \u2192 ", separator="; "):
        """
        Return the changes recorded in this log entry as a string. The formatting of the string can be
        customized by setting alternate values for colon, arrow and separator. If the formatting is still
        not satisfying, please use :py:func:`LogEntry.changes_dict` and format the string yourself.

        :param colon: The string to place between the field name and the values.
        :param arrow: The string to place between each old and new value (non-m2m field changes only).
        :param separator: The string to place between each field.
        :return: A readable string of the changes in this log entry.
        """
        substrings = []

        for field, value in sorted(self.changes_dict.items()):
            if isinstance(value, (list, tuple)) and len(value) == 2:
                # handle regular field change
                substring = "{field_name:s}{colon:s}{old:s}{arrow:s}{new:s}".format(
                    field_name=field,
                    colon=colon,
                    old=value[0],
                    arrow=arrow,
                    new=value[1],
                )
                substrings.append(substring)
            elif isinstance(value, dict) and value.get("type") == "m2m":
                # handle m2m change
                substring = (
                    f"{field}{colon}{value['operation']} {sorted(value['objects'])}"
                )
                substrings.append(substring)

        return separator.join(substrings)

    @property
    def changes_display_dict(self):
        """
        :return: The changes recorded in this log entry intended for display to users as a dictionary object.
        """
        from auditlog.registry import auditlog

        # Get the model and model_fields, but gracefully handle the case where the model no longer exists
        model = self.content_type.model_class()
        model_fields = None
        if auditlog.contains(model._meta.model):
            model_fields = auditlog.get_model_fields(model._meta.model)

        if settings.AUDITLOG_STORE_JSON_CHANGES:
            changes_dict = {}
            for field_name, values in self.changes_dict.items():
                values_as_strings = [str(v) for v in values]
                changes_dict[field_name] = values_as_strings
        else:
            changes_dict = self.changes_dict

        changes_display_dict = {}
        # grab the changes_dict and iterate through
        for field_name, values in changes_dict.items():
            # try to get the field attribute on the model
            try:
                field = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                changes_display_dict[field_name] = values
                continue
            values_display = []
            # handle choices fields and Postgres ArrayField to get human-readable version
            choices_dict = None
            if getattr(field, "choices", []):
                choices_dict = dict(field.choices)
            if getattr(getattr(field, "base_field", None), "choices", []):
                choices_dict = dict(field.base_field.choices)

            if choices_dict:
                for value in values:
                    try:
                        value = ast.literal_eval(value)
                        if type(value) is [].__class__:
                            values_display.append(
                                ", ".join(
                                    [choices_dict.get(val, "None") for val in value]
                                )
                            )
                        else:
                            values_display.append(choices_dict.get(value, "None"))
                    except Exception:
                        values_display.append(choices_dict.get(value, "None"))
            else:
                try:
                    field_type = field.get_internal_type()
                except AttributeError:
                    # if the field is a relationship it has no internal type and exclude it
                    continue
                for value in values:
                    # handle case where field is a datetime, date, or time type
                    if field_type in ["DateTimeField", "DateField", "TimeField"]:
                        try:
                            value = parser.parse(value)
                            if field_type == "DateField":
                                value = value.date()
                            elif field_type == "TimeField":
                                value = value.time()
                            elif field_type == "DateTimeField":
                                value = value.replace(tzinfo=timezone.utc)
                                value = value.astimezone(gettz(settings.TIME_ZONE))
                            value = formats.localize(value)
                        except ValueError:
                            pass
                    elif field_type in ["ForeignKey", "OneToOneField"]:
                        value = self._get_changes_display_for_fk_field(field, value)

                    truncate_at = settings.AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH
                    if 0 <= truncate_at < len(value):
                        value = value[:truncate_at] + ("..." if truncate_at > 0 else "")

                    values_display.append(value)

            # Use verbose_name from mapping if available, otherwise determine from field
            if model_fields and field.name in model_fields["mapping_fields"]:
                verbose_name = model_fields["mapping_fields"][field.name]
            else:
                verbose_name = getattr(field, "verbose_name", field.name)

            changes_display_dict[verbose_name] = values_display
        return changes_display_dict

    def _get_changes_display_for_fk_field(
        self, field: models.ForeignKey | models.OneToOneField, value: Any
    ) -> str:
        """
        :return: A string representing a given FK value and the field to which it belongs
        """
        # Return "None" if the FK value is "None".
        if value == "None":
            return value

        # Attempt to convert given value to the PK type for the related model
        try:
            pk_value = field.related_model._meta.pk.to_python(value)
        # ValidationError will handle legacy values where string representations were
        # stored rather than PKs. This will also handle cases where the PK type is
        # changed between the time the LogEntry is created and this method is called.
        except ValidationError:
            return value
        # Attempt to return the string representation of the object
        try:
            related_model_manager = _get_manager_from_settings(field.related_model)

            return smart_str(related_model_manager.get(pk=pk_value))
        # ObjectDoesNotExist will be raised if the object was deleted.
        except ObjectDoesNotExist:
            return f"Deleted '{field.related_model.__name__}' ({value})"


class LogEntry(AbstractLogEntry):
    class Meta(AbstractLogEntry.Meta):
        swappable = "AUDITLOG_LOGENTRY_MODEL"


class AuditlogHistoryField(GenericRelation):
    """
    A subclass of py:class:`django.contrib.contenttypes.fields.GenericRelation` that sets some default
    variables. This makes it easier to access Auditlog's log entries, for example in templates.

    By default, this field will assume that your primary keys are numeric, simply because this is the most
    common case. However, if you have a non-integer primary key, you can simply pass ``pk_indexable=False``
    to the constructor, and Auditlog will fall back to using a non-indexed text based field for this model.

    Using this field will not automatically register the model for automatic logging. This is done so you
    can be more flexible with how you use this field.

    :param pk_indexable: Whether the primary key for this model is not an :py:class:`int` or :py:class:`long`.
    :type pk_indexable: bool
    :param delete_related: Delete referenced auditlog entries together with the tracked object.
        Defaults to False to keep the integrity of the auditlog.
    :type delete_related: bool
    """

    def __init__(self, pk_indexable=True, delete_related=False, **kwargs):
        kwargs["to"] = get_logentry_model()

        if pk_indexable:
            kwargs["object_id_field"] = "object_id"
        else:
            kwargs["object_id_field"] = "object_pk"

        kwargs["content_type_field"] = "content_type"
        self.delete_related = delete_related
        super().__init__(**kwargs)

    def bulk_related_objects(self, objs, using=DEFAULT_DB_ALIAS):
        """
        Return all objects related to ``objs`` via this ``GenericRelation``.
        """
        if self.delete_related:
            return super().bulk_related_objects(objs, using)

        # When deleting, Collector.collect() finds related objects using this
        # method.  However, because we don't want to delete these related
        # objects, we simply return an empty list.
        return []


# should I add a signal receiver for setting_changed?
changes_func = None


def _changes_func() -> Callable[[LogEntry], dict]:
    def json_then_text(instance: LogEntry) -> dict:
        if instance.changes:
            return instance.changes
        elif instance.changes_text:
            with contextlib.suppress(ValueError):
                return json.loads(instance.changes_text)
        return {}

    def default(instance: LogEntry) -> dict:
        return instance.changes or {}

    if settings.AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT:
        return json_then_text
    return default


def _get_manager_from_settings(model: type[models.Model]) -> models.Manager:
    """
    Get model manager as selected by AUDITLOG_USE_BASE_MANAGER.

    - True: return model._meta.base_manager
    - False: return model._meta.default_manager
    """
    if settings.AUDITLOG_USE_BASE_MANAGER:
        return model._meta.base_manager
    else:
        return model._meta.default_manager
