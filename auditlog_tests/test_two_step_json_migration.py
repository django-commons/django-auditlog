import importlib.util
import json
import unittest
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import CommandError, call_command
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.db.utils import ConnectionHandler
from django.test import TestCase, override_settings
from django.test.utils import skipIf
from test_app.models import SimpleModel

from auditlog import get_logentry_model

LogEntry = get_logentry_model()


class TwoStepMigrationTest(TestCase):
    def test_use_text_changes_first(self):
        text_obj = '{"field": "changes_text"}'
        json_obj = {"field": "changes"}
        _params = [
            (True, None, text_obj, {"field": "changes_text"}),
            (True, json_obj, text_obj, json_obj),
            (True, None, "not json", {}),
            (False, json_obj, text_obj, json_obj),
        ]

        for setting_value, changes_value, changes_text_value, expected in _params:
            with self.subTest():
                entry = LogEntry(changes=changes_value, changes_text=changes_text_value)
                with self.settings(
                    AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT=setting_value
                ):
                    from auditlog import models

                    changes_dict = models._changes_func()(entry)
                    self.assertEqual(changes_dict, expected)


class AuditlogMigrateJsonTest(TestCase):
    def make_logentry(self):
        model = SimpleModel.objects.create(text="I am a simple model.")
        log_entry: LogEntry = model.history.first()
        log_entry.changes_text = json.dumps(log_entry.changes)
        log_entry.changes = None
        log_entry.save()
        return log_entry

    def call_command(self, *args, **kwargs):
        outbuf = StringIO()
        errbuf = StringIO()
        args = ("--no-color",) + args
        call_command(
            "auditlogmigratejson", *args, stdout=outbuf, stderr=errbuf, **kwargs
        )
        return outbuf.getvalue().strip(), errbuf.getvalue().strip()

    def test_nothing_to_migrate(self):
        outbuf, errbuf = self.call_command()

        msg = "All records have been migrated."
        self.assertEqual(outbuf, msg)

    @override_settings(AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT=True)
    def test_nothing_to_migrate_with_conf_true(self):
        outbuf, errbuf = self.call_command()

        msg = (
            "All records have been migrated.\n"
            "You can now set AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT to False."
        )

        self.assertEqual(outbuf, msg)

    def test_check(self):
        # Arrange
        log_entry = self.make_logentry()

        # Act
        outbuf, errbuf = self.call_command("--check")
        log_entry.refresh_from_db()

        # Assert
        self.assertEqual("There are 1 records that needs migration.", outbuf)
        self.assertEqual("", errbuf)
        self.assertIsNone(log_entry.changes)

    def test_using_django(self):
        # Arrange
        log_entry = self.make_logentry()

        # Act
        outbuf, errbuf = self.call_command("-b=0")
        log_entry.refresh_from_db()

        # Assert
        self.assertEqual(errbuf, "")
        self.assertIsNotNone(log_entry.changes)

    def test_using_django_batched(self):
        # Arrange
        log_entry_1 = self.make_logentry()
        log_entry_2 = self.make_logentry()

        # Act
        outbuf, errbuf = self.call_command("-b=1")
        log_entry_1.refresh_from_db()
        log_entry_2.refresh_from_db()

        # Assert
        self.assertEqual(errbuf, "")
        self.assertIsNotNone(log_entry_1.changes)
        self.assertIsNotNone(log_entry_2.changes)

    def test_using_django_batched_call_count(self):
        """
        This is split into a different test because I couldn't figure out how to properly patch bulk_update.
        For some reason, then I
        """
        # Arrange
        self.make_logentry()
        self.make_logentry()

        # Act
        LogEntry = get_logentry_model()
        path = f"{LogEntry.__module__}.{LogEntry.__name__}.objects.bulk_update"

        with patch(path) as bulk_update:
            outbuf, errbuf = self.call_command("-b=1")
            call_count = bulk_update.call_count

        # Assert
        self.assertEqual(call_count, 2)

    @skipIf(settings.TEST_DB_BACKEND != "postgresql", "PostgreSQL-specific test")
    def test_native_postgres(self):
        # Arrange
        log_entry = self.make_logentry()

        # Act
        outbuf, errbuf = self.call_command("-d=postgres")
        log_entry.refresh_from_db()

        # Assert
        self.assertEqual(errbuf, "")
        self.assertIsNotNone(log_entry.changes)

    @skipIf(settings.TEST_DB_BACKEND != "postgresql", "PostgreSQL-specific test")
    def test_native_postgres_changes_not_overwritten(self):
        # Arrange
        log_entry = self.make_logentry()
        log_entry.changes = original_changes = {"key": "value"}
        log_entry.changes_text = '{"key": "new value"}'
        log_entry.save()

        # Act
        outbuf, errbuf = self.call_command("-d=postgres")
        log_entry.refresh_from_db()

        # Assert
        self.assertEqual(errbuf, "")
        self.assertEqual(log_entry.changes, original_changes)

    def test_native_unsupported(self):
        # Arrange
        log_entry = self.make_logentry()
        msg = (
            "Migrating the records using oracle is not implemented. "
            "Run this management command without passing a -d/--database argument."
        )

        # Act
        with self.assertRaises(CommandError) as cm:
            self.call_command("-d=oracle")
        log_entry.refresh_from_db()

        # Assert
        self.assertEqual(msg, cm.exception.args[0])
        self.assertIsNone(log_entry.changes)

    def test_using_django_with_error(self):
        # Arrange
        log_entry = self.make_logentry()
        log_entry.changes_text = "not json"
        log_entry.save()

        # Act
        outbuf, errbuf = self.call_command()
        log_entry.refresh_from_db()

        # Assert
        msg = (
            f"ValueError was raised while converting the logs with these ids into json."
            f"They where not be included in this migration batch."
            f"\n"
            f"{[log_entry.id]}"
        )
        self.assertEqual(msg, errbuf)
        self.assertIsNone(log_entry.changes)


