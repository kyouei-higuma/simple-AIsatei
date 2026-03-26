import pandas as pd
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
INPUT_CSV = DATA_DIR / "seiyaku_20260321_10year_date.csv"
OUTPUT_CSV = DATA_DIR / "seiyaku_20260321_10year_date.csv"


def filter_recent_years():
    """contract_date の年で、直近5年分に絞り込む（再生成用ユーティリティ）。"""
    print(f"Reading {INPUT_CSV} ...")
    df = pd.read_csv(INPUT_CSV, encoding="utf-8-sig")

    current_year = datetime.now().year
    five_years_ago = current_year - 5

    if "contract_date" in df.columns:
        df["contract_year"] = df["contract_date"].astype(str).str.extract(r"^(\d{4})")[0]
        df["contract_year"] = pd.to_numeric(df["contract_year"], errors="coerce")

        filtered_df = df[df["contract_year"] >= five_years_ago].copy()
        filtered_df = filtered_df.drop(columns=["contract_year"])

        print(f"Original records: {len(df)}")
        print(f"Filtered records (>= {five_years_ago}, past 5 years): {len(filtered_df)}")

        filtered_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
        print(f"Saved to {OUTPUT_CSV}")
    else:
        print("Error: 'contract_date' column not found.")


if __name__ == "__main__":
    filter_recent_years()
