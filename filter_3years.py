import pandas as pd
from datetime import datetime
import os
from pathlib import Path

DATA_DIR = Path(r"c:\Users\MD03\Desktop\不動産仮査定Project\data")
INPUT_CSV = DATA_DIR / "seiyaku_20260321_10year_date.csv"
OUTPUT_CSV = DATA_DIR / "reins_data_3years.csv"

def filter_recent_years():
    print(f"Reading {INPUT_CSV} ...")
    # UTF-8-SIG (BOM付き)で読み込む
    df = pd.read_csv(INPUT_CSV, encoding="utf-8-sig")
    
    # 過去3年（現在は2026年なので、2023年以降のデータを抽出）
    # contract_date が "YYYY/MM/DD" や "YYYY/MM" などの形式と想定
    current_year = 2026
    three_years_ago = current_year - 3
    
    # 'contract_date' を使ってフィルタリング
    if 'contract_date' in df.columns:
        # 文字列として先頭4文字（年）を抽出
        df['contract_year'] = df['contract_date'].astype(str).str.extract(r'^(\d{4})')[0]
        # 数値に変換できないものは NaN になる
        df['contract_year'] = pd.to_numeric(df['contract_year'], errors='coerce')
        
        # 過去3年分（2023年以上）を抽出
        filtered_df = df[df['contract_year'] >= three_years_ago].copy()
        
        # 不要な一時列を削除
        filtered_df = filtered_df.drop(columns=['contract_year'])
        
        print(f"Original records: {len(df)}")
        print(f"Filtered records (>= {three_years_ago}): {len(filtered_df)}")
        
        # 保存
        filtered_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
        print(f"Saved to {OUTPUT_CSV}")
    else:
        print("Error: 'contract_date' column not found.")

if __name__ == "__main__":
    filter_recent_years()
