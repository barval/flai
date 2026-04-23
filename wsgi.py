from app import create_app
import atexit

app = create_app()

def _shutdown_workers():
    """Gracefully stop queue workers on application exit."""
    if hasattr(app, 'request_queue') and hasattr(app.request_queue, 'stop_workers'):
        app.request_queue.stop_workers(timeout=30)

atexit.register(_shutdown_workers)

if __name__ != "__main__":
    # For Gunicorn
    application = app
else:
    # For development
    app.run(host='0.0.0.0', port=5000, debug=True)