import contextlib
import time
from contextvars import ContextVar
from functools import partial

from django.contrib.auth import get_user_model
from django.db.models.signals import pre_save

from auditlog import get_logentry_model

auditlog_value = ContextVar("auditlog_value")
auditlog_disabled = ContextVar("auditlog_disabled", default=False)


@contextlib.contextmanager
def set_actor(actor, remote_addr=None, remote_port=None):
    context_data = {
        "actor": actor,
        "remote_addr": remote_addr,
        "remote_port": remote_port,
    }
    return call_context_manager(context_data)


@contextlib.contextmanager
def set_extra_data(context_data):
    return call_context_manager(context_data)


def call_context_manager(context_data):
    """Connect a signal receiver with current user attached."""
    LogEntry = get_logentry_model()
    # Initialize thread local storage
    context_data["signal_duid"] = ("set_actor", time.time())
    token = auditlog_value.set(context_data)

    # Connect signal for automatic logging
    set_extra_data = partial(
        _set_extra_data,
        signal_duid=context_data["signal_duid"],
    )
    pre_save.connect(
        set_extra_data,
        sender=LogEntry,
        dispatch_uid=context_data["signal_duid"],
        weak=False,
    )

    try:
        yield
    finally:
        pre_save.disconnect(sender=LogEntry, dispatch_uid=context_data["signal_duid"])
        try:
            auditlog_value.reset(token)
        except LookupError:
            pass


def _set_actor(auditlog, instance, sender):
    LogEntry = get_logentry_model()
    auth_user_model = get_user_model()
    if "actor" in auditlog:
        actor = auditlog.get("actor")
        if (
            sender == LogEntry
            and isinstance(actor, auth_user_model)
            and instance.actor is None
        ):
            instance.actor = actor
            instance.actor_email = getattr(actor, "email", None)


def _set_extra_data(sender, instance, signal_duid, **kwargs):
    """Signal receiver with extra 'user' and 'signal_duid' kwargs.

    This function becomes a valid signal receiver when it is curried with the actor and a dispatch id.
    """
    LogEntry = get_logentry_model()
    try:
        auditlog = auditlog_value.get()
    except LookupError:
        pass
    else:
        if signal_duid != auditlog["signal_duid"]:
            return

        _set_actor(auditlog, instance, sender)

        for key in auditlog:
            if key != "actor" and hasattr(LogEntry, key):
                if callable(auditlog[key]):
                    setattr(instance, key, auditlog[key]())
                else:
                    setattr(instance, key, auditlog[key])


@contextlib.contextmanager
def disable_auditlog():
    token = auditlog_disabled.set(True)
    try:
        yield
    finally:
        try:
            auditlog_disabled.reset(token)
        except LookupError:
            pass
