Usage
=====

.. py:currentmodule:: auditlog.models

Manually logging changes
------------------------

Auditlog log entries are simple :py:class:`LogEntry` model instances. This makes creating a new log entry very easy. For
even more convenience, :py:class:`LogEntryManager` provides a number of methods which take some work out of your hands.

See :doc:`internals` for all details.

.. _Automatically logging changes:

Automatically logging changes
-----------------------------

Auditlog can automatically log changes to objects for you. This functionality is based on Django's signals, but linking
your models to Auditlog is even easier than using signals.

Registering your model for logging can be done with a single line of code, as the following example illustrates:

.. code-block:: python

    from django.db import models

    from auditlog.registry import auditlog

    class MyModel(models.Model):
        pass
        # Model definition goes here

    auditlog.register(MyModel)

It is recommended to place the register code (``auditlog.register(MyModel)``) at the bottom of your ``models.py`` file.
This ensures that every time your model is imported it will also be registered to log changes. Auditlog makes sure that
each model is only registered once, otherwise duplicate log entries would occur.


**Logging access**

By default, Auditlog will only log changes to your model instances. If you want to log access to your model instances as well, Auditlog provides a mixin class for that purpose. Simply add the :py:class:`auditlog.mixins.LogAccessMixin` to your class based view and Auditlog will log access to your model instances. The mixin expects your view to have a ``get_object`` method that returns the model instance for which access shall be logged - this is usually the case for DetailViews and UpdateViews.

A DetailView utilizing the LogAccessMixin could look like the following example:

.. code-block:: python

    from django.views.generic import DetailView

    from auditlog.mixins import LogAccessMixin

    class MyModelDetailView(LogAccessMixin, DetailView):
        model = MyModel

        # View code goes here

You can also add log-access to function base views, as the following example illustrates:

.. code-block:: python

    from auditlog.signals import accessed

    def profile_view(request, pk):
        ## get the object you want to log access
        user = User.objects.get(pk=pk)

        ## log access
        accessed.send(user.__class__, instance=user)

        # View code goes here
        ...


**Excluding fields**

Fields that are excluded will not trigger saving a new log entry and will not show up in the recorded changes.

To exclude specific fields from the log you can pass ``include_fields`` or ``exclude_fields`` to the ``register``
method. If ``exclude_fields`` is specified the fields with the given names will not be included in the generated log
entries. If ``include_fields`` is specified only the fields with the given names will be included in the generated log
entries. Explicitly excluding fields through ``exclude_fields`` takes precedence over specifying which fields to
include.

For example, to exclude the field ``last_updated``, use::

    auditlog.register(MyModel, exclude_fields=['last_updated'])

.. versionadded:: 0.3.0

    Excluding fields

**Mapping fields**

If you have field names on your models that aren't intuitive or user friendly you can include a dictionary of field mappings
during the `register()` call.

.. code-block:: python

    from django.db import models

    from auditlog.models import AuditlogHistoryField
    from auditlog.registry import auditlog

    class MyModel(models.Model):
        sku = models.CharField(max_length=20)
        version = models.CharField(max_length=5)
        product = models.CharField(max_length=50, verbose_name='Product Name')
        history = AuditlogHistoryField()

    auditlog.register(MyModel, mapping_fields={'sku': 'Product No.', 'version': 'Product Revision'})

.. code-block:: python

    log = MyModel.objects.first().history.latest()
    log.changes_display_dict
    // retrieves changes with keys Product No. Product Revision, and Product Name
    // If you don't map a field it will fall back on the verbose_name

.. versionadded:: 0.5.0

You do not need to map all the fields of the model, any fields not mapped will fall back on their ``verbose_name``. Django provides a default ``verbose_name`` which is a "munged camel case version" so ``product_name`` would become ``Product Name`` by default.

**Masking fields**

Fields that contain sensitive info and we want keep track of field change but not to contain the exact change.

To mask specific fields from the log you can pass ``mask_fields`` to the ``register``
method. If ``mask_fields`` is specified, the first half value of the fields is masked using ``*``.

For example, to mask the field ``address``, use::

    auditlog.register(MyModel, mask_fields=['address'])

You can also specify a custom masking function by passing ``mask_callable`` to the ``register``
method. The ``mask_callable`` should be a dotted path to a function that takes a string and returns
a masked version of that string.

For example, to use a custom masking function::

    # In your_app/utils.py
    def custom_mask(value: str) -> str:
        return "****" + value[-4:]  # Only show last 4 characters

    # In your models.py
    auditlog.register(
        MyModel,
        mask_fields=['credit_card'],
        mask_callable='your_app.utils.custom_mask'
    )

