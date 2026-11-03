import datetime
import itertools
import json
import random
import warnings
from datetime import timezone
from unittest import mock, skipIf
from unittest.mock import patch

import freezegun
from dateutil.tz import gettz
from django import VERSION as DJANGO_VERSION
from django.apps import apps
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.contenttypes.models import ContentType
from django.core import management
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import models
from django.db.models import JSONField, Value
from django.db.models.functions import Now
from django.db.models.signals import pre_save
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse
from django.utils import dateformat, formats
from django.utils import timezone as django_timezone
from django.utils.encoding import smart_str
from django.utils.translation import gettext_lazy as _
from test_app.fixtures.custom_get_cid import get_cid as custom_get_cid
from test_app.models import (
    AdditionalDataIncludedModel,
    AltPrimaryKeyModel,
    AutoManyRelatedModel,
    CharfieldTextfieldModel,
    ChoicesFieldModel,
    CustomMaskModel,
    DateTimeFieldModel,
    JSONModel,
    ManyRelatedModel,
    ManyRelatedOtherModel,
    ModelForReusableThroughModel,
    ModelPrimaryKeyModel,
    NoDeleteHistoryModel,
    NullableJSONModel,
    ProxyModel,
    RelatedModel,
    RelatedModelParent,
    ReusableThroughRelatedModel,
    SecretM2MModel,
    SecretRelatedModel,
    SerializeNaturalKeyRelatedModel,
    SerializeOnlySomeOfThisModel,
    SerializePrimaryKeyRelatedModel,
    SerializeThisModel,
    SimpleExcludeModel,
    SimpleIncludeModel,
    SimpleMappingModel,
    SimpleMaskedModel,
    SimpleModel,
    SimpleNonManagedModel,
    SwappedManagerModel,
    UUIDPrimaryKeyModel,
)

from auditlog import get_logentry_model
from auditlog.admin import LogEntryAdmin
from auditlog.cid import get_cid
from auditlog.context import disable_auditlog, set_actor, set_extra_data
from auditlog.diff import mask_str, model_instance_diff
from auditlog.middleware import AuditlogMiddleware
from auditlog.models import DEFAULT_OBJECT_REPR
from auditlog.registry import AuditlogModelRegistry, AuditLogRegistrationError, auditlog
from auditlog.signals import post_log, pre_log

LogEntry = get_logentry_model()


class SimpleModelTest(TestCase):
    def setUp(self):
        self.obj = self.make_object()
        super().setUp()

    def make_object(self):
        return SimpleModel.objects.create(text="I am not difficult.")

    def test_create(self):
        """Creation is logged correctly."""
        # Get the object to work with
        obj = self.obj

        # Check for log entries
        self.assertEqual(obj.history.count(), 1, msg="There is one log entry")

        history = obj.history.get()
        self.check_create_log_entry(obj, history)

    def check_create_log_entry(self, obj, history):
        self.assertEqual(
            history.action, LogEntry.Action.CREATE, msg="Action is 'CREATE'"
        )
        self.assertEqual(history.object_repr, str(obj), msg="Representation is equal")

    def test_update(self):
        """Updates are logged correctly."""
        # Get the object to work with
        obj = self.obj

        # Change something
        self.update(obj)

        # Check for log entries
        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            1,
            msg="There is one log entry for 'UPDATE'",
        )

        history = obj.history.get(action=LogEntry.Action.UPDATE)
        self.check_update_log_entry(obj, history)

    def update(self, obj):
        obj.boolean = True
        obj.save()

    def check_update_log_entry(self, obj, history):
        self.assertDictEqual(
            history.changes,
            {"boolean": ["False", "True"]},
            msg="The change is correctly logged",
        )
        self.assertEqual(
            history.changes_str,
            "boolean: False → True",
            msg="Changes string is correct",
        )

    def test_update_specific_field_supplied_via_save_method(self):
        obj = self.obj

        # Change 2 fields, but save one only.
        obj.boolean = True
        obj.text = "Short text"
        obj.save(update_fields=["boolean"])

        # This implicitly asserts there is only one UPDATE change since the `.get` would fail otherwise.
        self.assertDictEqual(
            obj.history.get(action=LogEntry.Action.UPDATE).changes,
            {"boolean": ["False", "True"]},
            msg=(
                "Object modifications that are not saved to DB are not logged "
                "when using the `update_fields`."
            ),
        )
        self.assertEqual(
            obj.history.get(action=LogEntry.Action.UPDATE).changes_str,
            "boolean: False → True",
            msg="Changes string is correct",
        )

    def test_django_update_fields_edge_cases(self):
        """
        The test ensures that if Django's `update_fields` behavior ever changes for special
        values `(None, [])`, the package should too.
        https://docs.djangoproject.com/en/3.2/ref/models/instances/#specifying-which-fields-to-save
        """
        obj = self.obj

        # Change boolean, but save no changes by passing an empty list.
        obj.boolean = True
        obj.save(update_fields=[])

        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            0,
            msg="There is no log entries created",
        )
        obj.refresh_from_db()
        self.assertFalse(obj.boolean)  # Change didn't persist in DB as expected.

        # Passing `None` should save both fields according to Django.
        obj.integer = 1
        obj.boolean = True
        obj.save(update_fields=None)
        self.assertDictEqual(
            obj.history.get(action=LogEntry.Action.UPDATE).changes,
            {"boolean": ["False", "True"], "integer": ["None", "1"]},
            msg="The 2 fields changed are correctly logged",
        )
        self.assertEqual(
            obj.history.get(action=LogEntry.Action.UPDATE).changes_str,
            "boolean: False → True; integer: None → 1",
            msg="Changes string is correct",
        )

    def test_delete(self):
        """Deletion is logged correctly."""
        # Get the object to work with
        obj = self.obj
        content_type = ContentType.objects.get_for_model(obj.__class__)
        pk = obj.pk

        # Delete the object
        self.delete(obj)

        # Check for log entries
        qs = LogEntry.objects.filter(content_type=content_type, object_pk=str(pk))
        self.assertEqual(qs.count(), 1, msg="There is one log entry for 'DELETE'")

        history = qs.get()
        self.check_delete_log_entry(obj, history)

    def delete(self, obj):
        obj.delete()

    def check_delete_log_entry(self, obj, history):
        pass

    def test_recreate(self):
        self.obj.delete()
        self.setUp()
        self.test_create()

    def test_create_log_to_object_from_other_database(self):
        msg = "The log should not try to write to the same database as the object"

        instance = self.obj
        # simulate object obtained from a different database (read only)
        instance._state.db = "replica"

        changes = model_instance_diff(None, instance)

        log_entry = LogEntry.objects.log_create(
            instance,
            action=LogEntry.Action.CREATE,
            changes=json.dumps(changes),
        )
        self.assertEqual(
            log_entry._state.db, "default", msg=msg
        )  # must be created in default database

    def test_default_timestamp(self):
        start = django_timezone.now()
        self.test_recreate()
        end = django_timezone.now()
        history = self.obj.history.latest()
        self.assertTrue(start <= history.timestamp <= end)

    def test_manual_timestamp(self):
        timestamp = datetime.datetime(1999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        LogEntry.objects.log_create(
            instance=self.obj,
            timestamp=timestamp,
            changes="foo bar",
            action=LogEntry.Action.UPDATE,
        )
        history = self.obj.history.filter(timestamp=timestamp, changes="foo bar")
        self.assertTrue(history.exists())

    def test_create_duplicate_with_pk_none(self):
        initial_entries_count = LogEntry.objects.count()
        obj = self.obj
        obj.pk = None
        obj.save()
        self.assertEqual(LogEntry.objects.count(), initial_entries_count + 1)


class NoActorMixin:
    def check_create_log_entry(self, obj, log_entry):
        super().check_create_log_entry(obj, log_entry)
        self.assertIsNone(log_entry.actor)

    def check_update_log_entry(self, obj, log_entry):
        super().check_update_log_entry(obj, log_entry)
        self.assertIsNone(log_entry.actor)

    def check_delete_log_entry(self, obj, log_entry):
        super().check_delete_log_entry(obj, log_entry)
        self.assertIsNone(log_entry.actor)


class WithActorMixinBase:
    sequence = itertools.count()

    def setUp(self):
        username = f"actor_{next(self.sequence)}"
        self.user = get_user_model().objects.create(
            username=username,
            email=f"{username}@example.com",
            password="secret",
        )
        super().setUp()

    def tearDown(self):
        user_email = self.user.email
        self.user.delete()
        auditlog_entries = LogEntry.objects.filter(actor_email=user_email).all()
        self.assertIsNotNone(auditlog_entries, msg="All auditlog entries are deleted.")
        super().tearDown()

    def check_create_log_entry(self, obj, log_entry):
        super().check_create_log_entry(obj, log_entry)
        self.assertEqual(log_entry.actor, self.user)
        self.assertEqual(log_entry.actor_email, self.user.email)

    def update(self, obj):
        with set_actor(self.user):
            return super().update(obj)

    def check_update_log_entry(self, obj, log_entry):
        super().check_update_log_entry(obj, log_entry)
        self.assertEqual(log_entry.actor, self.user)
        self.assertEqual(log_entry.actor_email, self.user.email)

    def delete(self, obj):
        with set_actor(self.user):
            return super().delete(obj)

    def check_delete_log_entry(self, obj, log_entry):
        super().check_delete_log_entry(obj, log_entry)
        self.assertEqual(log_entry.actor, self.user)
        self.assertEqual(log_entry.actor_email, self.user.email)


class WithActorMixin(WithActorMixinBase):
    def make_object(self):
        with set_actor(self.user):
            return super().make_object()


class AltPrimaryKeyModelBase(SimpleModelTest):
    def make_object(self):
        return AltPrimaryKeyModel.objects.create(
            key=str(datetime.datetime.now()), text="I am strange."
        )


class AltPrimaryKeyModelTest(NoActorMixin, AltPrimaryKeyModelBase):
    pass


class AltPrimaryKeyModelWithActorTest(WithActorMixin, AltPrimaryKeyModelBase):
    pass


class UUIDPrimaryKeyModelModelBase(SimpleModelTest):
    def make_object(self):
        return UUIDPrimaryKeyModel.objects.create(text="I am strange.")

    def test_get_for_object(self):
        self.obj.boolean = True
        self.obj.save()

        self.assertEqual(LogEntry.objects.get_for_object(self.obj).count(), 2)

    def test_get_for_objects(self):
        self.obj.boolean = True
        self.obj.save()

        self.assertEqual(
            LogEntry.objects.get_for_objects(UUIDPrimaryKeyModel.objects.all()).count(),
            2,
        )


class UUIDPrimaryKeyModelModelTest(NoActorMixin, UUIDPrimaryKeyModelModelBase):
    pass


class UUIDPrimaryKeyModelModelWithActorTest(
    WithActorMixin, UUIDPrimaryKeyModelModelBase
):
    pass


class ModelPrimaryKeyModelBase(SimpleModelTest):
    def make_object(self):
        self.key = super().make_object()
        return ModelPrimaryKeyModel.objects.create(key=self.key, text="I am strange.")

    def test_create_duplicate_with_pk_none(self):
        pass


class ModelPrimaryKeyModelTest(NoActorMixin, ModelPrimaryKeyModelBase):
    pass


class ModelPrimaryKeyModelWithActorTest(WithActorMixin, ModelPrimaryKeyModelBase):
    pass


# Must inherit from TransactionTestCase to use self.assertNumQueries.
class ModelPrimaryKeyTest(TransactionTestCase):

    def _fixture_teardown(self):
        call_command("flush", verbosity=0, interactive=False, allow_cascade=True)

    def test_get_pk_value(self):
        """
        Test that the primary key can be retrieved without additional database queries.
        """
        key = SimpleModel.objects.create(text="I am not difficult.")
        obj = ModelPrimaryKeyModel.objects.create(key=key, text="I am strange.")
        # Refresh the object so the primary key object is not cached.
        obj.refresh_from_db()
        with self.assertNumQueries(0):
            pk = LogEntry.objects._get_pk_value(obj)
        self.assertEqual(pk, obj.pk)
        self.assertEqual(pk, key.pk)
        # Sanity check: verify accessing obj.key causes database access.
        with self.assertNumQueries(1):
            pk = obj.key.pk
        self.assertEqual(pk, obj.pk)
        self.assertEqual(pk, key.pk)


class ProxyModelBase(SimpleModelTest):
    def make_object(self):
        return ProxyModel.objects.create(text="I am not what you think.")


class ProxyModelTest(NoActorMixin, ProxyModelBase):
    pass


class ProxyModelWithActorTest(WithActorMixin, ProxyModelBase):
    pass


class ManyRelatedModelTest(TestCase):
    """
    Test the behaviour of many-to-many relationships.
    """

    def setUp(self):
        self.obj = ManyRelatedModel.objects.create()
        self.recursive = ManyRelatedModel.objects.create()
        self.related = ManyRelatedOtherModel.objects.create()
        self.obj_reusable = ModelForReusableThroughModel.objects.create()
        self.obj_reusable_related = ReusableThroughRelatedModel.objects.create()
        self.base_log_entry_count = (
            LogEntry.objects.count()
        )  # created by the create() calls above

    def test_recursive(self):
        self.obj.recursive.add(self.recursive)
        self.assertEqual(
            LogEntry.objects.get_for_objects(self.obj.recursive.all()).first(),
            self.recursive.history.first(),
        )

    def test_related_add_from_first_side(self):
        self.obj.related.add(self.related)
        self.assertEqual(
            LogEntry.objects.get_for_objects(self.obj.related.all()).first(),
            self.related.history.first(),
        )
        self.assertEqual(LogEntry.objects.count(), self.base_log_entry_count + 1)

    def test_related_add_from_other_side(self):
        self.related.related.add(self.obj)
        self.assertEqual(
            LogEntry.objects.get_for_objects(self.obj.related.all()).first(),
            self.related.history.first(),
        )
        self.assertEqual(LogEntry.objects.count(), self.base_log_entry_count + 1)

    def test_related_remove_from_first_side(self):
        self.obj.related.add(self.related)
        self.obj.related.remove(self.related)
        self.assertEqual(LogEntry.objects.count(), self.base_log_entry_count + 2)

    def test_related_remove_from_other_side(self):
        self.related.related.add(self.obj)
        self.related.related.remove(self.obj)
        self.assertEqual(LogEntry.objects.count(), self.base_log_entry_count + 2)

    def test_related_clear_from_first_side(self):
        self.obj.related.add(self.related)
        self.obj.related.clear()
        self.assertEqual(LogEntry.objects.count(), self.base_log_entry_count + 2)

    def test_related_clear_from_other_side(self):
        self.related.related.add(self.obj)
        self.related.related.clear()
        self.assertEqual(LogEntry.objects.count(), self.base_log_entry_count + 2)

    def test_additional_data(self):
        self.obj.related.add(self.related)
        log_entry = self.obj.history.first()
        self.assertEqual(
            log_entry.additional_data, {"related_model_id": self.related.id}
        )

    def test_changes(self):
        self.obj.related.add(self.related)
        log_entry = self.obj.history.first()
        self.assertEqual(
            log_entry.changes,
            {
                "related": {
                    "type": "m2m",
                    "operation": "add",
                    "objects": [smart_str(self.related)],
                }
            },
        )

    def test_changes_str(self):
        self.obj.related.add(self.related)
        log_entry = self.obj.history.first()
        self.assertEqual(
            log_entry.changes_str, f"related: add {[smart_str(self.related)]}"
        )

    def test_adding_existing_related_obj(self):
        self.obj.related.add(self.related)
        log_entry = self.obj.history.first()
        self.assertEqual(
            log_entry.changes,
            {
                "related": {
                    "type": "m2m",
                    "operation": "add",
                    "objects": [smart_str(self.related)],
                }
            },
        )
        # Add same related obj again.
        self.obj.related.add(self.related)
        latest_log_entry = self.obj.history.first()
        self.assertEqual(log_entry.id, latest_log_entry.id)

    def test_object_repr_related_deleted(self):
        """No error is raised when __str__() raises ObjectDoesNotExist."""
        # monkey-patching to avoid extra logic in the model
        with mock.patch.object(self.obj.__class__, "__str__") as mock_str:
            mock_str.side_effect = self.related.DoesNotExist("I am fake")
            self.obj.related.add(self.related)
            log_entry = self.obj.history.first()
            self.assertEqual(log_entry.object_repr, DEFAULT_OBJECT_REPR)

    def test_changes_not_duplicated_with_reusable_through_model(self):
        self.obj_reusable.related.add(self.obj_reusable_related)
        entries = self.obj_reusable.history.all()
        self.assertEqual(len(entries), 1)


class MiddlewareTest(TestCase):
    """
    Test the middleware responsible for connecting and disconnecting the signals used in automatic logging.
    """

    def setUp(self):
        self.get_response_mock = mock.Mock()
        self.response_mock = mock.Mock()
        self.middleware = AuditlogMiddleware(get_response=self.get_response_mock)
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="test", email="test@example.com", password="top_secret"
        )

    def side_effect(self, assertion):
        def inner(request):
            assertion()
            return self.response_mock

        return inner

    def assert_has_listeners(self):
        self.assertTrue(pre_save.has_listeners(LogEntry))

    def assert_no_listeners(self):
        self.assertFalse(pre_save.has_listeners(LogEntry))

    def test_request_anonymous(self):
        """No actor will be logged when a user is not logged in."""
        request = self.factory.get("/")
        request.user = AnonymousUser()

        self.get_response_mock.side_effect = self.side_effect(self.assert_has_listeners)

        response = self.middleware(request)

        self.assertIs(response, self.response_mock)
        self.get_response_mock.assert_called_once_with(request)
        self.assert_no_listeners()

    def test_request(self):
        """The actor will be logged when a user is logged in."""
        request = self.factory.get("/")
        request.user = self.user

        self.get_response_mock.side_effect = self.side_effect(self.assert_has_listeners)

        response = self.middleware(request)

        self.assertIs(response, self.response_mock)
        self.get_response_mock.assert_called_once_with(request)
        self.assert_no_listeners()

    def test_exception(self):
        """The signal will be disconnected when an exception is raised."""
        request = self.factory.get("/")
        request.user = self.user

        SomeException = type("SomeException", (Exception,), {})

        self.get_response_mock.side_effect = SomeException

        with self.assertRaises(SomeException):
            self.middleware(request)

        self.assert_no_listeners()

    def test_init_middleware(self):
        with override_settings(AUDITLOG_DISABLE_REMOTE_ADDR="str"):
            with self.assertRaisesMessage(
                TypeError, "Setting 'AUDITLOG_DISABLE_REMOTE_ADDR' must be a boolean"
            ):
                AuditlogMiddleware()

    def test_disable_remote_addr(self):
        with override_settings(AUDITLOG_DISABLE_REMOTE_ADDR=True):
            headers = {"HTTP_X_FORWARDED_FOR": "127.0.0.2"}
            request = self.factory.get("/", **headers)
            remote_addr = self.middleware._get_remote_addr(request)
            self.assertIsNone(remote_addr)

    def test_get_remote_addr(self):
        tests = [  # (headers, expected_remote_addr)
            ({}, "127.0.0.1"),
            ({"HTTP_X_FORWARDED_FOR": "127.0.0.2"}, "127.0.0.2"),
            ({"HTTP_X_FORWARDED_FOR": "127.0.0.3:1234"}, "127.0.0.3"),
            ({"HTTP_X_FORWARDED_FOR": "2606:4700:4700::1111"}, "2606:4700:4700::1111"),
            (
                {"HTTP_X_FORWARDED_FOR": "[2606:4700:4700::1001]:1234"},
                "2606:4700:4700::1001",
            ),
        ]
        for headers, expected_remote_addr in tests:
            with self.subTest(headers=headers):
                request = self.factory.get("/", **headers)
                self.assertEqual(
                    self.middleware._get_remote_addr(request), expected_remote_addr
                )

    def test_get_remote_port(self):
        headers = {
            "HTTP_X_FORWARDED_PORT": "12345",
        }
        request = self.factory.get("/", **headers)
        self.assertEqual(self.middleware._get_remote_port(request), 12345)

    def test_cid(self):
        header = str(settings.AUDITLOG_CID_HEADER).lstrip("HTTP_").replace("_", "-")
        header_meta = "HTTP_" + header.upper().replace("-", "_")
        cid = "random_CID"

        _settings = [
            # these tuples test reading the cid from the header defined in the settings
            ({"AUDITLOG_CID_HEADER": header}, cid),  # x-correlation-id
            ({"AUDITLOG_CID_HEADER": header_meta}, cid),  # HTTP_X_CORRELATION_ID
            ({"AUDITLOG_CID_HEADER": None}, None),
            # these two tuples test using a custom getter.
            # Here, we don't necessarily care about the cid that was set in set_cid
            (
                {"AUDITLOG_CID_GETTER": "test_app.fixtures.custom_get_cid.get_cid"},
                custom_get_cid(),
            ),
            ({"AUDITLOG_CID_GETTER": custom_get_cid}, custom_get_cid()),
        ]
        for setting, expected_result in _settings:
            with self.subTest():
                with self.settings(**setting):
                    request = self.factory.get("/", **{header_meta: cid})
                    self.middleware(request)

                    obj = SimpleModel.objects.create(text="I am not difficult.")
                    history = obj.history.get(action=LogEntry.Action.CREATE)

                    self.assertEqual(history.cid, expected_result)
                    self.assertEqual(get_cid(), expected_result)

    def test_set_actor_anonymous_request(self):
        """
        The remote address will be set even when there is no actor
        """
        remote_addr = "123.213.145.99"
        remote_port = 12345
        actor = None

        with set_actor(actor=actor, remote_addr=remote_addr, remote_port=remote_port):
            obj = SimpleModel.objects.create(text="I am not difficult.")

            history = obj.history.get()
            self.assertEqual(
                history.remote_addr,
                remote_addr,
                msg=f"Remote address is {remote_addr}",
            )
            self.assertEqual(
                history.remote_port,
                remote_port,
                msg=f"Remote port is {remote_port}",
            )
            self.assertIsNone(history.actor, msg="Actor is `None` for anonymous user")

    def test_get_actor(self):
        params = [
            (AnonymousUser(), None, "The user is anonymous so the actor is `None`"),
            (self.user, self.user, "The use is authenticated so it is the actor"),
            (None, None, "There is no actor"),
            ("1234", None, "The value of request.user is not a valid user model"),
        ]
        for user, actor, msg in params:
            with self.subTest(msg):
                request = self.factory.get("/")
                request.user = user

                self.assertEqual(self.middleware._get_actor(request), actor)


