from app import create_app

app = create_app()

if __name__ != "__main__":
    # Для Gunicorn
    application = app
else:
    # Для разработки
    app.run(host='0.0.0.0', port=5000, debug=True)