Additionally, you can set a global default masking function that will be used when a model-specific
mask_callable is not provided. To do this, add the following to your Django settings::

    AUDITLOG_MASK_CALLABLE = 'your_app.utils.custom_mask'

The masking function priority is as follows:

1. Model-specific ``mask_callable`` if provided in ``register()``
2. ``AUDITLOG_MASK_CALLABLE`` from settings if configured
3. Default ``mask_str`` function which masks the first half of the string with asterisks

If ``mask_callable`` is not specified and no global default is configured, the default masking function will be used which masks
the first half of the string with asterisks.

.. versionadded:: 2.0.0

    Masking fields

**Many-to-many fields**

Changes to many-to-many fields are not tracked by default. If you want to enable tracking of a many-to-many field on a model, pass ``m2m_fields`` to the ``register`` method:

.. code-block:: python

    auditlog.register(MyModel, m2m_fields={"tags", "contacts"})

This functionality is based on the ``m2m_changed`` signal sent by the ``through`` model of the relationship.

Note that when the user changes multiple many-to-many fields on the same object through the admin, both adding and removing some objects from each, this code will generate multiple log entries: each log entry will represent a single operation (add or delete) of a single field, e.g. if you both add and delete values from 2 fields on the same form in the same request, you'll get 4 log entries.

.. versionadded:: 2.1.0

**Serialized Data**

The state of an object following a change action may be optionally serialized and persisted in the ``LogEntry.serialized_data`` JSONField. To enable this feature for a registered model, add ``serialize_data=True`` to the kwargs on the ``auditlog.register(...)`` method. Object serialization will not occur unless this kwarg is set.

.. code-block:: python

    auditlog.register(MyModel, serialize_data=True)

Objects are serialized using the Django core serializer. Keyword arguments may be passed to the serializer through ``serialize_kwargs``.

.. code-block:: python

    auditlog.register(
        MyModel,
        serialize_data=True,
        serialize_kwargs={"fields": ["foo", "bar", "biz", "baz"]}
    )

Note that all fields on the object will be serialized unless restricted with one or more configurations. The `serialize_kwargs` option contains a `fields` argument and this may be given an inclusive list of field names to serialize (as shown above). Alternatively, one may set ``serialize_auditlog_fields_only`` to ``True`` when registering a model with ``exclude_fields`` and ``include_fields`` set (as shown below). This will cause the data persisted in ``LogEntry.serialized_data`` to be limited to the same scope that is persisted within the ``LogEntry.changes`` field.

.. code-block:: python

    auditlog.register(
        MyModel,
        exclude_fields=["ssn", "confidential"]
        serialize_data=True,
        serialize_auditlog_fields_only=True
    )

Field masking is supported in object serialization. Any value belonging to a field whose name is found in the ``mask_fields`` list will be masked in the serialized object data. Masked values are obfuscated with asterisks in the same way as they are in the ``LogEntry.changes`` field.

Correlation ID
--------------

You can store a correlation ID (cid) in the log entries by:

1. Reading from a request header (specified by `AUDITLOG_CID_HEADER`)
2. Using a custom cid getter (specified by `AUDITLOG_CID_GETTER`)

Using the custom getter is helpful for integrating with a third-party cid package
such as `django-cid <https://pypi.org/project/django-cid/>`_.

Settings
--------

**AUDITLOG_INCLUDE_ALL_MODELS**

You can use this setting to register all your models:

.. code-block:: python

    AUDITLOG_INCLUDE_ALL_MODELS=True

.. versionadded:: 2.1.0

**AUDITLOG_EXCLUDE_TRACKING_FIELDS**

You can use this setting to exclude named fields from ALL models.
This is useful when lots of models share similar fields like
```created``` and ```modified``` and you want those excluded from
logging.
It will be considered when ``AUDITLOG_INCLUDE_ALL_MODELS`` is `True`.

.. code-block:: python

    AUDITLOG_EXCLUDE_TRACKING_FIELDS = (
        "created",
        "modified"
    )

.. versionadded:: 3.0.0

**AUDITLOG_DISABLE_REMOTE_ADDR**

When using "AuditlogMiddleware",
the IP address is logged by default, you can use this setting
to exclude the IP address from logging.
It will be considered when ``AUDITLOG_DISABLE_REMOTE_ADDR`` is `True`.

.. code-block:: python

    AUDITLOG_DISABLE_REMOTE_ADDR = True

.. versionadded:: 3.0.0

