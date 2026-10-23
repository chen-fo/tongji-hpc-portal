"""Isolated browser-test server. Never opens or changes the development database."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from alembic.config import Config  # noqa: E402

from alembic import command  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.entities import User  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="portal-e2e-") as folder:
        root = Path(folder)
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{root / 'portal.sqlite3'}",
            mock_root=root / "mock-ssh",
        )
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.attributes["database_url"] = settings.database_url
        command.upgrade(cfg, "head")
        app = create_app(settings)
        with app.state.sessions() as db:
            db.add(
                User(
                    username="admin",
                    password_hash=hash_password(os.environ["PORTAL_E2E_PASSWORD"]),
                    role="admin",
                )
            )
            db.commit()
        uvicorn.run(app, host="127.0.0.1", port=8000)
