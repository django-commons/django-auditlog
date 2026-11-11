from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model

from auditlog.cid import set_cid
from auditlog.context import set_extra_data


class AuditlogMiddleware:
    """
    Middleware to couple the request's user to log items. This is accomplished by currying the
    signal receiver with the user from the request (or None if the user is not authenticated).

    The middleware supports both WSGI and ASGI: when the next layer of the middleware chain is
    asynchronous the request is handled without forcing the chain to be adapted to synchronous mode.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response=None):
        self.get_response = get_response
        if iscoroutinefunction(self.get_response):
            # Let Django know that this instance is a coroutine function
            markcoroutinefunction(self)
        if not isinstance(settings.AUDITLOG_DISABLE_REMOTE_ADDR, bool):
            raise TypeError("Setting 'AUDITLOG_DISABLE_REMOTE_ADDR' must be a boolean")

    @staticmethod
    def _get_remote_addr(request):
        if settings.AUDITLOG_DISABLE_REMOTE_ADDR:
            return None

        # In case there is no proxy, return the original address
        if not request.headers.get("X-Forwarded-For"):
            return request.META.get("REMOTE_ADDR")

        # In case of proxy, set 'original' address
        remote_addr: str = request.headers.get("X-Forwarded-For").split(",")[0]

        # Remove port number from remote_addr
        if "." in remote_addr and ":" in remote_addr:  # IPv4 with port (`x.x.x.x:x`)
            remote_addr = remote_addr.split(":")[0]
        elif "[" in remote_addr:  # IPv6 with port (`[:::]:x`)
            remote_addr = remote_addr[1:].split("]")[0]

        return remote_addr

    @staticmethod
    def _get_remote_port(request) -> int | None:
        remote_port = request.headers.get("X-Forwarded-Port", "")

        try:
            remote_port = int(remote_port)
        except ValueError:
            remote_port = None

        return remote_port

    @staticmethod
    def _get_actor(request):
        user = getattr(request, "user", None)
        if isinstance(user, get_user_model()) and user.is_authenticated:
            return user
        return None

    def get_extra_data(self, request):
        context_data = {}
        context_data["remote_addr"] = self._get_remote_addr(request)
        context_data["remote_port"] = self._get_remote_port(request)

        context_data["actor"] = self._get_actor(request)

        return context_data

    async def aget_extra_data(self, request):
        """
        Asynchronous counterpart of ``get_extra_data``, used when handling an async request.

        ``get_extra_data`` is run in a thread, as resolving ``request.user`` may query the database
        and is not allowed in an async context. This also keeps overrides of ``get_extra_data``
        working in both modes.
        """
        return await sync_to_async(self.get_extra_data)(request)

    def __call__(self, request):
        if iscoroutinefunction(self):
            return self.__acall__(request)

        set_cid(request)

        with set_extra_data(context_data=self.get_extra_data(request)):
            return self.get_response(request)

    async def __acall__(self, request):
        set_cid(request)

        with set_extra_data(context_data=await self.aget_extra_data(request)):
            return await self.get_response(request)
