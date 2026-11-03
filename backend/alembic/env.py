from alembic import context
from app.config import get_settings
from app.db.session import Base, make_engine
from app.models import billing, entities, jobs  # noqa: F401

config = context.config
target_metadata = Base.metadata
url = config.attributes.get("database_url") or get_settings().database_url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        # WAL PRAGMA in the connect hook does not leave an open SQL transaction.
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