@unittest.skipUnless(
    importlib.util.find_spec("oracledb") is not None,
    "oracledb driver is required to build the Oracle schema editor",
)
class OracleMigrationSQLTest(TestCase):
    """Regression test for #757.

    The one-step ``0015`` migration used to alter ``changes`` straight from a
    ``TextField`` to a ``JSONField(null=True)``. On Oracle an empty string is
    stored as NULL, so that column is already nullable and the generated
    ``MODIFY "CHANGES" NULL`` statement fails with ORA-01451. This builds an
    Oracle schema editor in ``collect_sql`` mode (no live database) and checks
    the generated DDL for the forwards migration.
    """

    def _collect_oracle_sql(self):
        handler = ConnectionHandler(
            {
                "default": {
                    "ENGINE": "django.db.backends.oracle",
                    "NAME": "auditlog",
                    "USER": "auditlog",
                    "PASSWORD": "auditlog",
                    "HOST": "localhost",
                    "PORT": "1521",
                }
            }
        )
        connection = handler["default"]
        loader = MigrationLoader(None, ignore_no_migrations=True)
        state = ProjectState()
        for node in loader.graph.forwards_plan(("auditlog", "0014_logentry_cid")):
            state = loader.get_migration(*node).mutate_state(state, preserve=True)
        migration = loader.get_migration("auditlog", "0015_alter_logentry_changes")
        with connection.schema_editor(collect_sql=True, atomic=False) as editor:
            new_state = state.clone()
            for operation in migration.operations:
                from_state = new_state.clone()
                operation.state_forwards("auditlog", new_state)
                operation.database_forwards("auditlog", editor, from_state, new_state)
            return editor.collected_sql

    @override_settings(AUDITLOG_TWO_STEP_MIGRATION=False)
    def test_default_migration_does_not_modify_changes_to_null(self):
        collected_sql = self._collect_oracle_sql()
        self.assertNotIn(
            'MODIFY "CHANGES" NULL',
            "\n".join(collected_sql),
        )
