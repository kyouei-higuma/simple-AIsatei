import pandas as pd
import re
import math

file_path = r'c:\Users\MD03\Desktop\不動産仮査定Project\data\seiyaku_20260321_10year_date.csv'

# CSVを読み込む
df = pd.read_csv(file_path, encoding='utf-8-sig', on_bad_lines='skip')

# バックアップ保存
df.to_csv(file_path + ".bak2", index=False, encoding='utf-8-sig')

def clean_price(val):
    if pd.isna(val):
        return val
    s = str(val).replace(",", "").replace(" ", "")
    # "320万円" -> 3200000
    is_man = "万円" in s or "万" in s
    m = re.search(r"([\d\.]+)", s)
    if m:
        num = float(m.group(1))
        if is_man:
            num *= 10000
        return int(num)
    return val

def clean_area(val):
    if pd.isna(val):
        return val
    s = str(val)
    m = re.search(r"([\d\.]+)", s)
    if m:
        return float(m.group(1))
    return val

def clean_year(val):
    if pd.isna(val):
        return val
    s = str(val)
    m = re.search(r"(\d{4})", s)
    if m:
        return int(m.group(1))
    return val

# データのクレンジング
if 'price' in df.columns:
    df['price'] = df['price'].apply(clean_price)
if 'land_area' in df.columns:
    df['land_area'] = df['land_area'].apply(clean_area)
if 'building_area' in df.columns:
    df['building_area'] = df['building_area'].apply(clean_area)
if 'floor_area' in df.columns:
    df['floor_area'] = df['floor_area'].apply(clean_area)
if 'construction_year' in df.columns:
    df['construction_year'] = df['construction_year'].apply(clean_year)

# クレンジング後のデータを保存
df.to_csv(file_path, index=False, encoding='utf-8-sig')

print("CSVの単位を削除し、数値のみに変換しました！")
print(df[['price', 'land_area', 'building_area', 'floor_area', 'construction_year']].head())
