# config/__init__.py
import sys
from pathlib import Path

base_dir = Path(__file__).resolve().parent.parent
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from .celery import app as celery_app

__all__ = ("celery_app",)
