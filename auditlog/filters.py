from django.contrib.admin import SimpleListFilter
from django.contrib.admin.options import IncorrectLookupParameters
from django.utils.translation import gettext_lazy as _


class ResourceTypeFilter(SimpleListFilter):
    title = _("Resource Type")
    parameter_name = "resource_type"

    def lookups(self, request, model_admin):
        qs = model_admin.get_queryset(request)
        types = qs.values_list("content_type_id", "content_type__model")
        return list(types.order_by("content_type__model").distinct())

    def queryset(self, request, queryset):
        value = self.value()
        if value is None:
            return queryset
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise IncorrectLookupParameters(exc) from exc
        return queryset.filter(content_type_id=value)


class CIDFilter(SimpleListFilter):
    title = _("Correlation ID")
    parameter_name = "cid"

    def lookups(self, request, model_admin):
        return []

    def has_output(self):
        return True

    def queryset(self, request, queryset):
        if self.value() is None:
            return queryset
        return queryset.filter(cid=self.value())
