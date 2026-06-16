import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 2
timeout = 120

def on_starting(server):
    """Roda antes dos workers iniciarem."""
    from servidor import init_db
    init_db()