class SimpleIncludeModelTest(TestCase):
    """Log only changes in include_fields"""

    def test_specified_save_fields_are_ignored_if_not_included(self):
        obj = SimpleIncludeModel.objects.create(label="Initial label", text="Text")
        obj.text = "New text"
        obj.save(update_fields=["text"])

        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            0,
            msg="Text change was not logged, even when passed explicitly",
        )

        obj.label = "New label"
        obj.text = "Newer text"
        obj.save(update_fields=["text", "label"])

        self.assertDictEqual(
            obj.history.get(action=LogEntry.Action.UPDATE).changes,
            {"label": ["Initial label", "New label"]},
            msg="Only the label was logged, regardless of multiple entries in `update_fields`",
        )
        self.assertEqual(
            obj.history.get(action=LogEntry.Action.UPDATE).changes_str,
            "label: Initial label → New label",
            msg="Changes string is correct",
        )

    def test_register_include_fields(self):
        sim = SimpleIncludeModel(label="Include model", text="Looong text")
        sim.save()
        self.assertEqual(sim.history.count(), 1, msg="There is one log entry")

        # Change label, record
        sim.label = "Changed label"
        sim.save()
        self.assertEqual(sim.history.count(), 2, msg="There are two log entries")

        # Change text, ignore
        sim.text = "Short text"
        sim.save()
        self.assertEqual(sim.history.count(), 2, msg="There are two log entries")


class SimpleExcludeModelTest(TestCase):
    """Log only changes that are not in exclude_fields"""

    def test_specified_save_fields_are_excluded_normally(self):
        obj = SimpleExcludeModel.objects.create(label="Exclude model", text="Text")
        obj.text = "New text"
        obj.save(update_fields=["text"])

        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            0,
            msg="Text change was not logged, even when passed explicitly",
        )

    def test_register_exclude_fields(self):
        sem = SimpleExcludeModel(label="Exclude model", text="Looong text")
        sem.save()
        self.assertEqual(sem.history.count(), 1, msg="There is one log entry")

        # Change label, record it.
        sem.label = "Changed label"
        sem.save()
        self.assertEqual(sem.history.count(), 2, msg="There are two log entries")

        # Change text, ignore it.
        sem.text = "Short text"
        sem.save()
        self.assertEqual(sem.history.count(), 2, msg="There are two log entries")


