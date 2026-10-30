Upgrading to version 3
======================

Version 3.0.0 introduces breaking changes. Please review the migration guide below before upgrading.
If you're new to django-auditlog, you can ignore this part.

The major change in the version is that we're finally storing changes as json instead of json-text.
To convert the existing records, this version has a database migration that does just that.
However, this migration will take a long time if you have a huge amount of records,
causing your database and application to be out of sync until the migration is complete.

To avoid this, follow these steps:

1. Before upgrading the package, add these two variables to ``settings.py``:

    * ``AUDITLOG_TWO_STEP_MIGRATION = True``
    * ``AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT = True``

2. Upgrade the package. Your app will now start storing new records as JSON, but the old records will accessible via ``LogEntry.changes_text``.
3. Use the newly added ``auditlogmigratejson`` command to migrate your records. Run ``django-admin auditlogmigratejson --help`` to get more information.
4. Once all records are migrated, remove the variables listed above, or set their values to ``False``.

.. note::

    On Oracle the one-step migration previously failed with
    ``ORA-01451: column to be modified to NULL cannot be modified to NULL``,
    even on a fresh install. This is fixed, so the one-step migration now runs
    on Oracle. On older versions you can work around it by running the two-step
    migration described above.
