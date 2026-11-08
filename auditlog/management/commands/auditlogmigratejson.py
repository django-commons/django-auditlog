from math import ceil

from django.conf import settings
from django.core.management import CommandError, CommandParser
from django.core.management.base import BaseCommand

from auditlog import get_logentry_model

LogEntry = get_logentry_model()


class Command(BaseCommand):
    help = "Migrates changes from changes_text to json changes."
    requires_migrations_checks = True

    def add_arguments(self, parser: CommandParser):
        group = parser.add_argument_group()
        group.add_argument(
            "--check",
            action="store_true",
            help="Just check the status of the migration",
            dest="check",
        )
        group.add_argument(
            "-d",
            "--database",
            default=None,
            metavar="The database engine",
            help="If provided, the script will use native db operations. "
            "Otherwise, it will use LogEntry.objects.bulk_update",
            dest="db",
            type=str,
            choices=["postgres", "mysql", "oracle"],
        )
        group.add_argument(
            "-b",
            "--batch-size",
            default=500,
            help="Split the migration into multiple batches. If 0, then no batching will be done. "
            "When passing a -d/database, the batch value will be ignored.",
            dest="batch_size",
            type=int,
        )

    def handle(self, *args, **options):
        database = options["db"]
        batch_size = options["batch_size"]
        check = options["check"]

        if (not self.check_logs()) or check:
            return

        if database:
            result = self.migrate_using_sql(database)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated {result} records using native database operations."
                )
            )
        else:
            result = self.migrate_using_django(batch_size)
            self.stdout.write(
                self.style.SUCCESS(f"Updated {result} records using django operations.")
            )

        self.check_logs()

    def check_logs(self):
        count = self.get_logs().count()
        if count:
            self.stdout.write(f"There are {count} records that needs migration.")
            return True

        self.stdout.write(self.style.SUCCESS("All records have been migrated."))
        if settings.AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT:
            var_msg = self.style.WARNING(
                "AUDITLOG_USE_TEXT_CHANGES_IF_JSON_IS_NOT_PRESENT"
            )
            self.stdout.write(f"You can now set {var_msg} to False.")

        return False

    def get_logs(self):
        return (
            LogEntry.objects.filter(changes_text__isnull=False, changes__isnull=True)
            .exclude(changes_text__exact="")
            .order_by("pk")
        )

    def migrate_using_django(self, batch_size):
        def _apply_django_migration(_logs) -> tuple[int, int]:
            import json

            updated = []
            errors = []
            for log in _logs:
                try:
                    log.changes = json.loads(log.changes_text)
                except ValueError:
                    errors.append(log.id)
                else:
                    updated.append(log)

            LogEntry.objects.bulk_update(updated, fields=["changes"])
            if errors:
                self.stderr.write(
                    self.style.ERROR(
                        f"ValueError was raised while converting the logs with these ids into json."
                        f"They where not be included in this migration batch."
                        f"\n"
                        f"{errors}"
                    )
                )
            return len(updated), len(errors)

        logs = self.get_logs()

        if not batch_size:
            return _apply_django_migration(logs)[0]

        # Logs that cannot be converted stay in the queryset, so the batches
        # have to step over them. Otherwise every iteration re-fetches the same
        # failing logs and the ones behind them are never migrated.
        total_updated = 0
        offset = 0
        for _ in range(ceil(logs.count() / batch_size)):
            updated, errored = _apply_django_migration(
                self.get_logs()[offset:][:batch_size]
            )
            total_updated += updated
            offset += errored
        return total_updated

    def migrate_using_sql(self, database):
        from django.db import connection

        def postgres():
            with connection.cursor() as cursor:
                cursor.execute(f"""
                    UPDATE {LogEntry._meta.db_table}
                    SET changes="changes_text"::jsonb
                    WHERE changes_text IS NOT NULL
                        AND changes_text <> ''
                        AND changes IS NULL
                    """)
                return cursor.cursor.rowcount

        if database == "postgres":
            return postgres()

        raise CommandError(
            f"Migrating the records using {database} is not implemented. "
            f"Run this management command without passing a -d/--database argument."
        )
