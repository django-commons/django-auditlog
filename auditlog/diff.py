import json
from collections.abc import Callable
from datetime import timezone

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


def get_field_value(obj, field, use_json_for_changes=False):
    """
    Gets the value of a given model instance field.

    :param obj: The model instance.
    :type obj: Model
    :param field: The field you want to find the value of.
    :type field: Any
    :return: The value of the field as a string.
    :rtype: str
    """

    def get_default_value():
        """
        Attempts to get the default value for a field from the model's field definition.

        :return: The default value of the field or None
        """
        try:
            model_field = obj._meta.get_field(field.name)
            default = model_field.default
        except (AttributeError, FieldDoesNotExist):
            default = NOT_PROVIDED

        if default is NOT_PROVIDED:
            default = None
        elif callable(default):
            default = default()

        return smart_str(default) if not use_json_for_changes else default

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
            if not use_json_for_changes:
                try:
                    value = json.dumps(value, sort_keys=True, cls=field.encoder)
                except TypeError:
                    pass
        elif (
            not settings.AUDITLOG_USE_FK_STRING_REPRESENTATION
            and (field.one_to_one or field.many_to_one)
            and hasattr(field, "rel_class")
        ):
            value = smart_str(getattr(obj, field.get_attname()), strings_only=True)
        else:
            value = getattr(obj, field.name)
            if not use_json_for_changes:
                value = smart_str(value)
                if type(value).__name__ == "__proxy__":
                    value = str(value)
    except (ObjectDoesNotExist, AttributeError):
        return get_default_value()

    return value


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

    # Optionally exclude reverse relations (auto-created fields).
    # Reverse relations (auto_created & not concrete) can cause unwanted DB hits
    # and mutate instance._state.fields_cache as a side-effect.
    # Make this behavior opt-in via AUDITLOG_EXCLUDE_REVERSE_RELATIONS.
    if settings.AUDITLOG_EXCLUDE_REVERSE_RELATIONS:

        def is_reverse_field(f):
            return getattr(f, "auto_created", False) and not getattr(
                f, "concrete", False
            )

        fields = {f for f in fields if not is_reverse_field(f)}

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
        old_value = get_field_value(old, field, use_json_for_changes)
        new_value = get_field_value(new, field, use_json_for_changes)

        if old_value != new_value:
            if model_fields and field.name in model_fields["mask_fields"]:
                mask_func = get_mask_function(model_fields.get("mask_callable"))

                diff[field.name] = (
                    mask_func(smart_str(old_value)),
                    mask_func(smart_str(new_value)),
                )
            else:
                if not use_json_for_changes:
                    diff[field.name] = (smart_str(old_value), smart_str(new_value))
                else:
                    # TODO: should we handle the case where the value is a django Model specifically?
                    #       for example, could create a list of ids for ManyToMany fields

                    # this maintains the behavior of the original code
                    if not is_primitive(old_value):
                        old_value = smart_str(old_value)
                    if not is_primitive(new_value):
                        new_value = smart_str(new_value)
                    diff[field.name] = (old_value, new_value)

    if len(diff) == 0:
        diff = None

    return diff
