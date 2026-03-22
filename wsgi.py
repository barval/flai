from app import create_app, socketio

app = create_app()

if __name__ != "__main__":
    # For Gunicorn with eventlet
    application = app
else:
    # For development
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)