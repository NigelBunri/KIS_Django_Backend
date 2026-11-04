# apps/bots/permissions.py
from rest_framework.permissions import BasePermission

from .models import BotCredential


class IsBotCredential(BasePermission):
    """Gate for the external bot API surface — request.auth must be a
    live BotCredential (set by auth.BotAccessTokenAuthentication)."""

    def has_permission(self, request, view) -> bool:
        return isinstance(request.auth, BotCredential)


class IsOwnBotResource(BasePermission):
    """Object-level check for owner-facing bot management endpoints —
    the authenticated human user must own the Bot this object belongs
    to. Views using this must expose the related Bot via `obj.bot`
    (grants, webhook subscriptions, AI connections) or be the Bot
    itself (`obj`)."""

    def has_object_permission(self, request, view, obj) -> bool:
        bot = obj if obj.__class__.__name__ == "Bot" else getattr(obj, "bot", None)
        return bool(bot) and bot.owner_id == request.user.id