class SimpleMappingModelTest(TestCase):
    """Diff displays fields as mapped field names where available through mapping_fields"""

    def test_register_mapping_fields(self):
        smm = SimpleMappingModel(
            sku="ASD301301A6", vtxt="2.1.5", not_mapped="Not mapped"
        )
        smm.save()
        self.assertEqual(
            smm.history.latest().changes_dict["sku"][1],
            "ASD301301A6",
            msg="The diff function retains 'sku' and can be retrieved.",
        )
        self.assertEqual(
            smm.history.latest().changes_dict["not_mapped"][1],
            "Not mapped",
            msg="The diff function does not map 'not_mapped' and can be retrieved.",
        )
        self.assertEqual(
            smm.history.latest().changes_display_dict["Product No."][1],
            "ASD301301A6",
            msg="The diff function maps 'sku' as 'Product No.' and can be retrieved.",
        )
        self.assertEqual(
            smm.history.latest().changes_display_dict["Version"][1],
            "2.1.5",
            msg=(
                "The diff function maps 'vtxt' as 'Version' through verbose_name"
                " setting on the model field and can be retrieved."
            ),
        )
        self.assertEqual(
            smm.history.latest().changes_display_dict["not mapped"][1],
            "Not mapped",
            msg=(
                "The diff function uses the django default verbose name for 'not_mapped'"
                " and can be retrieved."
            ),
        )

    @override_settings(AUDITLOG_STORE_JSON_CHANGES=True)
    def test_changes_display_dict_with_json_changes_and_simplemodel(self):
        sm = SimpleModel(integer=37, text="my simple model instance")
        sm.save()
        self.assertEqual(
            sm.history.latest().changes_display_dict["integer"][1],
            "37",
        )
        self.assertEqual(
            sm.history.latest().changes_display_dict["text"][1],
            "my simple model instance",
        )

    @override_settings(AUDITLOG_STORE_JSON_CHANGES=True)
    def test_register_mapping_fields_with_json_changes(self):
        smm = SimpleMappingModel(
            sku="ASD301301A6", vtxt="2.1.5", not_mapped="Not mapped"
        )
        smm.save()
        self.assertEqual(
            smm.history.latest().changes_dict["sku"][1],
            "ASD301301A6",
            msg="The diff function retains 'sku' and can be retrieved.",
        )
        self.assertEqual(
            smm.history.latest().changes_dict["not_mapped"][1],
            "Not mapped",
            msg="The diff function does not map 'not_mapped' and can be retrieved.",
        )
        self.assertEqual(
            smm.history.latest().changes_display_dict["Product No."][1],
            "ASD301301A6",
            msg="The diff function maps 'sku' as 'Product No.' and can be retrieved.",
        )
        self.assertEqual(
            smm.history.latest().changes_display_dict["Version"][1],
            "2.1.5",
            msg=(
                "The diff function maps 'vtxt' as 'Version' through verbose_name"
                " setting on the model field and can be retrieved."
            ),
        )
        self.assertEqual(
            smm.history.latest().changes_display_dict["not mapped"][1],
            "Not mapped",
            msg=(
                "The diff function uses the django default verbose name for 'not_mapped'"
                " and can be retrieved."
            ),
        )


class SimpleMaskedFieldsModelTest(TestCase):
    """Log masked changes for fields in mask_fields"""

    def test_register_mask_fields(self):
        smm = SimpleMaskedModel(address="Sensitive data", text="Looong text")
        smm.save()
        self.assertEqual(
            smm.history.latest().changes_dict["address"][1],
            "*******ve data",
            msg="The diff function masks 'address' field.",
        )

    @override_settings(
        AUDITLOG_MASK_CALLABLE="auditlog_tests.test_app.mask.custom_mask_str"
    )
    def test_global_mask_callable(self):
        """Test that global mask_callable from settings is used when model-specific one is not provided"""
        instance = SimpleMaskedModel.objects.create(
            address="1234567890123456", text="Some text"
        )

        self.assertEqual(
            instance.history.latest().changes_dict["address"][1],
            "****3456",
            msg="The global masking function should be used when model-specific one is not provided",
        )


class AdditionalDataModelTest(TestCase):
    """Log additional data if get_additional_data is defined in the model"""

    def test_model_without_additional_data(self):
        obj_wo_additional_data = SimpleModel.objects.create(
            text="No additional " "data"
        )
        obj_log_entry = obj_wo_additional_data.history.get()
        self.assertIsNone(obj_log_entry.additional_data)

    def test_model_with_additional_data(self):
        related_model = SimpleModel.objects.create(text="Log my reference")
        obj_with_additional_data = AdditionalDataIncludedModel(
            label="Additional data to log entries", related=related_model
        )
        obj_with_additional_data.save()
        self.assertEqual(
            obj_with_additional_data.history.count(), 1, msg="There is 1 log entry"
        )
        log_entry = obj_with_additional_data.history.get()
        extra_data = log_entry.additional_data
        self.assertIsNotNone(extra_data)
        self.assertEqual(
            extra_data["related_model_text"],
            related_model.text,
            msg="Related model's text is logged",
        )
        self.assertEqual(
            extra_data["related_model_id"],
            related_model.id,
            msg="Related model's id is logged",
        )


