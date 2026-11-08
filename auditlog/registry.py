import copy
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable
from typing import Any

from django.apps import apps
from django.db.models import ManyToManyField, Model
from django.db.models.base import ModelBase
from django.db.models.signals import (
    ModelSignal,
    m2m_changed,
    post_delete,
    post_save,
    pre_save,
)

from auditlog.conf import settings
from auditlog.signals import accessed

DispatchUID = tuple[int, int, int]


class AuditLogRegistrationError(Exception):
    pass


class AuditlogModelRegistry:
    """
    A registry that keeps track of the models that use Auditlog to track changes.
    """

    DEFAULT_EXCLUDE_MODELS = (settings.AUDITLOG_LOGENTRY_MODEL, "admin.LogEntry")

    def __init__(
        self,
        create: bool = True,
        update: bool = True,
        delete: bool = True,
        access: bool = True,
        m2m: bool = True,
        custom: dict[ModelSignal, Callable] | None = None,
    ):
        from auditlog.receivers import log_access, log_create, log_delete, log_update

        self._registry = {}
        self._signals = {}
        self._m2m_signals = defaultdict(dict)

        if create:
            self._signals[post_save] = log_create
        if update:
            self._signals[pre_save] = log_update
        if delete:
            self._signals[post_delete] = log_delete
        if access:
            self._signals[accessed] = log_access
        self._m2m = m2m

        if custom is not None:
            self._signals.update(custom)

    def register(
        self,
        model: ModelBase = None,
        include_fields: list[str] | None = None,
        exclude_fields: list[str] | None = None,
        mapping_fields: dict[str, str] | None = None,
        mask_fields: list[str] | None = None,
        mask_callable: str | None = None,
        m2m_fields: Collection[str] | None = None,
        object_repr_field: str | None = None,
        serialize_data: bool = False,
        serialize_kwargs: dict[str, Any] | None = None,
        serialize_auditlog_fields_only: bool = False,
    ):
        """
        Register a model with auditlog. Auditlog will then track mutations on this model's instances.

        :param model: The model to register.
        :param include_fields: The fields to include. Implicitly excludes all other fields.
        :param exclude_fields: The fields to exclude. Overrides the fields to include.
        :param mapping_fields: Mapping from field names to strings in diff.
        :param mask_fields: The fields to mask for sensitive info.
        :param mask_callable: The dotted path to a callable that will be used for masking. If not provided,
                              the default mask_callable will be used.
        :param object_repr_field: The field or method to represent the instance in the log entry's object_repr
                              field, when absent the instance is stringified
        :param m2m_fields: The fields to handle as many to many.
        :param serialize_data: Option to include a dictionary of the objects state in the auditlog.
        :param serialize_kwargs: Optional kwargs to pass to Django serializer
        :param serialize_auditlog_fields_only: Only fields being considered in changes will be serialized.
        """

        if include_fields is None:
            include_fields = []
        if exclude_fields is None:
            exclude_fields = []
        if mapping_fields is None:
            mapping_fields = {}
        if mask_fields is None:
            mask_fields = []
        if m2m_fields is None:
            m2m_fields = set()
        if serialize_kwargs is None:
            serialize_kwargs = {}

        if (serialize_kwargs or serialize_auditlog_fields_only) and not serialize_data:
            raise AuditLogRegistrationError(
                "Serializer options were given but the 'serialize_data' option is not "
                "set. Did you forget to set serialized_data to True?"
            )

        for fld in settings.AUDITLOG_EXCLUDE_TRACKING_FIELDS:
            exclude_fields.append(fld)

        for fld in settings.AUDITLOG_MASK_TRACKING_FIELDS:
            mask_fields.append(fld)

        def registrar(cls):
            """Register models for a given class."""
            if not issubclass(cls, Model):
                raise TypeError("Supplied model is not a valid model.")

            self._registry[cls] = {
                "include_fields": include_fields,
                "exclude_fields": exclude_fields,
                "mapping_fields": mapping_fields,
                "mask_fields": mask_fields,
                "mask_callable": mask_callable,
                "m2m_fields": m2m_fields,
                "object_repr_field": object_repr_field,
                "serialize_data": serialize_data,
                "serialize_kwargs": serialize_kwargs,
                "serialize_auditlog_fields_only": serialize_auditlog_fields_only,
            }
            self._connect_signals(cls)

            # We need to return the class, as the decorator is basically
            # syntactic sugar for:
            # MyClass = auditlog.register(MyClass)
            return cls

        if model is None:
            # If we're being used as a decorator, return a callable with the
            # wrapper.
            return lambda cls: registrar(cls)
        else:
            # Otherwise, just register the model.
            registrar(model)

    def contains(self, model: ModelBase) -> bool:
        """
        Check if a model is registered with auditlog.

        :param model: The model to check.
        :return: Whether the model has been registered.
        :rtype: bool
        """
        return model in self._registry

    def unregister(self, model: ModelBase) -> None:
        """
        Unregister a model with auditlog. This will not affect the database.

        :param model: The model to unregister.
        """
        try:
            del self._registry[model]
        except KeyError:
            pass
        else:
            self._disconnect_signals(model)

    def get_models(self) -> list[ModelBase]:
        return list(self._registry.keys())

    def get_model_fields(self, model: ModelBase):
        return {
            "include_fields": list(self._registry[model]["include_fields"]),
            "exclude_fields": list(self._registry[model]["exclude_fields"]),
            "mapping_fields": dict(self._registry[model]["mapping_fields"]),
            "mask_fields": list(self._registry[model]["mask_fields"]),
            "mask_callable": self._registry[model]["mask_callable"],
            "object_repr_field": self._registry[model]["object_repr_field"],
        }

    def get_serialize_options(self, model: ModelBase):
        return {
            "serialize_data": bool(self._registry[model]["serialize_data"]),
            "serialize_kwargs": dict(self._registry[model]["serialize_kwargs"]),
            "serialize_auditlog_fields_only": bool(
                self._registry[model]["serialize_auditlog_fields_only"]
            ),
        }

    def _connect_signals(self, model):
        """
        Connect signals for the model.
        """
        from auditlog.receivers import make_log_m2m_changes

        for signal, receiver in self._signals.items():
            signal.connect(
                receiver,
                sender=model,
                dispatch_uid=self._dispatch_uid(signal, receiver),
            )
        if self._m2m:
            for field_name in self._registry[model]["m2m_fields"]:
                receiver = make_log_m2m_changes(field_name)
                self._m2m_signals[model][field_name] = receiver
                field = getattr(model, field_name)
                m2m_model = getattr(field, "through")

                m2m_changed.connect(
                    receiver,
                    sender=m2m_model,
                    dispatch_uid=self._m2m_dispatch_uid(m2m_changed, m2m_model),
                )

    def _disconnect_signals(self, model):
        """
        Disconnect signals for the model.
        """
        for signal, receiver in self._signals.items():
            signal.disconnect(
                sender=model, dispatch_uid=self._dispatch_uid(signal, receiver)
            )
        for field_name, receiver in self._m2m_signals[model].items():
            field = getattr(model, field_name)
            m2m_model = getattr(field, "through")
            m2m_changed.disconnect(
                sender=m2m_model,
                dispatch_uid=self._m2m_dispatch_uid(m2m_changed, m2m_model),
            )
        del self._m2m_signals[model]

    def _dispatch_uid(self, signal, receiver) -> DispatchUID:
        """Generate a dispatch_uid which is unique for a combination of self, signal, and receiver."""
        return id(self), id(signal), id(receiver)

    def _m2m_dispatch_uid(self, signal, sender) -> DispatchUID:
        """Generate a dispatch_uid which is unique for a combination of self, signal, and sender."""
        return id(self), id(signal), id(sender)

    def _get_model_classes(self, app_model: str) -> list[ModelBase]:
        try:
            try:
                app_label, model_name = app_model.split(".")
                return [apps.get_model(app_label, model_name)]
            except ValueError:
                return apps.get_app_config(app_model).get_models()
        except LookupError:
            return []

    def _get_exclude_models(
        self, exclude_tracking_models: Iterable[str]
    ) -> list[ModelBase]:
        exclude_models = [
            model
            for app_model in tuple(exclude_tracking_models)
            + self.DEFAULT_EXCLUDE_MODELS
            for model in self._get_model_classes(app_model)
        ]
        return exclude_models

    def _register_models(self, models: Iterable[str | dict[str, Any]]) -> None:
        models = copy.deepcopy(models)
        for model in models:
            if isinstance(model, str):
                for model_class in self._get_model_classes(model):
                    self.unregister(model_class)
                    self.register(model_class)
            elif isinstance(model, dict):
                appmodel = self._get_model_classes(model["model"])
                if not appmodel:
                    raise AuditLogRegistrationError(
                        f"An error was encountered while registering model '{model['model']}' - "
                        "make sure the app is registered correctly."
                    )
                model["model"] = appmodel[0]
                self.unregister(model["model"])
                self.register(**model)

    def register_from_settings(self):
        """
        Register models from settings variables
        """
        if not isinstance(settings.AUDITLOG_INCLUDE_ALL_MODELS, bool):
            raise TypeError("Setting 'AUDITLOG_INCLUDE_ALL_MODELS' must be a boolean")
        if not isinstance(settings.AUDITLOG_DISABLE_ON_RAW_SAVE, bool):
            raise TypeError("Setting 'AUDITLOG_DISABLE_ON_RAW_SAVE' must be a boolean")
        if not isinstance(settings.AUDITLOG_EXCLUDE_TRACKING_MODELS, (list, tuple)):
            raise TypeError(
                "Setting 'AUDITLOG_EXCLUDE_TRACKING_MODELS' must be a list or tuple"
            )

        if (
            not settings.AUDITLOG_INCLUDE_ALL_MODELS
            and settings.AUDITLOG_EXCLUDE_TRACKING_MODELS
        ):
            raise ValueError(
                "In order to use setting 'AUDITLOG_EXCLUDE_TRACKING_MODELS', "
                "setting 'AUDITLOG_INCLUDE_ALL_MODELS' must set to 'True'"
            )

        if (
            settings.AUDITLOG_EXCLUDE_TRACKING_FIELDS
            and not settings.AUDITLOG_INCLUDE_ALL_MODELS
        ):
            raise ValueError(
                "In order to use 'AUDITLOG_EXCLUDE_TRACKING_FIELDS', "
                "setting 'AUDITLOG_INCLUDE_ALL_MODELS' must be set to 'True'"
            )

        if (
            settings.AUDITLOG_MASK_TRACKING_FIELDS
            and not settings.AUDITLOG_INCLUDE_ALL_MODELS
        ):
            raise ValueError(
                "In order to use 'AUDITLOG_MASK_TRACKING_FIELDS', "
                "setting 'AUDITLOG_INCLUDE_ALL_MODELS' must be set to 'True'"
            )

        if not isinstance(settings.AUDITLOG_INCLUDE_TRACKING_MODELS, (list, tuple)):
            raise TypeError(
                "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' must be a list or tuple"
            )

        if not isinstance(settings.AUDITLOG_EXCLUDE_TRACKING_FIELDS, (list, tuple)):
            raise TypeError(
                "Setting 'AUDITLOG_EXCLUDE_TRACKING_FIELDS' must be a list or tuple"
            )

        if not isinstance(settings.AUDITLOG_MASK_TRACKING_FIELDS, (list, tuple)):
            raise TypeError(
                "Setting 'AUDITLOG_MASK_TRACKING_FIELDS' must be a list or tuple"
            )

        for item in settings.AUDITLOG_INCLUDE_TRACKING_MODELS:
            if not isinstance(item, (str, dict)):
                raise TypeError(
                    "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' items must be str or dict"
                )

            if isinstance(item, dict):
                if "model" not in item:
                    raise ValueError(
                        "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' dict items must contain 'model' key"
                    )
                if "." not in item["model"]:
                    raise ValueError(
                        "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' model must be in the "
                        "format <app_name>.<model_name>"
                    )

        if settings.AUDITLOG_INCLUDE_ALL_MODELS:
            exclude_models = self._get_exclude_models(
                settings.AUDITLOG_EXCLUDE_TRACKING_MODELS
            )

            for model in apps.get_models(include_auto_created=True):
                if model in exclude_models:
                    continue

                meta = model._meta
                if not meta.managed:
                    continue

                m2m_fields = [
                    m.name for m in meta.get_fields() if isinstance(m, ManyToManyField)
                ]

                exclude_fields = [
                    i.related_name
                    for i in meta.related_objects
                    if i.related_name and not i.related_model._meta.managed
                ]

                self.register(
                    model=model, m2m_fields=m2m_fields, exclude_fields=exclude_fields
                )

        if not isinstance(settings.AUDITLOG_STORE_JSON_CHANGES, bool):
            raise TypeError("Setting 'AUDITLOG_STORE_JSON_CHANGES' must be a boolean")

        self._register_models(settings.AUDITLOG_INCLUDE_TRACKING_MODELS)


auditlog = AuditlogModelRegistry()
