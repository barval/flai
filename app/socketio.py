# app/socketio.py
from flask_socketio import SocketIO

# Create SocketIO instance without initializing with app yet
socketio = SocketIO(cors_allowed_origins="*", logger=True, engineio_logger=True)