class DateTimeFieldModelTest(TestCase):
    """Tests if DateTimeField changes are recognised correctly"""

    utc_plus_one = django_timezone.get_fixed_timezone(datetime.timedelta(hours=1))
    now = django_timezone.now()

    def setUp(self):
        super().setUp()
        self._context = warnings.catch_warnings()
        self._context.__enter__()
        warnings.filterwarnings(
            "ignore", message=".*naive datetime", category=RuntimeWarning
        )

    def tearDown(self):
        self._context.__exit__()
        super().tearDown()

    def test_model_with_same_time(self):
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

        # Change timestamp to same datetime and timezone
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        dtm.timestamp = timestamp
        dtm.date = datetime.date(2017, 1, 10)
        dtm.time = datetime.time(12, 0)
        dtm.save()

        # Nothing should have changed
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

    def test_model_with_different_timezone(self):
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

        # Change timestamp to same datetime in another timezone
        timestamp = datetime.datetime(2017, 1, 10, 13, 0, tzinfo=self.utc_plus_one)
        dtm.timestamp = timestamp
        dtm.save()

        # Nothing should have changed
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

    def test_model_with_different_datetime(self):
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

        # Change timestamp to another datetime in the same timezone
        timestamp = datetime.datetime(2017, 1, 10, 13, 0, tzinfo=timezone.utc)
        dtm.timestamp = timestamp
        dtm.save()

        # The time should have changed.
        self.assertEqual(dtm.history.count(), 2, msg="There are two log entries")

    def test_model_with_different_date(self):
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

        # Change timestamp to another datetime in the same timezone
        date = datetime.datetime(2017, 1, 11)
        dtm.date = date
        dtm.save()

        # The time should have changed.
        self.assertEqual(dtm.history.count(), 2, msg="There are two log entries")

    def test_model_with_different_time(self):
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

        # Change timestamp to another datetime in the same timezone
        time = datetime.time(6, 0)
        dtm.time = time
        dtm.save()

        # The time should have changed.
        self.assertEqual(dtm.history.count(), 2, msg="There are two log entries")

    def test_model_with_different_time_and_timezone(self):
        timestamp = datetime.datetime(2017, 1, 10, 12, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(dtm.history.count(), 1, msg="There is one log entry")

        # Change timestamp to another datetime and another timezone
        timestamp = datetime.datetime(2017, 1, 10, 14, 0, tzinfo=self.utc_plus_one)
        dtm.timestamp = timestamp
        dtm.save()

        # The time should have changed.
        self.assertEqual(dtm.history.count(), 2, msg="There are two log entries")

    def test_changes_display_dict_datetime(self):
        timestamp = datetime.datetime(2017, 1, 10, 15, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        localized_timestamp = timestamp.astimezone(gettz(settings.TIME_ZONE))
        self.assertEqual(
            dtm.history.latest().changes_display_dict["timestamp"][1],
            dateformat.format(localized_timestamp, settings.DATETIME_FORMAT),
            msg=(
                "The datetime should be formatted according to Django's settings for"
                " DATETIME_FORMAT"
            ),
        )
        timestamp = django_timezone.now()
        dtm.timestamp = timestamp
        dtm.save()
        localized_timestamp = timestamp.astimezone(gettz(settings.TIME_ZONE))
        self.assertEqual(
            dtm.history.latest().changes_display_dict["timestamp"][1],
            dateformat.format(localized_timestamp, settings.DATETIME_FORMAT),
            msg=(
                "The datetime should be formatted according to Django's settings for"
                " DATETIME_FORMAT"
            ),
        )

        # Change USE_L10N = True
        with self.settings(USE_L10N=True, LANGUAGE_CODE="en-GB"):
            self.assertEqual(
                dtm.history.latest().changes_display_dict["timestamp"][1],
                formats.localize(localized_timestamp),
                msg=(
                    "The datetime should be formatted according to Django's settings for"
                    " USE_L10N is True with a different LANGUAGE_CODE."
                ),
            )

    def test_changes_display_dict_date(self):
        timestamp = datetime.datetime(2017, 1, 10, 15, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(
            dtm.history.latest().changes_display_dict["date"][1],
            dateformat.format(date, settings.DATE_FORMAT),
            msg=(
                "The date should be formatted according to Django's settings for"
                " DATE_FORMAT unless USE_L10N is True."
            ),
        )
        date = datetime.date(2017, 1, 11)
        dtm.date = date
        dtm.save()
        self.assertEqual(
            dtm.history.latest().changes_display_dict["date"][1],
            dateformat.format(date, settings.DATE_FORMAT),
            msg=(
                "The date should be formatted according to Django's settings for"
                " DATE_FORMAT unless USE_L10N is True."
            ),
        )

        # Change USE_L10N = True
        with self.settings(USE_L10N=True, LANGUAGE_CODE="en-GB"):
            self.assertEqual(
                dtm.history.latest().changes_display_dict["date"][1],
                formats.localize(date),
                msg=(
                    "The date should be formatted according to Django's settings for"
                    " USE_L10N is True with a different LANGUAGE_CODE."
                ),
            )

    def test_changes_display_dict_time(self):
        timestamp = datetime.datetime(2017, 1, 10, 15, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()
        self.assertEqual(
            dtm.history.latest().changes_display_dict["time"][1],
            dateformat.format(time, settings.TIME_FORMAT),
            msg=(
                "The time should be formatted according to Django's settings for"
                " TIME_FORMAT unless USE_L10N is True."
            ),
        )
        time = datetime.time(6, 0)
        dtm.time = time
        dtm.save()
        self.assertEqual(
            dtm.history.latest().changes_display_dict["time"][1],
            dateformat.format(time, settings.TIME_FORMAT),
            msg=(
                "The time should be formatted according to Django's settings for"
                " TIME_FORMAT unless USE_L10N is True."
            ),
        )

        # Change USE_L10N = True
        with self.settings(USE_L10N=True, LANGUAGE_CODE="en-GB"):
            self.assertEqual(
                dtm.history.latest().changes_display_dict["time"][1],
                formats.localize(time),
                msg=(
                    "The time should be formatted according to Django's settings for"
                    " USE_L10N is True with a different LANGUAGE_CODE."
                ),
            )

    def test_update_naive_dt(self):
        timestamp = datetime.datetime(2017, 1, 10, 15, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)
        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=self.now,
        )
        dtm.save()

        # Change with naive field doesnt raise error
        dtm.naive_dt = django_timezone.make_naive(
            django_timezone.now(), timezone=timezone.utc
        )
        dtm.save()

    def test_datetime_field_functions_now(self):
        timestamp = datetime.datetime(2017, 1, 10, 15, 0, tzinfo=timezone.utc)
        date = datetime.date(2017, 1, 10)
        time = datetime.time(12, 0)

        dtm = DateTimeFieldModel(
            label="DateTimeField model",
            timestamp=timestamp,
            date=date,
            time=time,
            naive_dt=Now(),
        )
        dtm.save()
        dtm.naive_dt = Now()
        self.assertEqual(dtm.naive_dt, Now())
        dtm.save()

        # Django 6.0+ evaluates expressions during save (django ticket #27222)
        if DJANGO_VERSION >= (6, 0, 0):
            with self.subTest("After save Django 6.0+"):
                self.assertIsInstance(dtm.naive_dt, datetime.datetime)
        else:
            with self.subTest("After save Django < 6.0"):
                self.assertEqual(dtm.naive_dt, Now())

    def test_json_field_value_none(self):
        json_model = NullableJSONModel(json=Value(None, JSONField()))
        json_model.save()
        self.assertEqual(json_model.history.count(), 1)
        changes_dict = json_model.history.latest().changes_dict

        # Django 6.0+ evaluates expressions during save (django ticket #27222)
        if DJANGO_VERSION >= (6, 0, 0):
            with self.subTest("Django 6.0+"):
                # Value(None) gets evaluated to "null"
                self.assertEqual(changes_dict["json"][1], "null")
        else:
            with self.subTest("Django < 6.0"):
                # Value(None) is preserved as string representation
                self.assertEqual(changes_dict["json"][1], "Value(None)")


class UnregisterTest(TestCase):
    def setUp(self):
        auditlog.unregister(SimpleModel)
        self.obj = SimpleModel.objects.create(text="No history")

    def tearDown(self):
        # Re-register for future tests
        auditlog.register(SimpleModel)

    def test_unregister_create(self):
        """Creation is not logged after unregistering."""
        # Get the object to work with
        obj = self.obj

        # Check for log entries
        self.assertEqual(obj.history.count(), 0, msg="There are no log entries")

    def test_unregister_update(self):
        """Updates are not logged after unregistering."""
        # Get the object to work with
        obj = self.obj

        # Change something
        obj.boolean = True
        obj.save()

        # Check for log entries
        self.assertEqual(obj.history.count(), 0, msg="There are no log entries")

    def test_unregister_delete(self):
        """Deletion is not logged after unregistering."""
        # Get the object to work with
        obj = self.obj

        # Delete the object
        obj.delete()

        # Check for log entries
        self.assertEqual(LogEntry.objects.count(), 0, msg="There are no log entries")

    def test_manual_logging(self):
        obj = self.obj
        obj.boolean = True
        obj.save()
        LogEntry.objects.log_create(
            instance=obj,
            action=LogEntry.Action.UPDATE,
            changes="",
        )
        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            1,
            msg="There is one log entry for 'UPDATE'",
        )


class RegisterModelSettingsTest(TestCase):
    def setUp(self):
        self.test_auditlog = AuditlogModelRegistry()

    def tearDown(self):
        for model in self.test_auditlog.get_models():
            self.test_auditlog.unregister(model)

    def test_get_model_classes(self):
        self.assertEqual(
            len(list(self.test_auditlog._get_model_classes("auditlog"))),
            len(list(apps.get_app_config("auditlog").get_models())),
        )
        self.assertEqual([], self.test_auditlog._get_model_classes("fake_model"))

    def test_get_exclude_models(self):
        # By default it returns DEFAULT_EXCLUDE_MODELS
        self.assertEqual(len(self.test_auditlog._get_exclude_models(())), 2)

        # Exclude just one model
        self.assertTrue(
            SimpleExcludeModel
            in self.test_auditlog._get_exclude_models(("test_app.SimpleExcludeModel",))
        )

        # Exclude all model of an app
        self.assertTrue(
            SimpleExcludeModel in self.test_auditlog._get_exclude_models(("test_app",))
        )

    def test_register_models_no_models(self):
        self.test_auditlog._register_models(())

        self.assertEqual(self.test_auditlog._registry, {})

    def test_register_models_register_single_model(self):
        self.test_auditlog._register_models(("test_app.SimpleExcludeModel",))

        self.assertTrue(self.test_auditlog.contains(SimpleExcludeModel))
        self.assertEqual(len(self.test_auditlog._registry), 1)

    def test_register_models_register_app(self):
        self.test_auditlog._register_models(("test_app",))

        self.assertTrue(self.test_auditlog.contains(SimpleExcludeModel))
        self.assertTrue(self.test_auditlog.contains(ChoicesFieldModel))
        self.assertEqual(len(self.test_auditlog.get_models()), 36)

    def test_register_models_register_model_with_attrs(self):
        self.test_auditlog._register_models(
            (
                {
                    "model": "test_app.SimpleExcludeModel",
                    "include_fields": ["label"],
                    "exclude_fields": [
                        "text",
                    ],
                },
            )
        )

        self.assertTrue(self.test_auditlog.contains(SimpleExcludeModel))
        fields = self.test_auditlog.get_model_fields(SimpleExcludeModel)
        self.assertEqual(fields["include_fields"], ["label"])
        self.assertEqual(fields["exclude_fields"], ["text"])

    def test_register_models_register_model_with_m2m_fields(self):
        self.test_auditlog._register_models(
            (
                {
                    "model": "test_app.ManyRelatedModel",
                    "m2m_fields": {"related"},
                },
            )
        )

        self.assertTrue(self.test_auditlog.contains(ManyRelatedModel))
        self.assertEqual(
            self.test_auditlog._registry[ManyRelatedModel]["m2m_fields"], {"related"}
        )

    def test_register_from_settings_invalid_settings(self):
        with override_settings(AUDITLOG_INCLUDE_ALL_MODELS="str"):
            with self.assertRaisesMessage(
                TypeError, "Setting 'AUDITLOG_INCLUDE_ALL_MODELS' must be a boolean"
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_EXCLUDE_TRACKING_MODELS="str"):
            with self.assertRaisesMessage(
                TypeError,
                "Setting 'AUDITLOG_EXCLUDE_TRACKING_MODELS' must be a list or tuple",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_EXCLUDE_TRACKING_MODELS=("app1.model1",)):
            with self.assertRaisesMessage(
                ValueError,
                "In order to use setting 'AUDITLOG_EXCLUDE_TRACKING_MODELS', "
                "setting 'AUDITLOG_INCLUDE_ALL_MODELS' must set to 'True'",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(
            AUDITLOG_INCLUDE_ALL_MODELS=True,
            AUDITLOG_EXCLUDE_TRACKING_FIELDS="badvalue",
        ):
            with self.assertRaisesMessage(
                TypeError,
                "Setting 'AUDITLOG_EXCLUDE_TRACKING_FIELDS' must be a list or tuple",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(
            AUDITLOG_EXCLUDE_TRACKING_FIELDS=("created", "modified")
        ):
            with self.assertRaisesMessage(
                ValueError,
                "In order to use 'AUDITLOG_EXCLUDE_TRACKING_FIELDS', "
                "setting 'AUDITLOG_INCLUDE_ALL_MODELS' must be set to 'True'",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(
            AUDITLOG_INCLUDE_ALL_MODELS=True,
            AUDITLOG_MASK_TRACKING_FIELDS="badvalue",
        ):
            with self.assertRaisesMessage(
                TypeError,
                "Setting 'AUDITLOG_MASK_TRACKING_FIELDS' must be a list or tuple",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_MASK_TRACKING_FIELDS=("token", "otp_secret")):
            with self.assertRaisesMessage(
                ValueError,
                "In order to use 'AUDITLOG_MASK_TRACKING_FIELDS', "
                "setting 'AUDITLOG_INCLUDE_ALL_MODELS' must be set to 'True'",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_INCLUDE_TRACKING_MODELS="str"):
            with self.assertRaisesMessage(
                TypeError,
                "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' must be a list or tuple",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_INCLUDE_TRACKING_MODELS=(1, 2)):
            with self.assertRaisesMessage(
                TypeError,
                "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' items must be str or dict",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_INCLUDE_TRACKING_MODELS=({"test": "test"},)):
            with self.assertRaisesMessage(
                ValueError,
                "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' dict items must contain 'model' key",
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_INCLUDE_TRACKING_MODELS=({"model": "test"},)):
            with self.assertRaisesMessage(
                ValueError,
                (
                    "Setting 'AUDITLOG_INCLUDE_TRACKING_MODELS' model must be in the "
                    "format <app_name>.<model_name>"
                ),
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(
            AUDITLOG_INCLUDE_TRACKING_MODELS=({"model": "notanapp.test"},)
        ):
            with self.assertRaisesMessage(
                AuditLogRegistrationError,
                (
                    "An error was encountered while registering model 'notanapp.test'"
                    " - make sure the app is registered correctly."
                ),
            ):
                self.test_auditlog.register_from_settings()

        with override_settings(AUDITLOG_DISABLE_ON_RAW_SAVE="bad value"):
            with self.assertRaisesMessage(
                TypeError, "Setting 'AUDITLOG_DISABLE_ON_RAW_SAVE' must be a boolean"
            ):
                self.test_auditlog.register_from_settings()

    @override_settings(
        AUDITLOG_INCLUDE_ALL_MODELS=True,
        AUDITLOG_EXCLUDE_TRACKING_MODELS=("test_app.SimpleExcludeModel",),
    )
    def test_register_from_settings_register_all_models_with_exclude_models_tuple(self):
        self.test_auditlog.register_from_settings()

        self.assertFalse(self.test_auditlog.contains(SimpleExcludeModel))
        self.assertTrue(self.test_auditlog.contains(ChoicesFieldModel))

    @override_settings(
        AUDITLOG_INCLUDE_ALL_MODELS=True,
        AUDITLOG_EXCLUDE_TRACKING_FIELDS=("datetime",),
    )
    def test_register_from_settings_register_all_models_with_exclude_tracking_fields(
        self,
    ):
        self.test_auditlog.register_from_settings()

        self.assertEqual(
            self.test_auditlog.get_model_fields(SimpleModel)["exclude_fields"],
            ["datetime"],
        )
        self.assertEqual(
            self.test_auditlog.get_model_fields(AltPrimaryKeyModel)["exclude_fields"],
            ["datetime"],
        )

    @override_settings(
        AUDITLOG_INCLUDE_ALL_MODELS=True,
        AUDITLOG_MASK_TRACKING_FIELDS=("secret",),
    )
    def test_register_from_settings_register_all_models_with_mask_tracking_fields(
        self,
    ):
        self.test_auditlog.register_from_settings()

        self.assertEqual(
            self.test_auditlog.get_model_fields(SimpleModel)["mask_fields"],
            ["secret"],
        )
        self.assertEqual(
            self.test_auditlog.get_model_fields(AltPrimaryKeyModel)["mask_fields"],
            ["secret"],
        )

    @override_settings(
        AUDITLOG_INCLUDE_ALL_MODELS=True,
        AUDITLOG_EXCLUDE_TRACKING_MODELS=["test_app.SimpleExcludeModel"],
    )
    def test_register_from_settings_register_all_models_with_exclude_models_list(self):
        self.test_auditlog.register_from_settings()

        self.assertFalse(self.test_auditlog.contains(SimpleExcludeModel))
        self.assertTrue(self.test_auditlog.contains(ChoicesFieldModel))

    @override_settings(
        AUDITLOG_INCLUDE_TRACKING_MODELS=(
            {
                "model": "test_app.SimpleExcludeModel",
                "include_fields": ["label"],
                "exclude_fields": [
                    "text",
                ],
            },
        )
    )
    def test_register_from_settings_register_models(self):
        self.test_auditlog.register_from_settings()

        self.assertTrue(self.test_auditlog.contains(SimpleExcludeModel))
        fields = self.test_auditlog.get_model_fields(SimpleExcludeModel)
        self.assertEqual(fields["include_fields"], ["label"])
        self.assertEqual(fields["exclude_fields"], ["text"])

    def test_registration_error_if_bad_serialize_params(self):
        with self.assertRaisesMessage(
            AuditLogRegistrationError,
            "Serializer options were given but the 'serialize_data' option is not "
            "set. Did you forget to set serialized_data to True?",
        ):
            register = AuditlogModelRegistry()
            register.register(
                SimpleModel, serialize_kwargs={"fields": ["text", "integer"]}
            )

    @override_settings(AUDITLOG_INCLUDE_ALL_MODELS=True)
    def test_register_from_settings_register_all_models_excluding_non_managed_models(
        self,
    ):
        self.test_auditlog.register_from_settings()

        self.assertFalse(self.test_auditlog.contains(SimpleNonManagedModel))

    @override_settings(AUDITLOG_INCLUDE_ALL_MODELS=True)
    def test_register_from_settings_register_all_models_and_figure_out_m2m_fields(self):
        self.test_auditlog.register_from_settings()

        self.assertIn(
            "related", self.test_auditlog._registry[AutoManyRelatedModel]["m2m_fields"]
        )

    @override_settings(AUDITLOG_INCLUDE_ALL_MODELS=True)
    def test_register_from_settings_register_all_models_including_auto_created_models(
        self,
    ):
        self.test_auditlog.register_from_settings()

        self.assertTrue(
            self.test_auditlog.contains(AutoManyRelatedModel.related.through)
        )


class ChoicesFieldModelTest(TestCase):
    def setUp(self):
        self.obj = ChoicesFieldModel.objects.create(
            status=ChoicesFieldModel.RED,
            multiplechoice=[
                ChoicesFieldModel.RED,
                ChoicesFieldModel.YELLOW,
                ChoicesFieldModel.GREEN,
            ],
        )

    def test_changes_display_dict_single_choice(self):
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["status"][1],
            "Red",
            msg="The human readable text 'Red' is displayed.",
        )
        self.obj.status = ChoicesFieldModel.GREEN
        self.obj.save()
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["status"][1],
            "Green",
            msg="The human readable text 'Green' is displayed.",
        )

    def test_changes_display_dict_multiplechoice(self):
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["multiplechoice"][1],
            "Red, Yellow, Green",
            msg="The human readable text 'Red, Yellow, Green' is displayed.",
        )
        self.obj.multiplechoice = ChoicesFieldModel.RED
        self.obj.save()
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["multiplechoice"][1],
            "Red",
            msg="The human readable text 'Red' is displayed.",
        )

    def test_changes_display_dict_many_to_one_relation(self):
        obj = SimpleModel()
        obj.save()
        history = obj.history.get()
        assert "related_models" in history.changes_display_dict


class CharFieldTextFieldModelTest(TestCase):
    def setUp(self):
        self.PLACEHOLDER_LONGCHAR = "s" * 255
        self.PLACEHOLDER_LONGTEXTFIELD = "s" * 1000
        self.obj = CharfieldTextfieldModel.objects.create(
            longchar=self.PLACEHOLDER_LONGCHAR,
            longtextfield=self.PLACEHOLDER_LONGTEXTFIELD,
        )

    def test_changes_display_dict_longchar(self):
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["longchar"][1],
            f"{self.PLACEHOLDER_LONGCHAR[:140]}...",
            msg="The string should be truncated at 140 characters with an ellipsis at the end.",
        )
        SHORTENED_PLACEHOLDER = self.PLACEHOLDER_LONGCHAR[:139]
        self.obj.longchar = SHORTENED_PLACEHOLDER
        self.obj.save()
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["longchar"][1],
            SHORTENED_PLACEHOLDER,
            msg="The field should display the entire string because it is less than 140 characters",
        )

    def test_changes_display_dict_longtextfield(self):
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["longtextfield"][1],
            f"{self.PLACEHOLDER_LONGTEXTFIELD[:140]}...",
            msg="The string should be truncated at 140 characters with an ellipsis at the end.",
        )
        SHORTENED_PLACEHOLDER = self.PLACEHOLDER_LONGTEXTFIELD[:139]
        self.obj.longtextfield = SHORTENED_PLACEHOLDER
        self.obj.save()
        self.assertEqual(
            self.obj.history.latest().changes_display_dict["longtextfield"][1],
            SHORTENED_PLACEHOLDER,
            msg="The field should display the entire string because it is less than 140 characters",
        )

    def test_changes_display_dict_longtextfield_to_be_truncated_at_custom_length(self):
        with override_settings(AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH=10):
            length = settings.AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH
            self.assertEqual(
                self.obj.history.latest().changes_display_dict["longtextfield"][1],
                f"{self.PLACEHOLDER_LONGCHAR[:length]}...",
                msg=f"The string should be truncated at {length} characters with an ellipsis at the end.",
            )

    def test_changes_display_dict_longtextfield_to_be_truncated_to_empty_string(self):
        with override_settings(AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH=0):
            length = settings.AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH
            self.assertEqual(
                self.obj.history.latest().changes_display_dict["longtextfield"][1],
                "",
                msg=f"The string should be empty as AUDITLOG_TRUNCATE_CHANGES_DISPLAY is set to {length}.",
            )

    def test_changes_display_dict_longtextfield_with_truncation_disabled(self):
        with override_settings(AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH=-1):
            length = settings.AUDITLOG_CHANGE_DISPLAY_TRUNCATE_LENGTH
            self.assertEqual(
                self.obj.history.latest().changes_display_dict["longtextfield"][1],
                self.PLACEHOLDER_LONGTEXTFIELD,
                msg=(
                    "The field should display the entire string "
                    f"even though it is longer than {length} characters"
                    "as AUDITLOG_TRUNCATE_CHANGES_DISPLAY is set to a negative number"
                ),
            )


class AdminPanelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="test_admin", is_staff=True, is_superuser=True, is_active=True
        )
        self.site = AdminSite()
        self.admin = LogEntryAdmin(LogEntry, self.site)
        self.admin_path_prefix = (
            f"admin/{LogEntry._meta.app_label}/{LogEntry._meta.model_name}"
        )
        with freezegun.freeze_time("2022-08-01 12:00:00Z"):
            self.obj = SimpleModel.objects.create(text="For admin logentry test")

    def test_auditlog_admin(self):
        self.client.force_login(self.user)
        log_pk = self.obj.history.latest().pk
        res = self.client.get(f"/{self.admin_path_prefix}/")
        self.assertEqual(res.status_code, 200)
        res = self.client.get(f"/{self.admin_path_prefix}/add/")
        self.assertEqual(res.status_code, 403)
        res = self.client.get(f"/{self.admin_path_prefix}/{log_pk}/", follow=True)
        self.assertEqual(res.status_code, 200)
        res = self.client.get(f"/{self.admin_path_prefix}/{log_pk}/delete/")
        self.assertEqual(res.status_code, 403)
        res = self.client.get(f"/{self.admin_path_prefix}/{log_pk}/history/")
        self.assertEqual(res.status_code, 200)

    def test_created_timezone(self):
        log_entry = self.obj.history.latest()

        for tz, timestamp in [
            ("UTC", "2022-08-01 12:00:00"),
            ("Asia/Tbilisi", "2022-08-01 16:00:00"),
            ("America/Argentina/Buenos_Aires", "2022-08-01 09:00:00"),
            ("Asia/Kathmandu", "2022-08-01 17:45:00"),
        ]:
            with self.settings(TIME_ZONE=tz):
                created = self.admin.created(log_entry)
                self.assertEqual(created.strftime("%Y-%m-%d %H:%M:%S"), timestamp)

    @freezegun.freeze_time("2022-08-01 12:00:00Z")
    def test_created_naive_datetime(self):
        with self.settings(USE_TZ=False):
            obj = SimpleModel.objects.create(text="For USE_TZ=False test")
            log_entry = obj.history.latest()
            created = self.admin.created(log_entry)
            self.assertEqual(
                created.strftime("%Y-%m-%d %H:%M:%S"),
                "2022-08-01 12:00:00",
            )

    def test_cid(self):
        self.client.force_login(self.user)
        expected_response = (
            f'<a href="/{self.admin_path_prefix}/?cid=123" '
            'title="Click to filter by records with this correlation id">123</a>'
        )

        log_entry = self.obj.history.latest()
        log_entry.cid = "123"
        log_entry.save()

        res = self.client.get(f"/{self.admin_path_prefix}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(expected_response, res.rendered_content)

    def test_has_delete_permission(self):
        log = self.obj.history.latest()
        obj_pk = self.obj.pk
        delete_log_request = RequestFactory().post(
            f"/{self.admin_path_prefix}/{log.pk}/delete/"
        )
        delete_log_request.resolver_match = resolve(delete_log_request.path)
        delete_log_request.user = self.user
        delete_object_request = RequestFactory().post(
            f"/admin/tests/simplemodel/{obj_pk}/delete/"
        )
        delete_object_request.resolver_match = resolve(delete_object_request.path)
        delete_object_request.user = self.user

        self.assertTrue(self.admin.has_delete_permission(delete_object_request, log))
        self.assertFalse(self.admin.has_delete_permission(delete_log_request, log))


class DiffMsgTest(TestCase):
    def setUp(self):
        super().setUp()
        self.site = AdminSite()
        self.admin = LogEntryAdmin(LogEntry, self.site)

    def _create_log_entry(self, action, changes):
        return LogEntry.objects.log_create(
            SimpleModel.objects.create(),  # doesn't affect anything
            action=action,
            changes=changes,
        )

    def test_change_msg_create_when_exceeds_max_len(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.CREATE,
            {
                "Camelopardalis": [None, "Giraffe"],
                "Capricornus": [None, "Sea goat"],
                "Equuleus": [None, "Little horse"],
                "Horologium": [None, "Clock"],
                "Microscopium": [None, "Microscope"],
                "Reticulum": [None, "Net"],
                "Telescopium": [None, "Telescope"],
            },
        )

        self.assertEqual(
            self.admin.msg_short(log_entry),
            "7 changes: Camelopardalis, Capricornus, Equuleus, Horologium, "
            "Microscopium, ..",
        )

    def test_changes_msg_delete(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.DELETE,
            {"field one": ["value before deletion", None], "field two": [11, None]},
        )

        self.assertEqual(self.admin.msg_short(log_entry), "")
        self.assertEqual(
            self.admin.msg(log_entry),
            (
                "<table>"
                "<tr><th>#</th><th>Field</th><th>From</th><th>To</th></tr>"
                "<tr><td>1</td><td>Field one</td><td>value before deletion</td><td>None</td></tr>"
                "<tr><td>2</td><td>Field two</td><td>11</td><td>None</td></tr>"
                "</table>"
            ),
        )

    def test_instance_translation_and_history_logging(self):
        first = SimpleModel()
        second = SimpleModel(text=_("test"))
        changes = model_instance_diff(first, second)
        self.assertEqual(changes, {"text": ("", "test")})
        second.save()
        log_one = second.history.last()
        self.assertTrue(isinstance(log_one, LogEntry))

    def test_changes_msg_create(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.CREATE,
            {
                "field two": [None, 11],
                "field one": [None, "a value"],
            },
        )

        self.assertEqual(
            self.admin.msg_short(log_entry), "2 changes: field two, field one"
        )
        self.assertEqual(
            self.admin.msg(log_entry),
            (
                "<table>"
                "<tr><th>#</th><th>Field</th><th>From</th><th>To</th></tr>"
                "<tr><td>1</td><td>Field one</td><td>None</td><td>a value</td></tr>"
                "<tr><td>2</td><td>Field two</td><td>None</td><td>11</td></tr>"
                "</table>"
            ),
        )

    def test_changes_msg_update(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.UPDATE,
            {
                "field two": [11, 42],
                "field one": ["old value of field one", "new value of field one"],
            },
        )

        self.assertEqual(
            self.admin.msg_short(log_entry), "2 changes: field two, field one"
        )
        self.assertEqual(
            self.admin.msg(log_entry),
            (
                "<table>"
                "<tr><th>#</th><th>Field</th><th>From</th><th>To</th></tr>"
                "<tr><td>1</td><td>Field one</td><td>old value of field one</td>"
                "<td>new value of field one</td></tr>"
                "<tr><td>2</td><td>Field two</td><td>11</td><td>42</td></tr>"
                "</table>"
            ),
        )

    def test_changes_msg_m2m(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.UPDATE,
            {  # mimicking the format used by log_m2m_changes
                "some_m2m_field": {
                    "type": "m2m",
                    "operation": "add",
                    "objects": ["Example User (user 1)", "Illustration (user 42)"],
                },
            },
        )

        self.assertEqual(self.admin.msg_short(log_entry), "1 change: some_m2m_field")
        self.assertEqual(
            self.admin.msg(log_entry),
            (
                "<table>"
                "<tr><th>#</th><th>Relationship</th><th>Action</th><th>Objects</th></tr>"
                "<tr><td>1</td><td>Some m2m field</td><td>add</td><td>Example User (user 1)"
                "<br>Illustration (user 42)</td></tr>"
                "</table>"
            ),
        )

    def test_unregister_after_log(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.CREATE,
            {
                "field two": [None, 11],
                "field one": [None, "a value"],
            },
        )
        # Unregister
        auditlog.unregister(SimpleModel)
        self.assertEqual(
            self.admin.msg_short(log_entry), "2 changes: field two, field one"
        )
        self.assertEqual(
            self.admin.msg(log_entry),
            (
                "<table>"
                "<tr><th>#</th><th>Field</th><th>From</th><th>To</th></tr>"
                "<tr><td>1</td><td>Field one</td><td>None</td><td>a value</td></tr>"
                "<tr><td>2</td><td>Field two</td><td>None</td><td>11</td></tr>"
                "</table>"
            ),
        )
        # Re-register
        auditlog.register(SimpleModel)

    def test_field_verbose_name(self):
        log_entry = self._create_log_entry(
            LogEntry.Action.CREATE,
            {"test": "test"},
        )

        self.assertEqual(self.admin.field_verbose_name(log_entry, "actor"), "Actor")
        with patch(
            "django.contrib.contenttypes.models.ContentType.model_class",
            return_value=None,
        ):
            self.assertEqual(self.admin.field_verbose_name(log_entry, "actor"), "actor")


class NoDeleteHistoryTest(TestCase):
    def test_delete_related(self):
        instance = SimpleModel.objects.create(integer=1)
        assert LogEntry.objects.all().count() == 1
        instance.integer = 2
        instance.save()
        assert LogEntry.objects.all().count() == 2

        instance.delete()
        entries = LogEntry.objects.order_by("id")

        # The "DELETE" record is always retained
        assert LogEntry.objects.all().count() == 1
        assert entries.first().action == LogEntry.Action.DELETE

    def test_no_delete_related(self):
        instance = NoDeleteHistoryModel.objects.create(integer=1)
        self.assertEqual(LogEntry.objects.all().count(), 1)
        instance.integer = 2
        instance.save()
        self.assertEqual(LogEntry.objects.all().count(), 2)

        instance.delete()
        entries = LogEntry.objects.order_by("id")
        self.assertEqual(entries.count(), 3)
        self.assertEqual(
            list(entries.values_list("action", flat=True)),
            [LogEntry.Action.CREATE, LogEntry.Action.UPDATE, LogEntry.Action.DELETE],
        )


class JSONModelTest(TestCase):
    def setUp(self):
        self.obj = JSONModel.objects.create()

    def test_update(self):
        """Changes on a JSONField are logged correctly."""
        # Get the object to work with
        obj = self.obj

        # Change something
        obj.json = {
            "quantity": "1",
        }
        obj.save()

        # Check for log entries
        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            1,
            msg="There is one log entry for 'UPDATE'",
        )

        history = obj.history.get(action=LogEntry.Action.UPDATE)

        self.assertDictEqual(
            history.changes,
            {"json": ["{}", '{"quantity": "1"}']},
            msg="The change is correctly logged",
        )
        self.assertEqual(
            history.changes_str,
            'json: {} → {"quantity": "1"}',
            msg="Changes string is correct",
        )

    def test_update_with_no_changes(self):
        """No changes are logged."""
        first_json = {
            "quantity": "1814",
            "tax_rate": "17",
            "unit_price": "144",
            "description": "Method form.",
            "discount_rate": "42",
            "unit_of_measure": "bytes",
        }
        obj = JSONModel.objects.create(json=first_json)

        # Change the order of the keys but not the values
        second_json = {
            "tax_rate": "17",
            "description": "Method form.",
            "quantity": "1814",
            "unit_of_measure": "bytes",
            "unit_price": "144",
            "discount_rate": "42",
        }
        obj.json = second_json
        obj.save()

        # Check for log entries
        self.assertEqual(
            first_json,
            second_json,
            msg="dicts are the same",
        )
        self.assertEqual(
            obj.history.filter(action=LogEntry.Action.UPDATE).count(),
            0,
            msg="There is no log entry",
        )


