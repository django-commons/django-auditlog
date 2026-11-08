import json
from collections.abc import Callable
from datetime import timezone
from typing import Any

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.db.models import NOT_PROVIDED, DateTimeField, ForeignKey, JSONField, Model
from django.utils import timezone as django_timezone
from django.utils.encoding import smart_str
from django.utils.module_loading import import_string

from auditlog import get_logentry_model


def track_field(field):
    """
    Returns whether the given field should be tracked by Auditlog.

    Untracked fields are many-to-many relations and relations to the Auditlog LogEntry model.

    :param field: The field to check.
    :type field: Field
    :return: Whether the given field should be tracked.
    :rtype: bool
    """

    # Do not track many to many relations
    if field.many_to_many:
        return False

    # Do not track relations to LogEntry
    if (
        getattr(field, "remote_field", None) is not None
        and field.remote_field.model == get_logentry_model()
    ):
        return False

    return True


def get_fields_in_model(instance):
    """
    Returns the list of fields in the given model instance. Checks whether to use the official
    _meta API or use the raw data. This method excludes many to many fields.

    :param instance: The model instance to get the fields for
    :type instance: Model
    :return: The list of fields for the given model (instance)
    :rtype: list
    """
    assert isinstance(instance, Model)

    return [f for f in instance._meta.get_fields() if track_field(f)]


def _get_field_default(obj: Model | None, field: Any) -> Any:
    """
    Gets the default value for a field from the model's field definition.

    :return: The default value of the field or None.
    """
    try:
        model_field = obj._meta.get_field(field.name)
        default = model_field.default
    except (AttributeError, FieldDoesNotExist):
        default = NOT_PROVIDED

    if default is NOT_PROVIDED:
        return None
    if callable(default):
        return default()
    return default


def get_raw_field_value(obj: Model | None, field: Any) -> Any:
    """
    Gets the value of a given model instance field without serializing it.

    Values are normalized so that the old and the new side of a diff are
    comparable:

    - timezone-aware datetimes are converted to naive UTC
    - JSON values pass through the field's ``to_python``
    - foreign keys resolve to their raw attname value, unless
      AUDITLOG_USE_FK_STRING_REPRESENTATION is enabled
    - no instance returns None; a value inaccessible on an instance
      (deferred field, missing relation) falls back to the field's default,
      or None

    :param obj: The model instance, or None.
    :type obj: Model
    :param field: The field you want to find the value of.
    :type field: Any
    :return: The raw value of the field.
    :rtype: Any
    """
    try:
        if isinstance(field, DateTimeField):
            # DateTimeFields are timezone-aware, so we need to convert the field
            # to its naive form before we can accurately compare them for changes.
            value = getattr(obj, field.name)
            try:
                value = field.to_python(value)
            except TypeError:
                return value
            if (
                value is not None
                and settings.USE_TZ
                and not django_timezone.is_naive(value)
            ):
                value = django_timezone.make_naive(value, timezone=timezone.utc)
        elif isinstance(field, JSONField):
            value = field.to_python(getattr(obj, field.name))
        elif (
            not settings.AUDITLOG_USE_FK_STRING_REPRESENTATION
            and (field.one_to_one or field.many_to_one)
            and hasattr(field, "rel_class")
        ):
            value = getattr(obj, field.get_attname())
        else:
            value = getattr(obj, field.name)
    except (ObjectDoesNotExist, AttributeError):
        return _get_field_default(obj, field)

    return value


def serialize_field_value(
    field: Any, value: Any, use_json_for_changes: bool = False
) -> Any:
    """
    Serializes a raw field value for the changes dict. Stringification of raw
    values must go through this function, so that the old and the new side of
    a diff always serialize the same way.

    :param field: The field the value belongs to.
    :type field: Any
    :param value: The raw value, as returned by ``get_raw_field_value``.
    :type value: Any
    :param use_json_for_changes: whether or not to use JSON for changes
        (see settings.AUDITLOG_STORE_JSON_CHANGES)
    :return: The serialized value: a string, or a JSON-serializable primitive
        when use_json_for_changes is enabled.
    :rtype: Any
    """
    if use_json_for_changes:
        # TODO: should we handle the case where the value is a django Model specifically?
        #       for example, could create a list of ids for ManyToMany fields
        return value if is_primitive(value) else smart_str(value)

    if isinstance(field, JSONField):
        try:
            return json.dumps(value, sort_keys=True, cls=field.encoder)
        except TypeError:
            pass

    # TODO: non-UTF-8 bytes crash smart_str (see #700, #204)
    value = smart_str(value)
    if type(value).__name__ == "__proxy__":
        value = str(value)
    return value


def _serialize_field_value_or_default(
    obj: Model | None, field: Any, value: Any, use_json_for_changes: bool
) -> Any:
    """
    Serializes a raw field value, falling back to the field's default when
    serialization itself fails: serializing can hit the database, e.g. a
    model's ``__str__`` loading a related object that was already deleted.
    """
    try:
        return serialize_field_value(field, value, use_json_for_changes)
    except (ObjectDoesNotExist, AttributeError):
        return serialize_field_value(
            field, _get_field_default(obj, field), use_json_for_changes
        )


