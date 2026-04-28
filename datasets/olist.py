from datasets.base import Dataset


class OlistDataset(Dataset):
    name = "Olist E-Commerce"
    db_path = "data/olist.db"
    description = (
        "Brazilian e-commerce dataset (Olist) with orders, customers, "
        "products, payments, reviews, and sellers. Not yet enabled - "
        "run scripts/build_olist_db.py to convert the CSVs to SQLite."
    )
    enabled = False

    def schema_summary(self) -> str:
        raise NotImplementedError(
            "Olist dataset support pending. Convert CSVs first."
        )