class ModelInstanceDiffTest(TestCase):
    def test_diff_models_with_related_fields(self):
        """No error is raised when comparing models with related fields."""

        # This tests that track_field() does indeed ignore related fields.

        # a model without reverse relations
        simple1 = SimpleModel()
        simple1.save()

        # a model with reverse relations
        simple2 = SimpleModel()
        simple2.save()
        related = RelatedModel(related=simple2, one_to_one=simple2)
        related.save()

        # Demonstrate that simple1 can have DoesNotExist on reverse
        # OneToOne relation.
        with self.assertRaises(
            SimpleModel.reverse_one_to_one.RelatedObjectDoesNotExist
        ):
            simple1.reverse_one_to_one  # equals None

        # accessing relatedmodel_set won't trigger DoesNotExist.
        self.assertEqual(simple1.related_models.count(), 0)

        # simple2 DOES have these relations
        self.assertEqual(simple2.reverse_one_to_one, related)
        self.assertEqual(simple2.related_models.count(), 1)

        model_instance_diff(simple2, simple1)
        model_instance_diff(simple1, simple2)

    def test_diff_polymorphic_models(self):
        """No error is raised when comparing parent/child for polymorphic models."""

        # This tests that when a polymorphic model is compared to its parent,
        # no FieldDoesNotExist errors are raised because those fields don't exist
        # on the parent model.

        # relation target
        simple = SimpleModel()
        simple.save()

        # the parent model
        related_parent = RelatedModelParent()
        related_parent.save()

        # the child model, with some fields that don't exist on the parent
        related = RelatedModel(related=simple, one_to_one=simple)
        related.save()

        model_instance_diff(related, related_parent)

    def test_object_repr_related_deleted(self):
        """No error is raised when __str__() loads a related object that has been deleted."""
        simple = SimpleModel()
        simple.save()
        related = RelatedModel(related=simple, one_to_one=simple)
        related.save()
        related_id = related.id

        related.refresh_from_db()
        simple.delete()
        related.delete()

        log_entry = (
            LogEntry.objects.get_for_model(RelatedModel)
            .filter(object_id=related_id)
            .get(action=LogEntry.Action.DELETE)
        )
        self.assertEqual(log_entry.object_repr, DEFAULT_OBJECT_REPR)

    def test_when_field_doesnt_exist(self):
        """No error is raised and the default is returned."""
        first = SimpleModel(boolean=True)
        second = SimpleModel()

        # then boolean should be False, as we use the default value
        # specified inside the model
        del second.boolean

        changes = model_instance_diff(first, second)

        # Check for log entries
        self.assertEqual(
            changes,
            {"boolean": ("True", "False")},
            msg="ObjectDoesNotExist should be handled",
        )

    def test_field_with_no_default_provided(self):
        """Field with no default (NOT_PROVIDED) should return None."""
        first = SimpleModel(integer=1)
        second = SimpleModel()

        delattr(second, "integer")

        changes = model_instance_diff(first, second)
        self.assertEqual(
            changes,
            {"integer": ("1", "None")},
            msg="field with no default should return None",
        )

    def test_field_with_callable_default(self):
        first = SimpleModel(char="value")
        second = SimpleModel()

        delattr(second, "char")

        changes = model_instance_diff(first, second)
        self.assertEqual(
            changes,
            {"char": ("value", "default value")},
            msg="callable default should be handled",
        )

    def test_diff_models_with_json_fields(self):
        first = JSONModel.objects.create(
            json={
                "code": "17",
                "date": datetime.date(2022, 1, 1),
                "description": "first",
            }
        )
        first.refresh_from_db()  # refresh json data from db
        second = JSONModel.objects.create(
            json={
                "code": "17",
                "description": "second",
                "date": datetime.date(2023, 1, 1),
            }
        )
        diff = model_instance_diff(first, second, ["json"])

        self.assertDictEqual(
            diff,
            {
                "json": (
                    '{"code": "17", "date": "2022-01-01", "description": "first"}',
                    '{"code": "17", "date": "2023-01-01", "description": "second"}',
                )
            },
        )


class TestRelatedDiffs(TestCase):
    def setUp(self):
        self.test_date = datetime.datetime(2022, 1, 1, 12, tzinfo=datetime.timezone.utc)

    def test_log_entry_changes_on_fk_object_update(self):
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create()
            one_simple = SimpleModel.objects.create()
            two_simple = SimpleModel.objects.create()
            instance = RelatedModel.objects.create(
                one_to_one=simple, related=one_simple
            )

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            instance.related = two_simple
            instance.save()

        log_one = instance.history.filter(timestamp=t1).first()
        log_two = instance.history.filter(timestamp=t2).first()
        self.assertTrue(isinstance(log_one, LogEntry))
        self.assertTrue(isinstance(log_two, LogEntry))

        self.assertEqual(int(log_one.changes_dict["related"][1]), one_simple.id)
        self.assertEqual(int(log_one.changes_dict["one_to_one"][1]), simple.id)
        self.assertEqual(int(log_two.changes_dict["related"][1]), two_simple.id)

    def test_log_entry_changes_on_fk_object_id_update(self):
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create()
            one_simple = SimpleModel.objects.create()
            two_simple = SimpleModel.objects.create()
            instance = RelatedModel.objects.create(
                one_to_one=simple, related=one_simple
            )

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            instance.related_id = two_simple.id
            instance.one_to_one = one_simple
            instance.save(update_fields=["related_id", "one_to_one_id"])

        log_one = instance.history.filter(timestamp=t1).first()
        log_two = instance.history.filter(timestamp=t2).first()
        self.assertTrue(isinstance(log_one, LogEntry))
        self.assertTrue(isinstance(log_two, LogEntry))

        self.assertEqual(int(log_one.changes_dict["related"][1]), one_simple.id)
        self.assertEqual(int(log_one.changes_dict["one_to_one"][1]), simple.id)
        self.assertEqual(int(log_two.changes_dict["related"][1]), two_simple.id)
        self.assertEqual(int(log_two.changes_dict["one_to_one"][1]), one_simple.id)

    def test_log_entry_changes_on_fk_id_update(self):
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create()
            one_simple = SimpleModel.objects.create()
            two_simple = SimpleModel.objects.create()
            instance = RelatedModel.objects.create(
                one_to_one_id=int(simple.id), related_id=int(one_simple.id)
            )

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            instance.related_id = int(two_simple.id)
            instance.save()

        log_one = instance.history.filter(timestamp=t1).first()
        log_two = instance.history.filter(timestamp=t2).first()
        self.assertTrue(isinstance(log_one, LogEntry))
        self.assertTrue(isinstance(log_two, LogEntry))

        self.assertEqual(int(log_one.changes_dict["related"][1]), one_simple.id)
        self.assertEqual(int(log_one.changes_dict["one_to_one"][1]), simple.id)
        self.assertEqual(int(log_two.changes_dict["related"][1]), two_simple.id)

    def test_log_entry_create_fk_changes_to_string_objects_in_display_dict(self):
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create(text="Test Foo")
            one_simple = SimpleModel.objects.create(text="Test Bar")
            instance = RelatedModel.objects.create(
                one_to_one=simple, related=one_simple
            )

        log_one = instance.history.filter(timestamp=t1).first()
        self.assertTrue(isinstance(log_one, LogEntry))
        display_dict = log_one.changes_display_dict
        self.assertEqual(display_dict["related"][1], "Test Bar")
        self.assertEqual(display_dict["related"][0], "None")
        self.assertEqual(display_dict["one to one"][1], "Test Foo")

    def test_log_entry_deleted_fk_changes_to_string_objects_in_display_dict(self):
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create(text="Test Foo")
            one_simple = SimpleModel.objects.create(text="Test Bar")
            one_simple_id = int(one_simple.id)
            instance = RelatedModel.objects.create(
                one_to_one=simple, related=one_simple
            )

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            one_simple.delete()

        log_two = instance.history.filter(timestamp=t2).first()
        self.assertTrue(isinstance(log_two, LogEntry))
        display_dict = log_two.changes_display_dict
        self.assertEqual(
            display_dict["related"][0], f"Deleted 'SimpleModel' ({one_simple_id})"
        )
        self.assertEqual(display_dict["related"][1], "None")

    def test_no_log_entry_created_on_related_object_string_update(self):
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create(text="Test Foo")
            one_simple = SimpleModel.objects.create(text="Test Bar")
            instance = RelatedModel.objects.create(
                one_to_one=simple, related=one_simple
            )

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            # Order is important. Without special FK handling, the arbitrary in memory
            # changes to the (same) related object's signature result in a perceived
            # update where no update has occurred.
            one_simple.text = "Test Baz"
            instance.save()
            one_simple.save()

        # Assert that only one log for the instance was created
        self.assertEqual(instance.history.all().count(), 1)
        # Assert that two logs were created for the parent object
        self.assertEqual(one_simple.history.all().count(), 2)

    def test_log_entry_created_if_obj_strings_are_same_for_two_objs(self):
        """FK changes trigger update when the string representation is the same."""
        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create(text="Test Foo")
            one_simple = SimpleModel.objects.create(text="Twinsies", boolean=True)
            two_simple = SimpleModel.objects.create(text="Twinsies", boolean=False)
            instance = RelatedModel.objects.create(
                one_to_one=simple, related=one_simple
            )

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            instance.related = two_simple
            instance.save()

        self.assertEqual(instance.history.all().count(), 2)
        log_create = instance.history.filter(timestamp=t1).first()
        log_update = instance.history.filter(timestamp=t2).first()
        self.assertEqual(int(log_create.changes_dict["related"][1]), one_simple.id)
        self.assertEqual(int(log_update.changes_dict["related"][1]), two_simple.id)

    @override_settings(AUDITLOG_USE_FK_STRING_REPRESENTATION=True)
    def test_string_representation_of_fk_changes(self):
        """FK changes should be stored using string representation when setting is enabled"""

        t1 = self.test_date
        with freezegun.freeze_time(t1):
            simple = SimpleModel.objects.create(text="Test Foo")
            two_simple = SimpleModel.objects.create(text="Test Bar")
            instance = RelatedModel.objects.create(one_to_one=simple, related=simple)

        t2 = self.test_date + datetime.timedelta(days=20)
        with freezegun.freeze_time(t2):
            instance.one_to_one = two_simple
            instance.related = two_simple
            instance.save()

        self.assertEqual(instance.history.all().count(), 2)
        log_update = instance.history.filter(timestamp=t2).first()
        self.assertEqual(log_update.changes_dict["related"][0], "Test Foo")
        self.assertEqual(log_update.changes_dict["related"][1], "Test Bar")
        self.assertEqual(log_update.changes_dict["one_to_one"][0], "Test Foo")
        self.assertEqual(log_update.changes_dict["one_to_one"][1], "Test Bar")


