import os
import sys
from pathlib import Path
from django.core.asgi import get_asgi_application

base_dir = Path(__file__).resolve().parent.parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_application = get_asgi_application()


async def application(scope, receive, send):
    """
    Django backend is HTTP-only.
    Explicitly reject websocket scopes to prevent accidental WS usage.
    """
    scope_type = str(scope.get("type") or "").lower()
    if scope_type == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    await django_asgi_application(scope, receive, send)