def get_field_value(
    obj: Model | None, field: Any, use_json_for_changes: bool = False
) -> Any:
    """
    Gets the value of a given model instance field, serialized for the
    changes dict.

    :param obj: The model instance.
    :type obj: Model
    :param field: The field you want to find the value of.
    :type field: Any
    :return: The serialized value of the field.
    :rtype: Any
    """
    return _serialize_field_value_or_default(
        obj, field, get_raw_field_value(obj, field), use_json_for_changes
    )


def is_primitive(obj) -> bool:
    """
    Checks if the given object is a primitive Python type that can be safely serialized to JSON.

    :param obj: The object to check
    :return: True if the object is a primitive type, False otherwise
    :rtype: bool
    """
    primitive_types = (type(None), bool, int, float, str, list, tuple, dict, set)
    return isinstance(obj, primitive_types)


def get_mask_function(mask_callable: str | None = None) -> Callable[[str], str]:
    """
    Get the masking function to use based on the following priority:
    1. Model-specific mask_callable if provided
    2. mask_callable from settings if configured
    3. Default mask_str function

    :param mask_callable: The dotted path to a callable that will be used for masking.
    :type mask_callable: str
    :return: A callable that takes a string and returns a masked version.
    :rtype: Callable[[str], str]
    """

    if mask_callable:
        return import_string(mask_callable)

    default_mask_callable = settings.AUDITLOG_MASK_CALLABLE
    if default_mask_callable:
        return import_string(default_mask_callable)

    return mask_str


def mask_str(value: str) -> str:
    """
    Masks the first half of the input string to remove sensitive data.

    :param value: The value to mask.
    :type value: str
    :return: The masked version of the string.
    :rtype: str
    """
    mask_limit = int(len(value) / 2)
    return "*" * mask_limit + value[mask_limit:]


def model_instance_diff(
    old: Model | None,
    new: Model | None,
    fields_to_check=None,
    use_json_for_changes=False,
):
    """
    Calculates the differences between two model instances. One of the instances may be ``None``
    (i.e., a newly created model or deleted model). This will cause all fields with a value to have
    changed (from ``None``).

    :param old: The old state of the model instance.
    :type old: Model
    :param new: The new state of the model instance.
    :type new: Model
    :param fields_to_check: An iterable of the field names to restrict the diff to, while ignoring the rest of
        the model's fields. This is used to pass the `update_fields` kwarg from the model's `save` method.
    :param use_json_for_changes: whether or not to use a JSON for changes
        (see settings.AUDITLOG_STORE_JSON_CHANGES)
    :type fields_to_check: Iterable
    :return: A dictionary with the names of the changed fields as keys and a two tuple of the old and new
            field values as value.
    :rtype: dict
    """
    from auditlog.registry import auditlog

    if not (old is None or isinstance(old, Model)):
        raise TypeError("The supplied old instance is not a valid model instance.")
    if not (new is None or isinstance(new, Model)):
        raise TypeError("The supplied new instance is not a valid model instance.")

    diff = {}

    if old is not None and new is not None:
        fields = set(old._meta.fields + new._meta.fields)
        model_fields = auditlog.get_model_fields(new._meta.model)
    elif old is not None:
        fields = set(get_fields_in_model(old))
        model_fields = auditlog.get_model_fields(old._meta.model)
    elif new is not None:
        fields = set(get_fields_in_model(new))
        model_fields = auditlog.get_model_fields(new._meta.model)
    else:
        fields = set()
        model_fields = None

    if fields_to_check:
        fields = {
            field
            for field in fields
            if (
                (isinstance(field, ForeignKey) and field.attname in fields_to_check)
                or (field.name in fields_to_check)
            )
        }

    # Check if fields must be filtered
    if (
        model_fields
        and (model_fields["include_fields"] or model_fields["exclude_fields"])
        and fields
    ):
        filtered_fields = []
        if model_fields["include_fields"]:
            filtered_fields = [
                field
                for field in fields
                if field.name in model_fields["include_fields"]
            ]
        else:
            filtered_fields = fields
        if model_fields["exclude_fields"]:
            filtered_fields = [
                field
                for field in filtered_fields
                if field.name not in model_fields["exclude_fields"]
            ]
        fields = filtered_fields

    for field in fields:
        old_raw = get_raw_field_value(old, field)
        new_raw = get_raw_field_value(new, field)

        # Log only when raw AND serialized both differ:
        # - raw equal -> skip (representation-only change)
        # - serialized equal -> skip (type mismatch)
        # - the type check and the JSONField bypass exist because == coerces
        #   across types and containers ({"a": 1} == {"a": True})
        if not isinstance(field, JSONField):
            try:
                raw_equal = type(old_raw) is type(new_raw) and bool(old_raw == new_raw)
            except Exception:
                # __eq__ on exotic values (e.g. numpy arrays) may not produce
                # a truth value; fall back to the serialized comparison.
                raw_equal = False
            if raw_equal:
                continue

        old_value = _serialize_field_value_or_default(
            old, field, old_raw, use_json_for_changes
        )
        new_value = _serialize_field_value_or_default(
            new, field, new_raw, use_json_for_changes
        )
        if old_value == new_value:
            continue

        if model_fields and field.name in model_fields["mask_fields"]:
            mask_func = get_mask_function(model_fields.get("mask_callable"))

            diff[field.name] = (
                mask_func(smart_str(old_value)),
                mask_func(smart_str(new_value)),
            )
        else:
            diff[field.name] = (old_value, new_value)

    if len(diff) == 0:
        diff = None

    return diff