class TestModelSerialization(TestCase):
    def setUp(self):
        super().setUp()
        self.test_date = datetime.datetime(2022, 1, 1, 12, tzinfo=timezone.utc)
        self.test_date_string = datetime.datetime.strftime(
            self.test_date, "%Y-%m-%dT%XZ"
        )

    def test_does_not_serialize_data_when_not_configured(self):
        instance = SimpleModel.objects.create(
            text="sample text here", boolean=True, integer=4
        )

        log = instance.history.first()
        self.assertIsNone(log.serialized_data)

    def test_serializes_data_on_create(self):
        with freezegun.freeze_time(self.test_date):
            instance = SerializeThisModel.objects.create(
                label="test label",
                timestamp=self.test_date,
                nullable=4,
                nested={"foo": True, "bar": False},
            )

        log = instance.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 0)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "label": "test label",
                "timestamp": self.test_date_string,
                "nullable": 4,
                "nested": {"foo": True, "bar": False},
                "mask_me": None,
                "date": None,
                "code": None,
            },
        )

    def test_serializes_data_on_update(self):
        with freezegun.freeze_time(self.test_date):
            instance = SerializeThisModel.objects.create(
                label="test label",
                timestamp=self.test_date,
                nullable=4,
                nested={"foo": True, "bar": False},
            )

        update_date = self.test_date + datetime.timedelta(days=4)
        with freezegun.freeze_time(update_date):
            instance.label = "test label change"
            instance.save()

        log = instance.history.filter(timestamp=update_date).first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 1)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "label": "test label change",
                "timestamp": self.test_date_string,
                "nullable": 4,
                "nested": {"foo": True, "bar": False},
                "mask_me": None,
                "date": None,
                "code": None,
            },
        )

    def test_serializes_data_on_delete(self):
        with freezegun.freeze_time(self.test_date):
            instance = SerializeThisModel.objects.create(
                label="test label",
                timestamp=self.test_date,
                nullable=4,
                nested={"foo": True, "bar": False},
            )

        obj_id = int(instance.id)
        delete_date = self.test_date + datetime.timedelta(days=4)
        with freezegun.freeze_time(delete_date):
            instance.delete()

        log = LogEntry.objects.filter(object_id=obj_id, timestamp=delete_date).first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 2)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "label": "test label",
                "timestamp": self.test_date_string,
                "nullable": 4,
                "nested": {"foo": True, "bar": False},
                "mask_me": None,
                "date": None,
                "code": None,
            },
        )

    def test_serialize_string_representations(self):
        with freezegun.freeze_time(self.test_date):
            instance = SerializeThisModel.objects.create(
                label="test label",
                nullable=4,
                nested={"foo": 10, "bar": False},
                timestamp="2022-03-01T12:00Z",
                date="2022-04-05",
                code="e82d5e53-ca80-4037-af55-b90752326460",
            )

        log = instance.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 0)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "label": "test label",
                "timestamp": "2022-03-01T12:00:00Z",
                "date": "2022-04-05",
                "code": "e82d5e53-ca80-4037-af55-b90752326460",
                "nullable": 4,
                "nested": {"foo": 10, "bar": False},
                "mask_me": None,
            },
        )

    def test_serialize_mask_fields(self):
        with freezegun.freeze_time(self.test_date):
            instance = SerializeThisModel.objects.create(
                label="test label",
                nullable=4,
                timestamp=self.test_date,
                nested={"foo": 10, "bar": False},
                mask_me="confidential",
            )

        log = instance.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 0)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "label": "test label",
                "timestamp": self.test_date_string,
                "nullable": 4,
                "nested": {"foo": 10, "bar": False},
                "mask_me": "******ential",
                "date": None,
                "code": None,
            },
        )

    def test_serialize_only_auditlog_fields(self):
        with freezegun.freeze_time(self.test_date):
            instance = SerializeOnlySomeOfThisModel.objects.create(
                this="this should be there", not_this="leave this bit out"
            )

        log = instance.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 0)
        self.assertDictEqual(
            log.serialized_data["fields"], {"this": "this should be there"}
        )
        self.assertDictEqual(
            log.changes_dict,
            {"this": ["None", "this should be there"], "id": ["None", "1"]},
        )

    def test_serialize_related(self):
        with freezegun.freeze_time(self.test_date):
            serialize_this = SerializeThisModel.objects.create(
                label="test label",
                nested={"foo": "bar"},
                timestamp=self.test_date,
            )
            instance = SerializePrimaryKeyRelatedModel.objects.create(
                serialize_this=serialize_this,
                subheading="use a primary key for this serialization, please.",
                value=10,
            )

        log = instance.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 0)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "serialize_this": serialize_this.id,
                "subheading": "use a primary key for this serialization, please.",
                "value": 10,
            },
        )

    def test_serialize_related_with_kwargs(self):
        with freezegun.freeze_time(self.test_date):
            serialize_this = SerializeThisModel.objects.create(
                label="test label",
                nested={"foo": "bar"},
                timestamp=self.test_date,
            )
            instance = SerializeNaturalKeyRelatedModel.objects.create(
                serialize_this=serialize_this,
                subheading="use a natural key for this serialization, please.",
                value=11,
            )

        log = instance.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 0)
        self.assertDictEqual(
            log.serialized_data["fields"],
            {
                "serialize_this": "test label",
                "subheading": "use a natural key for this serialization, please.",
                "value": 11,
            },
        )

    def test_f_expressions(self):
        serialize_this = SerializeThisModel.objects.create(
            label="test label",
            nested={"foo": "bar"},
            timestamp=self.test_date,
            nullable=1,
        )
        serialize_this.nullable = models.F("nullable") + 1
        serialize_this.save()

        log = serialize_this.history.first()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, 1)
        self.assertEqual(
            log.serialized_data["fields"]["nullable"],
            "F(nullable) + Value(1)",
        )


