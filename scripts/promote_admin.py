from pathlib import Path
import sys

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models.admin_account import AdminAccount  # noqa: E402
from app.models.user import User  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    email = input("Email of an existing account to make an admin: ").strip().lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            print("No account exists with that email. Register that account first.")
            return
        if db.get(AdminAccount, user.id) is None:
            db.add(AdminAccount(user_id=user.id))
            db.commit()
        print(f"Admin access enabled for {user.email}.")


if __name__ == "__main__":
    main()