**AUDITLOG_MASK_TRACKING_FIELDS**

You can use this setting to mask specific field values in all tracked models
while still logging changes. This is useful when models contain sensitive fields
like `password`, `api_key`, or `secret_token` that should not be logged
in plain text but need to be auditable.

When a masked field changes, its value will be replaced with a masked
representation (e.g., `****`) in the audit log instead of storing the actual value.

This setting will be applied only when ``AUDITLOG_INCLUDE_ALL_MODELS`` is `True`.

.. code-block:: python

    AUDITLOG_MASK_TRACKING_FIELDS = (
    "password",
    "api_key",
    "secret_token"
    )

.. versionadded:: 3.1.0

**AUDITLOG_EXCLUDE_TRACKING_MODELS**

You can use this setting to exclude models in registration process.
It will be considered when ``AUDITLOG_INCLUDE_ALL_MODELS`` is `True`.

.. code-block:: python

    AUDITLOG_EXCLUDE_TRACKING_MODELS = (
        "<app_name>",
        "<app_name>.<model>"
    )

.. versionadded:: 2.1.0

**AUDITLOG_INCLUDE_TRACKING_MODELS**

You can use this setting to configure your models registration and other behaviours.
It must be a list or tuple. Each item in this setting can be a:

* ``str``: To register a model.
* ``dict``: To register a model and define its logging behaviour. e.g. include_fields, exclude_fields.

.. code-block:: python

    AUDITLOG_INCLUDE_TRACKING_MODELS = (
        "<appname>.<model1>",
        {
            "model": "<appname>.<model2>",
            "include_fields": ["field1", "field2"],
            "exclude_fields": ["field3", "field4"],
            "mapping_fields": {
                "field1": "FIELD",
            },
            "mask_fields": ["field5", "field6"],
            "m2m_fields": ["field7", "field8"],
            "serialize_data": True,
            "serialize_auditlog_fields_only": False,
            "serialize_kwargs": {"fields": ["foo", "bar", "biz", "baz"]},
        },
        "<appname>.<model3>",
    )

.. versionadded:: 2.1.0

**AUDITLOG_DISABLE_ON_RAW_SAVE**

Disables logging during raw save. (I.e. for instance using loaddata)

.. note::

    M2M operations will still be logged, since they're never considered `raw`. To disable them
    you must remove their setting or use the `disable_auditlog` context manager.

.. versionadded:: 2.2.0

**AUDITLOG_CID_HEADER**

The request header containing the Correlation ID value to use in all log entries created as a result of the request.
The value can of in the format `HTTP_MY_HEADER` or `my-header`.

.. versionadded:: 3.0.0

**AUDITLOG_CID_GETTER**

The function to use to retrieve the Correlation ID. The value can be a callable or a string import path.

If the value is `None`, the default getter will be used.

.. versionadded:: 3.0.0

**AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH**

This configuration variable defines the truncation behavior for strings in `changes_display_dict`, with a default value of `140` characters.

0: The entire string is truncated, resulting in an empty output.
Positive values (e.g., 5): Truncates the string, keeping only the specified number of characters followed by an ellipsis (...) after the limit.
Negative values: No truncation occurs, and the full string is displayed.

.. versionadded:: 3.1.0

**AUDITLOG_STORE_JSON_CHANGES**

This configuration variable defines whether to store changes as JSON.

This means that primitives such as booleans, integers, etc. will be represented using their JSON equivalents.  For example, instead of storing
`None` as a string, it will be stored as a JSON `null` in the `changes` field.  Same goes for other primitives.

.. versionadded:: 3.2.0

**AUDITLOG_USE_BASE_MANAGER**

This configuration variable determines whether to use `base managers
<https://docs.djangoproject.com/en/dev/topics/db/managers/#base-managers>`_ for
tracked models instead of their default managers.

This setting can be useful for applications where the default manager behaviour
hides some objects from the majority of ORM queries:

.. code-block:: python

    class SecretManager(models.Manager):
        def get_queryset(self):
            return super().get_queryset().filter(is_secret=False)


    @auditlog.register()
    class SwappedManagerModel(models.Model):
        is_secret = models.BooleanField(default=False)
        name = models.CharField(max_length=255)

        objects = SecretManager()

In this example, when ``AUDITLOG_USE_BASE_MANAGER`` is set to `True`, objects
with the `is_secret` field set will be made visible to Auditlog. Otherwise you
may see inaccurate data in log entries, recording changes to a seemingly
"non-existent" object with empty fields.

.. versionadded:: 3.4.0

**AUDITLOG_LOGENTRY_MODEL**

