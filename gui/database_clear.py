"""Authenticate and clear confirmed N2SCFTDB data in a separate Sage process."""

from __future__ import annotations

import json
import sys


def clear_database(settings, password, *, confirmed=False):
    """Use only the freshly entered password; never initialize the schema."""
    if confirmed is not True:
        raise ValueError("Database deletion was not confirmed.")
    if not isinstance(password, str) or not password:
        raise ValueError("Enter the current MySQL account password.")
    name = settings["mysql/database"].strip()
    if not name:
        raise ValueError("Choose a database in Settings first.")
    from common import n2_theory_db as database

    connection = database.connect_database(
        name, host=settings["mysql/host"], port=settings["mysql/port"],
        user=settings["mysql/user"], password=password,
        unix_socket=settings.get("mysql/unix_socket"),
        connect_timeout=settings["mysql/connect_timeout"], initialize_schema=False,
    )
    try:
        return database.delete_all_theory_data(connection)
    finally:
        connection.close()


def main():
    try:
        request = json.loads(sys.stdin.readline())
        count = clear_database(
            request["settings"], request["password"],
            confirmed=request.get("confirm_delete", False),
        )
        print(json.dumps({"deleted": count}), flush=True)
        return 0
    except Exception as exc:
        # Do not echo raw exceptions or request data containing credentials.
        code = exc.args[0] if exc.args else None
        if code == 1045:
            message = "MySQL rejected the account or password. No data was deleted."
        elif code in (1044, 1142):
            message = "This MySQL account does not have permission to delete the database contents."
        else:
            message = ("Deletion was not confirmed as successful. Check the database connection, "
                       "permissions and schema, and inspect its contents before retrying.")
        print(json.dumps({"error": message}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
