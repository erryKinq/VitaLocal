from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from vitalocal.config import get_settings

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)


def run_migrations_online() -> None:
    url = context.config.attributes.get("url") or get_settings().database_url
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