This configuration variable allows you to specify a custom model to be used instead of the default
:py:class:`auditlog.models.LogEntry` model for storing audit records.

By default, Auditlog stores change records in the built-in ``LogEntry`` model.
If you need to store additional information in each log entry (for example, a user role, request metadata,
or any other contextual data), you can define your own model by subclassing
:py:class:`auditlog.models.AbstractLogEntry` and configure it using this setting.

.. code-block:: python

    from django.db import models
    from auditlog.models import AbstractLogEntry

    class CustomLogEntryModel(AbstractLogEntry):
        role = models.CharField(max_length=100, null=True, blank=True)

Then, in your project settings:

.. code-block:: python

    AUDITLOG_LOGENTRY_MODEL = 'custom_log_app.CustomLogEntryModel'

Once defined, Auditlog will automatically use the specified model for all future log entries instead
of the default one.

.. note::

    - The custom model **must** inherit from :py:class:`auditlog.models.AbstractLogEntry`.
    - All fields and behaviors defined in :py:class:`AbstractLogEntry` should remain intact to ensure compatibility.
    - The app label and model name in ``AUDITLOG_LOGENTRY_MODEL`` must follow Django’s standard dotted notation
      (for example, ``"app_name.ModelName"``).

.. versionadded:: 3.5.0
    Custom LogEntry model configuration via ``AUDITLOG_LOGENTRY_MODEL``

**AUDITLOG_USE_FK_STRING_REPRESENTATION**

Determines how changes to foreign key fields are recorded in log entries.

When `True`, changes to foreign key fields are stored using the string representation of related objects.
When `False` (default), the primary key of the related objects is stored instead.

Before version 2.2.0, foreign key changes were stored using the string representation of the related objects.
Starting from version 2.2.0, the default behavior was updated to store the primary key of the related objects instead.

Before:

.. code-block:: json

    { "foreign_key_field": ["foo", "bar"] }


After:

.. code-block:: json

    { "foreign_key_field": [1, 2] }

You can use this option to enable the legacy behavior.

.. warning::

    This reintroduces a known issue https://github.com/django-commons/django-auditlog/issues/421
    Commission Error: Causes unnecessary LogEntries even though no update occurrs because the string representation in memory changed
    Omission Error: More common problem, a related object is updated to another object with the same string representation, no update is logged

    Beware of these problem when enabling this setting.

.. versionadded:: 3.4.0

Actors
------

Middleware
**********

When using automatic logging, the actor is empty by default. However, auditlog can set the actor from the current
request automatically. This does not need any custom code, adding a middleware class is enough. When an actor is logged
the remote address of that actor will be logged as well.

To enable the automatic logging of the actors, simply add the following to your ``MIDDLEWARE`` setting in your
project's configuration file::

    MIDDLEWARE = (
        # Request altering middleware, e.g., Django's default middleware classes
        'auditlog.middleware.AuditlogMiddleware',
        # Other middleware
    )

It is recommended to keep all middleware that alters the request loaded before Auditlog's middleware.

.. warning::

    Please keep in mind that every object change in a request that gets logged automatically will have the current request's
    user as actor. To only have some object changes to be logged with the current request's user as actor manual logging is
    required.

Context managers
----------------

Set actor
*********

To enable the automatic logging of the actors outside of request context (e.g. in a Celery task), you can use a context
manager::

    from auditlog.context import set_actor

    def do_stuff(actor_id: int):
        actor = get_user(actor_id)
        with set_actor(actor):
            # if your code here leads to creation of LogEntry instances, these will have the actor set
            ...


.. versionadded:: 2.1.0


Disable auditlog
****************

Disable auditlog temporary, for instance if you need to install a large fixture on a live system or cleanup
corrupt data::

    from auditlog.context import disable_auditlog

    with disable_auditlog():
        # Do things silently here
        ...


.. versionadded:: 2.2.0


Object history
--------------

Auditlog ships with a custom field that enables you to easily get the log entries that are relevant to your object. This
functionality is built on Django's content types framework (:py:mod:`django.contrib.contenttypes`). Using this field in
your models is equally easy as any other field::

    from django.db import models

    from auditlog.models import AuditlogHistoryField
    from auditlog.registry import auditlog

    class MyModel(models.Model):
        history = AuditlogHistoryField()
        # Model definition goes here

    auditlog.register(MyModel)

:py:class:`AuditlogHistoryField` accepts an optional :py:attr:`pk_indexable` parameter, which is either ``True`` or
``False``, this defaults to ``True``. If your model has a custom primary key that is not an integer value,
:py:attr:`pk_indexable` needs to be set to ``False``. Keep in mind that this might slow down queries.

