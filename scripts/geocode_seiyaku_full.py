"""
seiyaku_20260321_10year_date.csv 全行に latitude / longitude を付与するバッチ。
国土地理院 AddressSearch を優先し、失敗時のみ Nominatim（1秒間隔）。
同一住所はメモリキャッシュで1回だけAPI呼び出し。
中断後も再実行で未設定行のみ続行可能（定期保存あり）。

使い方:
  python scripts/geocode_seiyaku_full.py
"""
from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "seiyaku_20260321_10year_date.csv"

# 国土地理院APIへの負荷軽減（秒）
GSI_DELAY_SEC = 0.35
SAVE_EVERY = 150
ADDRESS_COL = "address"


def _save_csv_safe(df: pd.DataFrame, path: Path) -> bool:
    """Excel 等で開いていると PermissionError になるため再試行する。"""
    for attempt in range(8):
        try:
            df.to_csv(path, index=False, encoding="utf-8-sig")
            return True
        except PermissionError:
            time.sleep(2.5)
    print(
        "[警告] CSV を保存できません（他アプリで開いていませんか）。閉じてから再実行するか、しばらく待って再試行してください。",
        flush=True,
    )
    return False


def _is_valid_coord(val: Any) -> bool:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return False
    if str(val).strip() in ("", "nan", "None"):
        return False
    try:
        v = float(val)
        return -180 <= v <= 180
    except (ValueError, TypeError):
        return False


def geocode_gsi(address: str) -> Optional[Tuple[float, float]]:
    try:
        url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
        resp = requests.get(url, params={"q": address.strip()}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data and len(data) > 0:
            geom = data[0].get("geometry") or {}
            coords = geom.get("coordinates", [])
            if len(coords) >= 2:
                lon, lat = float(coords[0]), float(coords[1])
                return (lat, lon)
    except Exception:
        pass
    return None


def geocode_nominatim(address: str) -> Optional[Tuple[float, float]]:
    try:
        from geopy.geocoders import Nominatim
        from geopy.extra.rate_limiter import RateLimiter

        geolocator = Nominatim(user_agent="kyouei_asahikawa_geocode_batch")
        geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)
        query = f"日本 {address.strip()}" if "日本" not in address else address.strip()
        location = geocode(query)
        if location:
            return (float(location.latitude), float(location.longitude))
    except Exception:
        pass
    return None


def geocode_one(address: str, cache: Dict[str, Optional[Tuple[float, float]]]) -> Optional[Tuple[float, float]]:
    key = (address or "").strip()
    if not key:
        return None
    if key in cache:
        return cache[key]

    time.sleep(GSI_DELAY_SEC)
    result = geocode_gsi(key)
    if result is None:
        result = geocode_nominatim(key)

    cache[key] = result
    return result


def main() -> int:
    if not CSV_PATH.exists():
        print(f"CSV が見つかりません: {CSV_PATH}", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"seiyaku_20260321_10year_date_backup_{stamp}.csv"
    shutil.copy2(CSV_PATH, backup_path)
    print(f"バックアップ: {backup_path}", flush=True)

    df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    if ADDRESS_COL not in df.columns:
        print(f"列 '{ADDRESS_COL}' がありません。", file=sys.stderr)
        return 1

    if "latitude" not in df.columns:
        df["latitude"] = np.nan
    if "longitude" not in df.columns:
        df["longitude"] = np.nan

    cache: Dict[str, Optional[Tuple[float, float]]] = {}
    total = len(df)
    need = 0
    done = 0
    fail = 0

    for i in range(total):
        lat0 = df.at[i, "latitude"]
        lon0 = df.at[i, "longitude"]
        if _is_valid_coord(lat0) and _is_valid_coord(lon0):
            continue

        need += 1
        addr = df.at[i, ADDRESS_COL]
        if pd.isna(addr) or str(addr).strip() == "":
            fail += 1
            continue

        coords = geocode_one(str(addr), cache)
        if coords:
            df.at[i, "latitude"] = coords[0]
            df.at[i, "longitude"] = coords[1]
            done += 1
        else:
            fail += 1

        if need % SAVE_EVERY == 0:
            if _save_csv_safe(df, CSV_PATH):
                print(
                    f"途中保存 … 処理済み（今回の未設定行ベース） need={need} ok={done} fail={fail} / 行{i+1}/{total}",
                    flush=True,
                )

    _save_csv_safe(df, CSV_PATH)
    print(
        f"完了。全{total}行。今回ジオコーディング対象 need={need}、成功={done}、失敗（空住所含む）={fail}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