class TestAccessLog(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test_user", is_active=True)
        self.obj = SimpleModel.objects.create(text="For admin logentry test")

    def test_access_log(self):
        self.client.force_login(self.user)
        content_type = ContentType.objects.get_for_model(self.obj.__class__)

        # Check for log entries
        qs = LogEntry.objects.filter(content_type=content_type, object_pk=self.obj.pk)
        old_count = qs.count()

        self.client.get(reverse("simplemodel-detail", args=[self.obj.pk]))
        new_count = qs.count()
        self.assertEqual(new_count, old_count + 1)

        log_entry = qs.latest()
        self.assertEqual(int(log_entry.object_pk), self.obj.pk)
        self.assertEqual(log_entry.actor, self.user)
        self.assertEqual(log_entry.content_type, content_type)
        self.assertEqual(
            log_entry.action, LogEntry.Action.ACCESS, msg="Action is 'ACCESS'"
        )
        self.assertIsNone(log_entry.changes)
        self.assertEqual(log_entry.changes_dict, {})
        self.assertEqual(log_entry.changes_str, "")


class SignalTests(TestCase):
    def setUp(self):
        self.obj = SimpleModel.objects.create(text="I am not difficult.")
        self.my_pre_log_data = {
            "is_called": False,
            "my_sender": None,
            "my_instance": None,
            "my_action": None,
        }
        self.my_post_log_data = {
            "is_called": False,
            "my_sender": None,
            "my_instance": None,
            "my_action": None,
            "my_error": None,
            "my_log_entry": None,
        }

    def assertSignals(self, action):
        self.assertTrue(
            self.my_pre_log_data["is_called"], "pre_log hook receiver not called"
        )
        self.assertIs(self.my_pre_log_data["my_sender"], self.obj.__class__)
        self.assertIs(self.my_pre_log_data["my_instance"], self.obj)
        self.assertEqual(self.my_pre_log_data["my_action"], action)

        self.assertTrue(
            self.my_post_log_data["is_called"], "post_log hook receiver not called"
        )
        self.assertIs(self.my_post_log_data["my_sender"], self.obj.__class__)
        self.assertIs(self.my_post_log_data["my_instance"], self.obj)
        self.assertEqual(self.my_post_log_data["my_action"], action)
        self.assertIsNone(self.my_post_log_data["my_error"])
        self.assertIsNotNone(self.my_post_log_data["my_log_entry"])

    def test_custom_signals(self):
        my_ret_val = random.randint(0, 10000)
        my_other_ret_val = random.randint(0, 10000)

        def pre_log_receiver(sender, instance, action, **_kwargs):
            self.my_pre_log_data["is_called"] = True
            self.my_pre_log_data["my_sender"] = sender
            self.my_pre_log_data["my_instance"] = instance
            self.my_pre_log_data["my_action"] = action
            return my_ret_val

        def pre_log_receiver_extra(*_args, **_kwargs):
            return my_other_ret_val

        def post_log_receiver(
            sender, instance, action, error, log_entry, pre_log_results, **_kwargs
        ):
            self.my_post_log_data["is_called"] = True
            self.my_post_log_data["my_sender"] = sender
            self.my_post_log_data["my_instance"] = instance
            self.my_post_log_data["my_action"] = action
            self.my_post_log_data["my_error"] = error
            self.my_post_log_data["my_log_entry"] = log_entry

            self.assertEqual(len(pre_log_results), 2)

            found_first_result = False
            found_second_result = False
            for pre_log_fn, pre_log_result in pre_log_results:
                if pre_log_fn is pre_log_receiver and pre_log_result == my_ret_val:
                    found_first_result = True
            for pre_log_fn, pre_log_result in pre_log_results:
                if (
                    pre_log_fn is pre_log_receiver_extra
                    and pre_log_result == my_other_ret_val
                ):
                    found_second_result = True

            self.assertTrue(found_first_result)
            self.assertTrue(found_second_result)

            return my_ret_val

        pre_log.connect(pre_log_receiver)
        pre_log.connect(pre_log_receiver_extra)
        post_log.connect(post_log_receiver)

        self.obj = SimpleModel.objects.create(text="I am not difficult.")

        self.assertSignals(LogEntry.Action.CREATE)

    def test_disabled_logging(self):
        log_count = LogEntry.objects.count()

        def pre_log_receiver(sender, instance, action, **_kwargs):
            return True

        def pre_log_receiver_extra(*_args, **_kwargs):
            pass

        def pre_log_receiver_disable(*_args, **_kwargs):
            return False

        pre_log.connect(pre_log_receiver)
        pre_log.connect(pre_log_receiver_extra)

        self.obj = SimpleModel.objects.create(text="I am not difficult.")

        self.assertEqual(LogEntry.objects.count(), log_count + 1)

        log_count = LogEntry.objects.count()

        pre_log.connect(pre_log_receiver_disable)

        self.obj = SimpleModel.objects.create(text="I am not difficult.")

        self.assertEqual(LogEntry.objects.count(), log_count)

    def test_custom_signals_update(self):
        def pre_log_receiver(sender, instance, action, **_kwargs):
            self.my_pre_log_data["is_called"] = True
            self.my_pre_log_data["my_sender"] = sender
            self.my_pre_log_data["my_instance"] = instance
            self.my_pre_log_data["my_action"] = action

        def post_log_receiver(sender, instance, action, error, log_entry, **_kwargs):
            self.my_post_log_data["is_called"] = True
            self.my_post_log_data["my_sender"] = sender
            self.my_post_log_data["my_instance"] = instance
            self.my_post_log_data["my_action"] = action
            self.my_post_log_data["my_error"] = error
            self.my_post_log_data["my_log_entry"] = log_entry

        pre_log.connect(pre_log_receiver)
        post_log.connect(post_log_receiver)

        self.obj.text = "Changed Text"
        self.obj.save()

        self.assertSignals(LogEntry.Action.UPDATE)

    def test_custom_signals_delete(self):
        def pre_log_receiver(sender, instance, action, **_kwargs):
            self.my_pre_log_data["is_called"] = True
            self.my_pre_log_data["my_sender"] = sender
            self.my_pre_log_data["my_instance"] = instance
            self.my_pre_log_data["my_action"] = action

        def post_log_receiver(sender, instance, action, error, log_entry, **_kwargs):
            self.my_post_log_data["is_called"] = True
            self.my_post_log_data["my_sender"] = sender
            self.my_post_log_data["my_instance"] = instance
            self.my_post_log_data["my_action"] = action
            self.my_post_log_data["my_error"] = error
            self.my_post_log_data["my_log_entry"] = log_entry

        pre_log.connect(pre_log_receiver)
        post_log.connect(post_log_receiver)

        self.obj.delete()

        self.assertSignals(LogEntry.Action.DELETE)

    @patch.object(LogEntry, "objects")
    def test_signals_errors(self, log_entry_objects_mock):
        class CustomSignalError(BaseException):
            pass

        def post_log_receiver(error, **_kwargs):
            self.my_post_log_data["my_error"] = error

        post_log.connect(post_log_receiver)

        # create
        error_create = CustomSignalError(LogEntry.Action.CREATE)
        log_entry_objects_mock.log_create.side_effect = error_create
        with self.assertRaises(CustomSignalError):
            SimpleModel.objects.create(text="I am not difficult.")
        self.assertEqual(self.my_post_log_data["my_error"], error_create)

        # update
        error_update = CustomSignalError(LogEntry.Action.UPDATE)
        log_entry_objects_mock.log_create.side_effect = error_update
        with self.assertRaises(CustomSignalError):
            obj = SimpleModel.objects.get(pk=self.obj.pk)
            obj.text = "updating"
            obj.save()
        self.assertEqual(self.my_post_log_data["my_error"], error_update)

        # delete
        error_delete = CustomSignalError(LogEntry.Action.DELETE)
        log_entry_objects_mock.log_create.side_effect = error_delete
        with self.assertRaises(CustomSignalError):
            obj = SimpleModel.objects.get(pk=self.obj.pk)
            obj.delete()
        self.assertEqual(self.my_post_log_data["my_error"], error_delete)


@override_settings(AUDITLOG_DISABLE_ON_RAW_SAVE=True)
class DisableTest(TestCase):
    """
    All the other tests check logging, so this only needs to test disabled logging.
    """

    def test_create(self):
        # Mimic the way imports create objects
        inst = SimpleModel(
            text="I am a bit more difficult.",
            boolean=False,
            datetime=django_timezone.now(),
        )
        SimpleModel.save_base(inst, raw=True)
        self.assertEqual(0, LogEntry.objects.get_for_object(inst).count())

    def test_create_with_context_manager(self):
        with disable_auditlog():
            inst = SimpleModel.objects.create(text="I am a bit more difficult.")
        self.assertEqual(0, LogEntry.objects.get_for_object(inst).count())

    def test_update(self):
        inst = SimpleModel(
            text="I am a bit more difficult.",
            boolean=False,
            datetime=django_timezone.now(),
        )
        SimpleModel.save_base(inst, raw=True)
        inst.text = "I feel refreshed"
        inst.save_base(raw=True)
        self.assertEqual(0, LogEntry.objects.get_for_object(inst).count())

    def test_update_with_context_manager(self):
        inst = SimpleModel(
            text="I am a bit more difficult.",
            boolean=False,
            datetime=django_timezone.now(),
        )
        SimpleModel.save_base(inst, raw=True)
        with disable_auditlog():
            inst.text = "I feel refreshed"
            inst.save()
        self.assertEqual(0, LogEntry.objects.get_for_object(inst).count())

    def test_m2m(self):
        """
        Create m2m from fixture and check that nothing was logged.
        This only works with context manager
        """
        with disable_auditlog():
            management.call_command(
                "loaddata", "test_app/fixtures/m2m_test_fixture.json", verbosity=0
            )
        recursive = ManyRelatedModel.objects.get(pk=1)
        self.assertEqual(0, LogEntry.objects.get_for_object(recursive).count())
        related = ManyRelatedOtherModel.objects.get(pk=1)
        self.assertEqual(0, LogEntry.objects.get_for_object(related).count())


class MissingModelTest(TestCase):
    def setUp(self):
        # Create a log entry, then unregister the model
        self.obj = SimpleModel.objects.create(text="I am old.")
        auditlog.unregister(SimpleModel)

    def tearDown(self):
        # Re-register the model for other tests
        auditlog.register(SimpleModel)

    def test_get_changes_for_missing_model(self):
        history = self.obj.history.latest()
        self.assertEqual(history.changes_dict["text"][1], self.obj.text)
        self.assertEqual(history.changes_display_dict["text"][1], self.obj.text)


class ModelManagerTest(TestCase):
    """
    This does not directly assert the configured manager, but its behaviour.
    The "secret" object should not be accessible, as the queryset is overridden.
    """

    def setUp(self):
        self.secret = SwappedManagerModel.objects.create(is_secret=True, name="Secret")
        self.public = SwappedManagerModel.objects.create(is_secret=False, name="Public")

    def test_update_secret(self):
        self.secret.name = "Updated"
        self.secret.save()
        log = LogEntry.objects.get_for_object(self.secret).first()
        self.assertEqual(log.action, LogEntry.Action.UPDATE)
        self.assertEqual(log.changes_dict["name"], ["None", "Updated"])

    def test_update_public(self):
        self.public.name = "Updated"
        self.public.save()
        log = LogEntry.objects.get_for_object(self.public).first()
        self.assertEqual(log.action, LogEntry.Action.UPDATE)
        self.assertEqual(log.changes_dict["name"], ["Public", "Updated"])


class BaseManagerSettingTest(TestCase):
    """
    If the AUDITLOG_USE_BASE_MANAGER setting is enabled, "secret" objects
    should be audited as if they were public, with full access to field
    values.
    """

    def test_use_base_manager_setting_update(self):
        """
        Model update. The default False case is covered by test_update_secret.
        """
        secret = SwappedManagerModel.objects.create(is_secret=True, name="Secret")
        with override_settings(AUDITLOG_USE_BASE_MANAGER=True):
            secret.name = "Updated"
            secret.save()
            log = LogEntry.objects.get_for_object(secret).first()
            self.assertEqual(log.action, LogEntry.Action.UPDATE)
            self.assertEqual(log.changes_dict["name"], ["Secret", "Updated"])

    def test_use_base_manager_setting_related_model(self):
        """
        When AUDITLOG_USE_BASE_MANAGER is enabled, related model changes that
        are normally invisible to the default model manager should remain
        visible and not refer to "deleted" objects.
        """
        t1 = datetime.datetime(2025, 1, 1, 12, tzinfo=datetime.timezone.utc)
        with (
            override_settings(AUDITLOG_USE_BASE_MANAGER=False),
            freezegun.freeze_time(t1),
        ):
            public_one = SwappedManagerModel.objects.create(name="Public One")
            secret_one = SwappedManagerModel.objects.create(
                is_secret=True, name="Secret One"
            )
            instance_one = SecretRelatedModel.objects.create(
                one_to_one=public_one,
                related=secret_one,
            )

            log_one = instance_one.history.filter(timestamp=t1).first()
            self.assertIsInstance(log_one, LogEntry)
            display_dict = log_one.changes_display_dict
            self.assertEqual(display_dict["related"][0], "None")
            self.assertEqual(
                display_dict["related"][1],
                f"Deleted 'SwappedManagerModel' ({secret_one.id})",
                "Default manager should have no visibility of secret object",
            )
            self.assertEqual(display_dict["one to one"][0], "None")
            self.assertEqual(display_dict["one to one"][1], "Public One")

        t2 = t1 + datetime.timedelta(days=20)
        with (
            override_settings(AUDITLOG_USE_BASE_MANAGER=True),
            freezegun.freeze_time(t2),
        ):
            public_two = SwappedManagerModel.objects.create(name="Public Two")
            secret_two = SwappedManagerModel.objects.create(
                is_secret=True, name="Secret Two"
            )
            instance_two = SecretRelatedModel.objects.create(
                one_to_one=public_two,
                related=secret_two,
            )

            log_two = instance_two.history.filter(timestamp=t2).first()
            self.assertIsInstance(log_two, LogEntry)
            display_dict = log_two.changes_display_dict
            self.assertEqual(display_dict["related"][0], "None")
            self.assertEqual(
                display_dict["related"][1],
                "Secret Two",
                "Base manager should have full visibility of secret object",
            )
            self.assertEqual(display_dict["one to one"][0], "None")
            self.assertEqual(display_dict["one to one"][1], "Public Two")

    def test_use_base_manager_setting_changes(self):
        """
        When AUDITLOG_USE_BASE_MANAGER is enabled, registered many-to-many model
        changes that refer to an object hidden from the default model manager
        should remain visible and be logged.
        """
        with override_settings(AUDITLOG_USE_BASE_MANAGER=False):
            obj_one = SwappedManagerModel.objects.create(
                is_secret=True, name="Secret One"
            )
            m2m_one = SecretM2MModel.objects.create(name="M2M One")
            m2m_one.m2m_related.add(obj_one)

        self.assertIn(m2m_one, obj_one.m2m_related.all(), "Secret One sees M2M One")
        self.assertNotIn(
            obj_one, m2m_one.m2m_related.all(), "M2M One cannot see Secret One"
        )
        self.assertEqual(
            0,
            LogEntry.objects.get_for_object(m2m_one).count(),
            "No update with default manager",
        )

        with override_settings(AUDITLOG_USE_BASE_MANAGER=True):
            obj_two = SwappedManagerModel.objects.create(
                is_secret=True, name="Secret Two"
            )
            m2m_two = SecretM2MModel.objects.create(name="M2M Two")
            m2m_two.m2m_related.add(obj_two)

        self.assertIn(m2m_two, obj_two.m2m_related.all(), "Secret Two sees M2M Two")
        self.assertNotIn(
            obj_two, m2m_two.m2m_related.all(), "M2M Two cannot see Secret Two"
        )
        self.assertEqual(
            1,
            LogEntry.objects.get_for_object(m2m_two).count(),
            "Update logged with base manager",
        )

        log_entry = LogEntry.objects.get_for_object(m2m_two).first()
        self.assertEqual(
            log_entry.changes,
            {
                "m2m_related": {
                    "type": "m2m",
                    "operation": "add",
                    "objects": [smart_str(obj_two)],
                }
            },
        )
        self.assertEqual(
            log_entry.changes_str, f"m2m_related: add {[smart_str(obj_two)]}"
        )


class TestMaskStr(TestCase):
    """Test the mask_str function that masks sensitive data."""

    def test_mask_str_empty(self):
        self.assertEqual(mask_str(""), "")

    def test_mask_str_single_char(self):
        self.assertEqual(mask_str("a"), "a")

    def test_mask_str_even_length(self):
        self.assertEqual(mask_str("1234"), "**34")

    def test_mask_str_odd_length(self):
        self.assertEqual(mask_str("12345"), "**345")

    def test_mask_str_long_text(self):
        self.assertEqual(mask_str("confidential"), "******ential")


class CustomMaskModelTest(TestCase):
    def test_custom_mask_function(self):
        instance = CustomMaskModel.objects.create(
            credit_card="1234567890123456", text="Some text"
        )
        self.assertEqual(
            instance.history.latest().changes_dict["credit_card"][1],
            "****3456",
            msg="The custom masking function should mask all but last 4 digits",
        )

    def test_custom_mask_function_short_value(self):
        """Test that custom masking function handles short values correctly"""
        instance = CustomMaskModel.objects.create(credit_card="123", text="Some text")
        self.assertEqual(
            instance.history.latest().changes_dict["credit_card"][1],
            "123",
            msg="The custom masking function should not mask values shorter than 4 characters",
        )

    def test_custom_mask_function_serialized_data(self):
        instance = CustomMaskModel.objects.create(
            credit_card="1234567890123456", text="Some text"
        )
        log = instance.history.latest()
        self.assertTrue(isinstance(log, LogEntry))
        self.assertEqual(log.action, LogEntry.Action.CREATE)

        # Update to trigger serialization
        instance.credit_card = "9876543210987654"
        instance.save()

        log = instance.history.latest()
        self.assertEqual(
            log.changes_dict["credit_card"][1],
            "****7654",
            msg="The custom masking function should be used in serialized data",
        )


class WithExtraDataMixin(WithActorMixinBase):
    def get_context_data(self):
        return {}

    def make_object(self):
        with set_extra_data(context_data=self.get_context_data()):
            return super().make_object()


class ExtraDataTest(WithExtraDataMixin, SimpleModelTest):
    def get_context_data(self):
        return {
            "actor": self.user,
        }


class ExtraDataWithRoleTest(WithExtraDataMixin, SimpleModelTest):
    def get_context_data(self):
        return {
            "actor": self.user,
            "role": "admin",
        }

    @skipIf(
        settings.AUDITLOG_LOGENTRY_MODEL == "auditlog.LogEntry",
        "Do not run on defualt log entry model",
    )
    def test_extra_data_role(self):
        log = self.obj.history.first()
        self.assertEqual(log.role, "admin")


class ExtraDataWithRoleLazyLoadTest(WithExtraDataMixin, SimpleModelTest):
    def get_context_data(self):
        return {
            "actor": self.user,
            "role": lambda: "admin",
        }

    @skipIf(
        settings.AUDITLOG_LOGENTRY_MODEL == "auditlog.LogEntry",
        "Do not run on defualt log entry model",
    )
    def test_extra_data_role(self):
        log = self.obj.history.first()
        self.assertEqual(log.role, "admin")


class GetLogEntryModelTest(TestCase):
    """Test the get_logentry_model function."""

    def get_model_name(self):
        model = get_logentry_model()
        return f"{model._meta.app_label}.{model._meta.object_name}"

    def test_logentry_model(self):
        self.assertEqual(self.get_model_name(), settings.AUDITLOG_LOGENTRY_MODEL)

    @override_settings(AUDITLOG_LOGENTRY_MODEL="LogEntry")
    def test_invalid_logentry_model_name(self):
        with self.assertRaises(ImproperlyConfigured):
            get_logentry_model()

    @override_settings(AUDITLOG_LOGENTRY_MODEL="test_app2.LogEntry")
    def test_invalid_appname(self):
        with self.assertRaises(ImproperlyConfigured):
            get_logentry_model()

    def test_logentry_model_default_when_setting_missing(self):
        """Regression test for issue #788: AttributeError when AUDITLOG_LOGENTRY_MODEL is not set."""
        # Save and remove the setting to simulate the bug condition
        original_value = getattr(settings, "AUDITLOG_LOGENTRY_MODEL", None)
        if hasattr(settings, "AUDITLOG_LOGENTRY_MODEL"):
            delattr(settings, "AUDITLOG_LOGENTRY_MODEL")

        try:
            # This should NOT raise AttributeError - it should use the default
            model = get_logentry_model()
            self.assertEqual(
                f"{model._meta.app_label}.{model._meta.object_name}",
                "auditlog.LogEntry",
            )
        finally:
            # Restore the original setting
            if original_value is not None:
                settings.AUDITLOG_LOGENTRY_MODEL = original_value