The :py:class:`AuditlogHistoryField` provides easy access to :py:class:`LogEntry` instances related to the model instance. Here is an example of how to use it:

.. code-block:: html

    <div class="table-responsive">
      <table class="table table-striped table-bordered">
        <thead>
          <tr>
            <th>Field</th>
            <th>From</th>
            <th>To</th>
          </tr>
        </thead>
        <tbody>
        {% for key, value in mymodel.history.latest.changes_dict.items %}
          <tr>
            <td>{{ key }}</td>
            <td>{{ value.0|default:"None" }}</td>
            <td>{{ value.1|default:"None" }}</td>
          </tr>
        {% empty %}
          <p>No history for this item has been logged yet.</p>
        {% endfor %}
        </tbody>
      </table>
    </div>

If you want to display the changes in a more human readable format use the :py:class:`LogEntry`'s :py:attr:`changes_display_dict` instead. The :py:attr:`changes_display_dict` will make a few cosmetic changes to the data.

- Mapping Fields property will be used to display field names, falling back on ``verbose_name`` if no mapping field is present
- Fields with a value whose length is greater than 140 will be truncated with an ellipsis appended
- Date, Time, and DateTime fields will follow ``L10N`` formatting. If ``USE_L10N=False`` in your settings it will fall back on the settings defaults defined for ``DATE_FORMAT``, ``TIME_FORMAT``, and ``DATETIME_FORMAT``
- Fields with ``choices`` will be translated into their human readable form, this feature also supports choices defined on ``django-multiselectfield`` and Postgres's native ``ArrayField``

Check out the internals for the full list of attributes you can use to get associated :py:class:`LogEntry` instances.

Many-to-many relationships
--------------------------

.. versionadded:: 0.3.0

.. note::

    This section shows a workaround which can be used to track many-to-many relationships on older versions of django-auditlog. For versions 2.1.0 and onwards, please see the many-to-many fields section of :ref:`Automatically logging changes`.
    **Do not rely on the workaround here to be stable across releases.**

By default, many-to-many relationships are not tracked by Auditlog.

The history for a many-to-many relationship without an explicit 'through' model can be recorded by registering this
model as follows::

    auditlog.register(MyModel.related.through)

The log entries for all instances of the 'through' model that are related to a ``MyModel`` instance can be retrieved
with the :py:meth:`LogEntryManager.get_for_objects` method. The resulting QuerySet can be combined with any other
queryset of :py:class:`LogEntry` instances. This way it is possible to get a list of all changes on an object and its
related objects::

    obj = MyModel.objects.first()
    rel_history = LogEntry.objects.get_for_objects(obj.related.all())
    full_history = (obj.history.all() | rel_history.all()).order_by('-timestamp')

Management commands
-------------------

.. versionadded:: 0.4.0

Auditlog provides the ``auditlogflush`` management command to clear all log entries from the database.

By default, the command asks for confirmation. It is possible to run the command with the ``-y`` or ``--yes`` flag to skip
confirmation and immediately delete all entries.

You may also specify a date using the ``-b`` or ``--before-date`` option in ISO 8601 format (YYYY-mm-dd) to delete all
log entries prior to a given date. This may be used to implement time based retention windows.

.. versionadded:: 2.1.0

.. warning::

    Using the ``auditlogflush`` command deletes log entries permanently and irreversibly from the database.

Django Admin integration
------------------------

.. versionadded:: 0.4.1

When ``auditlog`` is added to your ``INSTALLED_APPS`` setting a customized admin class is active providing an enhanced
Django Admin interface for log entries.

Audit log history view
----------------------

.. versionadded:: 3.2.2

Use ``AuditlogHistoryAdminMixin`` to add a "View" link in the admin changelist for accessing each object's audit history::

    from auditlog.mixins import AuditlogHistoryAdminMixin

    @admin.register(MyModel)
    class MyModelAdmin(AuditlogHistoryAdminMixin, admin.ModelAdmin):
        show_auditlog_history_link = True

The history page displays paginated log entries with user, timestamp, action, and field changes. Override
``auditlog_history_template`` to customize the page layout.

The mixin provides the following configuration options:

- ``show_auditlog_history_link``: Set to ``True`` to display the "View" link in the admin changelist
- ``auditlog_history_template``: Template to use for rendering the history page (default: ``auditlog/object_history.html``)
- ``auditlog_history_per_page``: Number of log entries to display per page (default: 10)

