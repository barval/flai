import click
from flask.cli import with_appcontext
from app.userdb import get_user_by_login, create_user, update_password

@click.command('admin-password')
@click.argument('password')
@with_appcontext
def set_admin_password(password):
    """Установить пароль администратора (создаёт пользователя admin, если его нет)"""
    admin = get_user_by_login('admin')
    if admin:
        update_password('admin', password)
        click.echo('Пароль администратора изменён.')
    else:
        create_user(
            login='admin',
            password=password,
            name='Administrator',
            service_class=0,
            is_admin=True
        )
        click.echo('Администратор создан.')