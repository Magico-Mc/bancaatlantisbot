# Expose the Flask app at package level so gunicorn can import `BancaAtlantisBot:app`
from .BancaAtlantisBot import app

__all__ = ["app"]
