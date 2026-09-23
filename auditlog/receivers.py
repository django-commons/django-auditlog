from functools import wraps

from django.conf import settings

from auditlog import get_logentry_model
from auditlog.context import auditlog_disabled
from auditlog.diff import model_instance_diff
from auditlog.models import _get_manager_from_settings
from auditlog.signals import post_log, post_m2m_log, pre_log, pre_m2m_log


def check_disable(signal_handler):
    """
    Decorator that passes along disabled in kwargs if any of the following is true:
    - 'auditlog_disabled' from threadlocal is true
    - raw = True and AUDITLOG_DISABLE_ON_RAW_SAVE is True
    """

    @wraps(signal_handler)
    def wrapper(*args, **kwargs):
        try:
            auditlog_disabled_value = auditlog_disabled.get()
        except LookupError:
            auditlog_disabled_value = False
        if not auditlog_disabled_value and not (
            kwargs.get("raw") and settings.AUDITLOG_DISABLE_ON_RAW_SAVE
        ):
            signal_handler(*args, **kwargs)

    return wrapper


@check_disable
def log_create(sender, instance, created, **kwargs):
    """
    Signal receiver that creates a log entry when a model instance is first saved to the database.

    Direct use is discouraged, connect your model through :py:func:`auditlog.registry.register` instead.
    """
    if created:
        _create_log_entry(
            action=get_logentry_model().Action.CREATE,
            instance=instance,
            sender=sender,
            diff_old=None,
            diff_new=instance,
            use_json_for_changes=settings.AUDITLOG_STORE_JSON_CHANGES,
        )


@check_disable
def log_update(sender, instance, **kwargs):
    """
    Signal receiver that creates a log entry when a model instance is changed and saved to the database.

    Direct use is discouraged, connect your model through :py:func:`auditlog.registry.register` instead.
    """
    if not instance._state.adding and instance.pk is not None:
        update_fields = kwargs.get("update_fields", None)
        old = _get_manager_from_settings(sender).filter(pk=instance.pk).first()
        _create_log_entry(
            action=get_logentry_model().Action.UPDATE,
            instance=instance,
            sender=sender,
            diff_old=old,
            diff_new=instance,
            fields_to_check=update_fields,
            use_json_for_changes=settings.AUDITLOG_STORE_JSON_CHANGES,
        )


@check_disable
def log_delete(sender, instance, **kwargs):
    """
    Signal receiver that creates a log entry when a model instance is deleted from the database.

    Direct use is discouraged, connect your model through :py:func:`auditlog.registry.register` instead.
    """
    if instance.pk is not None:
        _create_log_entry(
            action=get_logentry_model().Action.DELETE,
            instance=instance,
            sender=sender,
            diff_old=instance,
            diff_new=None,
            use_json_for_changes=settings.AUDITLOG_STORE_JSON_CHANGES,
        )


def log_access(sender, instance, **kwargs):
    """
    Signal receiver that creates a log entry when a model instance is accessed in a AccessLogDetailView.

    Direct use is discouraged, connect your model through :py:func:`auditlog.registry.register` instead.
    """
    if instance.pk is not None:
        _create_log_entry(
            action=get_logentry_model().Action.ACCESS,
            instance=instance,
            sender=sender,
            diff_old=None,
            diff_new=None,
            force_log=True,
            use_json_for_changes=settings.AUDITLOG_STORE_JSON_CHANGES,
        )


def _create_log_entry(
    action,
    instance,
    sender,
    diff_old,
    diff_new,
    fields_to_check=None,
    force_log=False,
    use_json_for_changes=False,
):
    pre_log_results = pre_log.send(
        sender,
        instance=instance,
        action=action,
    )

    if any(item[1] is False for item in pre_log_results):
        return
    LogEntry = get_logentry_model()

    error = None
    log_entry = None
    changes = None
    try:
        changes = model_instance_diff(
            diff_old,
            diff_new,
            fields_to_check=fields_to_check,
            use_json_for_changes=use_json_for_changes,
        )

        if force_log or changes:
            log_entry = LogEntry.objects.log_create(
                instance,
                action=action,
                changes=changes,
                force_log=force_log,
            )
    except BaseException as e:
        error = e
    finally:
        if log_entry or error:
            post_log.send(
                sender,
                instance=instance,
                instance_old=diff_old,
                action=action,
                error=error,
                pre_log_results=pre_log_results,
                changes=changes,
                log_entry=log_entry,
                log_created=log_entry is not None,
                use_json_for_changes=settings.AUDITLOG_STORE_JSON_CHANGES,
            )
        if error:
            raise error


def _create_m2m_log_entry(instance, sender, changed_queryset, operation, field_name):
    action = get_logentry_model().Action.UPDATE

    pre_log_results = pre_m2m_log.send(
        sender,
        instance=instance,
        action=action,
        field_name=field_name,
        operation=operation,
    )
    if any(item[1] is False for item in pre_log_results):
        return

    LogEntry = get_logentry_model()
    error = None
    log_entry = None
    try:
        log_entry = LogEntry.objects.log_m2m_changes(
            changed_queryset, instance, operation, field_name
        )
    except BaseException as e:
        error = e
    finally:
        if log_entry or error:
            post_m2m_log.send(
                sender,
                instance=instance,
                action=action,
                field_name=field_name,
                operation=operation,
                error=error,
                pre_log_results=pre_log_results,
                changes=log_entry.changes if log_entry else None,
                log_entry=log_entry,
                log_created=log_entry is not None,
            )
        if error:
            raise error


def make_log_m2m_changes(field_name):
    """Return a handler for m2m_changed with field_name enclosed."""

    @check_disable
    def log_m2m_changes(signal, action, **kwargs):
        """Handle m2m_changed and call LogEntry.objects.log_m2m_changes as needed."""
        if action not in ["post_add", "post_clear", "post_remove"]:
            return

        instance = kwargs["instance"]
        sender = type(instance)
        model_manager = _get_manager_from_settings(kwargs["model"])

        if action == "post_clear":
            changed_queryset = model_manager.all()
        else:
            changed_queryset = model_manager.filter(pk__in=kwargs["pk_set"])

        if action in ["post_add"]:
            _create_m2m_log_entry(instance, sender, changed_queryset, "add", field_name)
        elif action in ["post_remove", "post_clear"]:
            _create_m2m_log_entry(
                instance, sender, changed_queryset, "delete", field_name
            )

    return log_m2m_changes
