import sqlite3
from datasets.base import Dataset


class NorthwindDataset(Dataset):
    name = "Northwind"
    db_path = "data/northwind.db"
    description = (
        "Classic Northwind sample database. Contains customers, orders, "
        "order details, products, categories, suppliers, employees, "
        "shippers, and territories for a small import/export company."
    )
    enabled = True

    def __init__(self) -> None:
        self._cached_schema: str | None = None

    def schema_summary(self) -> str:
        if self._cached_schema is not None:
            return self._cached_schema
        
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                ).fetchall()
            ]

            sections: list[str] = []
            for table in tables:
                cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
                col_lines = []
                for _cid, col_name, col_type, _notnull, _default, _pk in cols:
                    type_label = col_type or "ANY"
                    examples = self._examples(conn, table, col_name, col_type)
                    suffix = f" e.g. {examples}" if examples else ""
                    col_lines.append(f"  - {col_name} ({type_label}){suffix}")
                sections.append(f"Table {table}:\n" + "\n".join(col_lines))

            self._cached_schema = "\n\n".join(sections)
            return self._cached_schema
        finally:
            conn.close()


    @staticmethod
    def _examples(
        conn: sqlite3.Connection, table: str, col: str, col_type: str | None
    ) -> str:
        if not col_type or col_type.upper() not in {"TEXT", "VARCHAR", "NVARCHAR", "CHAR"}:
            return ""
        try:
            distinct = conn.execute(
                f"SELECT COUNT(DISTINCT \"{col}\") FROM \"{table}\""
            ).fetchone()[0]
        except sqlite3.Error:
            return ""
        if distinct == 0 or distinct > 20:
            return ""
        rows = conn.execute(
            f"SELECT DISTINCT \"{col}\" FROM \"{table}\" "
            f"WHERE \"{col}\" IS NOT NULL LIMIT 3"
        ).fetchall()
        values = [str(r[0]) for r in rows]
        return ", ".join(values)
