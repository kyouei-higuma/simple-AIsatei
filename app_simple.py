"""
簡易AI査定アプリ（HP用・軽量版）
不動産仮査定アプリの簡易版。地図・周辺事例リストは非表示。
弊社HPからのリンク用に最適化。
"""

import html
import io
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import streamlit as st
import requests
import pandas as pd
import numpy as np

# data/ フォルダのパス（過去3年分のデータを優先）
DATA_DIR = Path(__file__).resolve().parent / "data"
CSV_PATH_3YEARS = DATA_DIR / "reins_data_3years.csv"
CSV_PATH_LEGACY = DATA_DIR / "reins_data.csv"

# ページ設定
st.set_page_config(
    page_title="AI査定",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Streamlitのツールバー・ヘッダー・フッターを非表示
st.markdown("""
<style>
[data-testid="stToolbar"] { display: none !important; }
[data-testid="stHeader"] { display: none !important; }
footer { display: none !important; }
</style>
""", unsafe_allow_html=True)

# 定数
MAX_REFERENCE_CASES = 20
M2_TO_TSUBO = 3.30578  # 1坪 = 3.30578㎡（坪単価換算用）
LAND_MARKUP_RATE = 1.20  # 土地単価の20%上乗せ（成約ベースの補正）

# Webhook転送用（環境変数 WEBHOOK_URL または Streamlit Secrets で設定）
def _get_webhook_url() -> Optional[str]:
    """転送先URLを取得（一時的に停止中）"""
    return None
    # 復旧する場合は以下をコメントアウト解除
    # try:
    #     if hasattr(st, "secrets") and st.secrets.get("WEBHOOK_URL"):
    #         return str(st.secrets["WEBHOOK_URL"]).strip()
    # except Exception:
    #     pass
    # import os
    # url = os.environ.get("WEBHOOK_URL", "").strip()
    # return url if url else None


def _format_payload_for_google_chat(payload: Dict[str, Any]) -> Dict[str, str]:
    """Google Chat用にメッセージを整形（text形式）"""
    contact = payload.get("お客様情報", {})
    property_info = payload.get("物件情報", {})
    result = payload.get("査定結果", {})
    lines = [
        f"*【AI査定】新規お問い合わせ*",
        f"",
        f"*■ お客様情報*",
        f"お名前: {contact.get('お名前', '-')}",
        f"電話番号: {contact.get('電話番号', '-')}",
        f"メール: {contact.get('メールアドレス', '-')}",
        f"",
        f"*■ 物件情報*",
        f"住所: {property_info.get('住所', '-')}",
        f"種別: {property_info.get('物件種別', '-')}",
        f"土地: {property_info.get('土地面積（㎡）', '-')}㎡ / 建物: {property_info.get('建物面積（㎡）', '-')}㎡ / 専有: {property_info.get('専有面積（㎡）', '-')}㎡",
        f"築年数: {property_info.get('築年数（年）', '-')}年 / 角地: {'あり' if property_info.get('角地・準角地') else 'なし'}",
        f"",
        f"*■ 査定結果*",
        f"仮査定金額: *{result.get('仮査定金額（万円）', '-')}万円*",
        f"㎡単価: {result.get('㎡単価の平均（万円/㎡）', '-')}万円/㎡ / 坪単価: {result.get('坪単価の平均（万円/坪）', '-')}万円/坪 / 参照事例: {result.get('参照事例数', '-')}件",
        f"",
        f"送信日時: {payload.get('送信日時', '-')}",
    ]
    return {"text": "\n".join(lines)}


def send_inquiry_to_webhook(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    お客様情報・査定結果をWebhookにPOST転送。
    Google ChatのURLの場合は自動でフォーマット変換。
    戻り値: (成功True/失敗False, エラー時のメッセージ)
    """
    url = _get_webhook_url()
    if not url:
        return False, None
    try:
        # Google Chat用フォーマット（chat.googleapis.com の場合）
        if "chat.googleapis.com" in url:
            body = _format_payload_for_google_chat(payload)
        else:
            body = payload

        resp = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            return True, None
        # エラー時の詳細を返す（デバッグ用）
        err_msg = f"HTTP {resp.status_code}"
        try:
            err_body = resp.text[:200] if resp.text else ""
            if err_body:
                err_msg += f": {err_body}"
        except Exception:
            pass
        return False, err_msg
    except requests.exceptions.Timeout:
        return False, "タイムアウト（接続が遅い可能性があります）"
    except requests.exceptions.ConnectionError:
        return False, "接続エラー（URLまたはネットワークを確認してください）"
    except Exception as e:
        return False, str(e)[:100]


# 物件種別 → CSVの物件項目（同様事例の絞り込み用）
PROPERTY_TYPE_TO_CSV_TYPE = {
    "土地": ["売地", "土地", "宅地"],
    "中古住宅（戸建て）": ["中古戸建", "既存住宅"],
    "中古マンション": ["中古マンション", "既存ＭＳ"],
}


def _get_map_zoom_for_radius(radius_km: float) -> int:
    """検索半径(km)に応じた地図のズームレベルを返す"""
    zoom = max(11, min(15, round(14 - math.log2(max(0.5, radius_km)))))
    return zoom


def _geocode_gsi(address: str) -> Optional[Tuple[float, float]]:
    """国土地理院APIで住所→緯度経度（日本の住所に最適・認証不要）"""
    try:
        url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
        resp = requests.get(url, params={"q": address.strip()}, timeout=10)
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


def _geocode_nominatim(address: str) -> Optional[Tuple[float, float]]:
    """geopy Nominatim で住所→緯度経度（フォールバック用）"""
    try:
        from geopy.geocoders import Nominatim
        from geopy.extra.rate_limiter import RateLimiter
        geolocator = Nominatim(user_agent="real_estate_app")
        geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
        query = f"日本 {address.strip()}" if "日本" not in address else address.strip()
        location = geocode(query)
        if location:
            return (location.latitude, location.longitude)
    except Exception:
        pass
    return None


@st.cache_data(ttl=86400)
def _geocode_address_cached(address: str) -> Optional[Tuple[float, float]]:
    """住所→緯度経度の変換（キャッシュ付き・一度変換した住所は即座に返却）"""
    if not address or not address.strip():
        return None
    result = _geocode_gsi(address)
    if result:
        return result
    return _geocode_nominatim(address)


def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    """住所を緯度・経度に変換（キャッシュ付き・国土地理院API優先 → Nominatim）"""
    result = _geocode_address_cached(address)
    if result is None and address and address.strip():
        st.error("住所を緯度・経度に変換できませんでした。住所を確認してください。")
    return result


@st.cache_data(ttl=86400)
def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """緯度・経度から住所を取得（逆ジオコーディング・Nominatim使用）"""
    try:
        from geopy.geocoders import Nominatim
        from geopy.extra.rate_limiter import RateLimiter
        geolocator = Nominatim(user_agent="real_estate_app")
        reverse = RateLimiter(geolocator.reverse, min_delay_seconds=1)
        location = reverse(f"{lat}, {lon}", language="ja")
        if location and location.address:
            return location.address
    except Exception:
        pass
    return None


def haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """2点間の距離（メートル）を Haversine 公式で計算"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def parse_numeric(value, suffixes: Tuple[str, ...] = (",", " ", "円", "/m²", "㎡", "m²", "万円", "万")) -> Optional[float]:
    """文字列を数値に変換（単位が含まれていても抽出する）"""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    
    s = str(value).replace(",", "").replace(" ", "")
    # "320万円" のような場合は 10000 倍する
    is_man = "万円" in s or "万" in s
    
    import re
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            val = float(m.group(1))
            if is_man:
                val *= 10000
            return val
        except ValueError:
            return None
    return None


def _parse_area_to_sqm(value: Any) -> Optional[float]:
    """面積を㎡の数値に変換"""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(" ", "").replace("㎡", "").replace("m²", "")
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_price_man(value: Any) -> Optional[int]:
    """価格文字列（例: '320万円'）を数値に変換"""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).replace(",", "").strip()
    is_man = "万円" in s or "万" in s
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            val = float(m.group(1))
            if is_man:
                val *= 10000
            return int(val)
        except ValueError:
            return None
    return None


def _parse_date_ymd(s: Any) -> Optional[datetime]:
    """YYYY/MM/DD または YYYY/MM を datetime に変換"""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    m = re.search(r"(\d{4})[/年.-](\d{1,2})[/月.-]?(\d{0,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        d = int(m.group(3)) if m.group(3) else 1
        try:
            return datetime(y, mo, min(d, 28))
        except ValueError:
            return datetime(y, mo, 1)
    return None


_MONTH_ABBREV = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_construction_date(cy_val: Any) -> Optional[datetime]:
    """建築年月を datetime に変換。YYYY/MM、Mon-YY（例: Nov-75）に対応"""
    if cy_val is None or (isinstance(cy_val, float) and pd.isna(cy_val)):
        return None
    s = str(cy_val).strip()
    if not s:
        return None
    m = re.search(r"(\d{4})[/年.-](\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        try:
            return datetime(y, mo, 1)
        except ValueError:
            return None
    m2 = re.search(r"([a-zA-Z]{3})[-/](\d{2})", s, re.IGNORECASE)
    if m2:
        mon_str = m2.group(1).lower()[:3]
        yy = int(m2.group(2))
        mo = _MONTH_ABBREV.get(mon_str)
        if mo is not None:
            y = 1900 + yy if yy >= 50 else 2000 + yy
            try:
                return datetime(y, mo, 1)
            except ValueError:
                return None
    m3 = re.search(r"(\d{4})[年]?", s)
    if m3:
        y = int(m3.group(1))
        try:
            return datetime(y, 1, 1)
        except ValueError:
            return None
    return None


def _ensure_reins_data_3years() -> Path:
    """reins_data_3years.csv がなければ reins_data.csv から生成"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CSV_PATH_3YEARS.exists():
        return CSV_PATH_3YEARS
    if not CSV_PATH_LEGACY.exists():
        return CSV_PATH_3YEARS
    import random
    random.seed(42)
    try:
        df = pd.read_csv(CSV_PATH_LEGACY, encoding="utf-8")
    except Exception:
        df = pd.read_csv(CSV_PATH_LEGACY, encoding="cp932")
    construction_years = []
    for _, row in df.iterrows():
        dt = _parse_date_ymd(row.get("contract_date"))
        t = str(row.get("type", ""))
        if dt is None or "売地" in t:
            construction_years.append("")
            continue
        age = random.randint(5, 32)
        cy = datetime(dt.year - age, dt.month, 1)
        construction_years.append(f"{cy.year}/{cy.month:02d}")
    df["construction_year"] = construction_years
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(CSV_PATH_3YEARS, index=False, encoding="utf-8")
    return CSV_PATH_3YEARS


def _is_valid_coord(val: Any) -> bool:
    """緯度・経度が有効な値か（空・NaNでなければ有効、日本付近の範囲）"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    if str(val).strip() in ("", "nan", "None"):
        return False
    try:
        v = float(val)
        return -180 <= v <= 180
    except (ValueError, TypeError):
        return False


@st.cache_data
def load_data(csv_path: str, csv_mtime: float) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
    """
    reins_data_3years.csv を読み込み、築年数計算・クレンジングを行う。
    latitude/longitude 列があれば座標を再利用（API呼び出しなし）。
    戻り値: (cases, df) - dfは座標保存用に保持
    """
    if not csv_path or csv_mtime <= 0:
        return [], pd.DataFrame()
    path = Path(csv_path)
    if not path.exists():
        return [], pd.DataFrame()
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except Exception:
        try:
            df = pd.read_csv(path, encoding="cp932")
        except Exception:
            return [], pd.DataFrame()
    if "latitude" not in df.columns:
        df["latitude"] = np.nan
    if "longitude" not in df.columns:
        df["longitude"] = np.nan
    cases = []
    for idx, row in df.iterrows():
        addr = str(row.get("address", row.get("所在地", ""))).strip()
        if not addr:
            continue
        lat = row.get("latitude")
        lon = row.get("longitude")
        has_coords = _is_valid_coord(lat) and _is_valid_coord(lon)
        case = _load_case_from_row(row, df.columns, idx)
        if has_coords:
            case["lat"] = float(lat)
            case["lon"] = float(lon)
        case["_df_index"] = idx
        cases.append(case)
    return cases, df


def _load_case_from_row(row: pd.Series, columns: Any, df_index: int) -> Dict[str, Any]:
    """DataFrameの1行からcase辞書を構築"""
    has_construction_year = "construction_year" in columns
    addr = str(row.get("address", row.get("所在地", ""))).strip()
    
    # priceはcontract_price（成約価格）を優先
    price_raw = row.get("contract_price") if pd.notna(row.get("contract_price")) else row.get("price")
    price = _parse_price_man(price_raw)
    
    contract_dt = _parse_date_ymd(row.get("contract_date", row.get("成約日", "")))
    construction_dt = None
    age_at_contract = None
    if has_construction_year:
        construction_dt = _parse_construction_date(row.get("construction_year"))
    if contract_dt and construction_dt:
        delta = contract_dt - construction_dt
        age_at_contract = max(0, delta.days / 365)
        
    zoning_raw = str(row.get("zoning", row.get("用途地域", "")))
    if " / " in zoning_raw:
        zoning_raw = zoning_raw.split(" / ")[-1]
        
    return {
        "所在地": addr,
        "成約価格_円": price,
        "成約年月日": str(row.get("contract_date", row.get("成約日", ""))),
        "物件項目": str(row.get("type", row.get("物件項目", ""))),
        "用途地域": zoning_raw,
        "土地面積_数値": _parse_area_to_sqm(row.get("land_area")) if pd.notna(row.get("land_area")) else None,
        "建物面積_数値": _parse_area_to_sqm(row.get("building_area")) if pd.notna(row.get("building_area")) else None,
        "専有面積_数値": _parse_area_to_sqm(row.get("floor_area")) if pd.notna(row.get("floor_area")) else None,
        "間取り": str(row.get("floor_plan", "")) if pd.notna(row.get("floor_plan")) else None,
        "接道状況": str(row.get("road_status", row.get("接道状況", ""))) if pd.notna(row.get("road_status")) else None,
        "接道1": str(row.get("road_width", row.get("接道1", ""))) if pd.notna(row.get("road_width")) else None,
        "築年数_成約時": age_at_contract,
    }


def csv_row_to_feature(
    row: Dict[str, Any],
    center_lon: Optional[float],
    center_lat: Optional[float],
    df: Optional[pd.DataFrame] = None,
) -> Tuple[Dict, bool]:
    """
    CSV行をfeature形式に変換。
    座標が既にあれば使用（API呼び出しなし）。なければジオコーディングしrowとdfを更新。
    戻り値: (feature, needs_save)
    """
    address = row.get("所在地", "")
    lon, lat = center_lon, center_lat
    needs_save = False
    if row.get("lat") is not None and row.get("lon") is not None:
        lat, lon = float(row["lat"]), float(row["lon"])
    elif address:
        coords = _geocode_address_cached(address)
        if coords:
            lat, lon = coords
            row["lat"], row["lon"] = lat, lon
            needs_save = True
            idx = row.get("_df_index")
            if df is not None and idx is not None:
                df.at[idx, "latitude"] = lat
                df.at[idx, "longitude"] = lon
    total = row.get("成約価格_円")
    land_a = row.get("土地面積_数値")
    bldg_a = row.get("建物面積_数値")
    excl_a = row.get("専有面積_数値")
    if excl_a and excl_a > 0:
        area = excl_a
    elif land_a and bldg_a and (land_a > 0 or bldg_a > 0):
        area = (land_a or 0) + (bldg_a or 0)
    else:
        area = bldg_a or land_a or excl_a
    age_at_contract = row.get("築年数_成約時")
    props = {
        "district_name_ja": address or "-",
        "floor_plan_name_ja": row.get("物件項目", "-"),
        "u_area_ja": str(area) if area is not None else None,
        "u_building_total_floor_area_ja": str(area) if area is not None else None,
        "u_transaction_price_total_ja": str(int(total)) if total is not None else None,
        "point_in_time_name_ja": row.get("成約年月日", "-"),
        "u_construction_year_ja": f"{age_at_contract:.0f}年" if age_at_contract is not None else "-",
        "築年数_成約時": age_at_contract,
        "所在地": address or None,
        "成約年月日": row.get("成約年月日"),
        "物件項目": row.get("物件項目"),
        "用途地域": row.get("用途地域"),
        "土地面積_数値": row.get("土地面積_数値"),
        "建物面積_数値": row.get("建物面積_数値"),
        "専有面積_数値": row.get("専有面積_数値") or area,
        "接道状況": row.get("接道状況"),
        "接道1": row.get("接道1"),
    }
    geom = {"coordinates": [lon or 0, lat or 0]} if (lon is not None and lat is not None) else {}
    return ({"properties": props, "geometry": geom}, needs_save)


def save_geocodes_to_csv(df: pd.DataFrame) -> None:
    """座標を reins_data_3years.csv に上書き保存"""
    if df is None or df.empty:
        return
    try:
        df.to_csv(CSV_PATH_3YEARS, index=False, encoding="utf-8")
    except Exception:
        pass


def filter_csv_by_distance(
    csv_cases: List[Dict[str, Any]],
    center_lat: float,
    center_lon: float,
    radius_m: float,
    csv_df: Optional[pd.DataFrame] = None,
    progress_placeholder: Optional[Any] = None,
) -> List[Dict]:
    """
    CSV事例を検索住所からの距離でフィルタ。
    座標がない行のみジオコーディング（API呼び出し）。10件ごとにCSV保存。
    """
    SAVE_INTERVAL = 10
    features = []
    geocode_count = 0
    for row in csv_cases:
        feat, needs_save = csv_row_to_feature(row, center_lon, center_lat, csv_df)
        if needs_save:
            geocode_count += 1
            if geocode_count % SAVE_INTERVAL == 0 and csv_df is not None and not csv_df.empty:
                save_geocodes_to_csv(csv_df)
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [0, 0])
        if len(coords) >= 2:
            plon, plat = float(coords[0]), float(coords[1])
            if plon != 0 or plat != 0:
                d = haversine_distance(center_lon, center_lat, plon, plat)
                if d <= radius_m:
                    features.append(feat)
    if geocode_count > 0 and csv_df is not None and not csv_df.empty:
        save_geocodes_to_csv(csv_df)
    return features


def apply_case_filters(
    csv_features: List[Dict],
    type_selected: List[str],
    age_min: float,
    age_max: float,
    contract_period: str,
) -> List[Dict]:
    """
    事例を物件種別・築年数・成約時期でフィルタ。
    type_selected: 空なら全種別、指定ありならその種別のみ
    contract_period: "1year" | "2years" | "all"
    """
    now = datetime.now()
    filtered = []
    for f in csv_features:
        p = f.get("properties", {})
        if type_selected:
            t = p.get("物件項目") or p.get("floor_plan_name_ja") or ""
            if t not in type_selected:
                continue
        age = p.get("築年数_成約時")
        if age is not None:
            try:
                age_f = float(age)
                if age_f < age_min or age_f > age_max:
                    continue
            except (TypeError, ValueError):
                pass
        if contract_period != "all":
            date_str = p.get("point_in_time_name_ja") or p.get("成約年月日") or ""
            dt = _parse_date_ymd(date_str)
            if dt is None:
                continue
            if contract_period == "1year":
                one_year_ago = datetime(now.year - 1, now.month, now.day)
                if dt < one_year_ago:
                    continue
            elif contract_period == "2years":
                two_years_ago = datetime(now.year - 2, now.month, now.day)
                if dt < two_years_ago:
                    continue
        filtered.append(f)
    return filtered


def get_unit_price(feature: Dict) -> Optional[float]:
    """取引データから㎡単価を取得"""
    p = feature.get("properties", {})
    total = parse_numeric(p.get("u_transaction_price_total_ja"))
    area = parse_numeric(p.get("u_area_ja")) or parse_numeric(p.get("u_building_total_floor_area_ja"))
    if total and area and area > 0:
        return total / area
    return None


# 中古戸建の建物減価償却：20年でゼロになる線形減価
DETACHED_DEPRECIATION_YEARS = 20
STANDARD_NEW_BUILDING_PRICE = 15_000_000  # 標準的な新築建物価格（万円）


def get_building_residual_rate_20y(age_years: Optional[float]) -> float:
    """築20年でゼロになる線形減価の残価率"""
    if age_years is None or age_years < 0:
        return 1.0
    if age_years >= DETACHED_DEPRECIATION_YEARS:
        return 0.0
    return max(0.0, (DETACHED_DEPRECIATION_YEARS - age_years) / DETACHED_DEPRECIATION_YEARS)


def compute_valuation(
    property_type: str,
    avg_unit_price: float,
    building_age_correction: float,
    land_area: float,
    building_area: float,
    exclusive_area: float,
    kakuti_rate: float = 0.0,
    subject_building_age: Optional[int] = None,
    csv_features: Optional[List[Dict]] = None,
    csv_features_2km: Optional[List[Dict]] = None,
    csv_features_2km_land: Optional[List[Dict]] = None,
) -> Tuple[float, Optional[float], Optional[float]]:
    """
    種別に応じた査定金額を算出。
    中古戸建は「土地単価×土地面積×画地補正＋建物評価額」で計算。
    戻り値: (査定額, 土地価格, 建物評価額)
    """
    if property_type == "土地":
        avg_with_markup = avg_unit_price * LAND_MARKUP_RATE
        land_val = land_area * avg_with_markup
        return land_val * (1.0 + kakuti_rate), land_val * (1.0 + kakuti_rate), None
    elif property_type == "中古住宅（戸建て）" and csv_features is not None:
        result = _compute_valuation_detached(
            csv_features, land_area, subject_building_age, kakuti_rate,
            csv_features_2km=csv_features_2km,
            csv_features_2km_land=csv_features_2km_land,
            avg_unit_price=avg_unit_price,
        )
        if result is not None:
            return result
    land_val = land_area * avg_unit_price
    bldg_val = building_area * avg_unit_price * building_age_correction if property_type == "中古住宅（戸建て）" else 0
    base = land_val + (bldg_val if property_type == "中古住宅（戸建て）" else exclusive_area * avg_unit_price * building_age_correction)
    if property_type == "中古住宅（戸建て）":
        return base * (1.0 + kakuti_rate), land_val * (1.0 + kakuti_rate), bldg_val * (1.0 + kakuti_rate)
    return base * (1.0 + kakuti_rate), None, None


def _compute_valuation_detached(
    csv_features: List[Dict],
    land_area: float,
    subject_building_age: Optional[int],
    kakuti_rate: float,
    csv_features_2km: Optional[List[Dict]] = None,
    csv_features_2km_land: Optional[List[Dict]] = None,
    avg_unit_price: Optional[float] = None,
) -> Optional[Tuple[float, float, float]]:
    """
    中古戸建の査定：
    ・昭和56年以前（築44年以上）: 建物評価0（リフォームされていても）
    ・築35年以上: 建物基本評価0、リフォーム等で変動
    ・築34年以下: 土地2km・売買価格差額で建物評価（土地の上乗せは廃止）
    """
    # 土地単価算出用データ（2km圏内の「土地」データがあればそれを優先、なければ築25年以上の中古戸建で代替）
    land_prices = []
    if csv_features_2km_land and subject_building_age is not None and subject_building_age <= 34:
        for f in csv_features_2km_land:
            p = f.get("properties", {})
            total = parse_numeric(p.get("u_transaction_price_total_ja"))
            land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
            if total and total > 0 and land_a and land_a > 0:
                land_prices.append(total / land_a)

    if not land_prices:
        land_data = csv_features_2km if (csv_features_2km and subject_building_age is not None and subject_building_age <= 34) else csv_features
        for f in land_data:
            p = f.get("properties", {})
            total = parse_numeric(p.get("u_transaction_price_total_ja"))
            land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
            age = p.get("築年数_成約時")
            age_f = float(age) if age is not None else None
            if not total or total <= 0 or not land_a or land_a <= 0:
                continue
            if age_f is not None and age_f >= 25:
                land_prices.append(total / land_a)

    if not land_prices:
        # 代替処理：築浅も含めた単価を出す
        fallback_data = csv_features_2km_land if csv_features_2km_land else (csv_features_2km if csv_features_2km else csv_features)
        for f in fallback_data:
            p = f.get("properties", {})
            total = parse_numeric(p.get("u_transaction_price_total_ja"))
            land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
            if total and total > 0 and land_a and land_a > 0:
                land_prices.append(total / land_a)

    if not land_prices:
        return None
    avg_land = _compute_robust_average(land_prices)
    if avg_land is None:
        return None
    land_value_base = land_area * avg_land * (1.0 + kakuti_rate)

    # 昭和56年以前（築44年以上）: 建物評価0
    if subject_building_age is not None and subject_building_age >= 44:
        return land_value_base, land_value_base, 0

    # 築35年以上: 建物基本評価0
    if subject_building_age is None or subject_building_age >= 35:
        return land_value_base, land_value_base, 0

    # 築34年以下: 2km圏内土地単価から算出した土地価格 + (売買価格 - 土地価格)の平均
    if csv_features_2km and subject_building_age is not None and subject_building_age <= 34:
        building_values = []
        for f in csv_features_2km:
            p = f.get("properties", {})
            total = parse_numeric(p.get("u_transaction_price_total_ja"))
            land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
            age = p.get("築年数_成約時")
            age_f = float(age) if age is not None else 0
            if not total or total <= 0 or not land_a or land_a <= 0:
                continue
            if age_f > 34:
                continue
            land_price_case = land_a * avg_land * (1.0 + kakuti_rate)
            bldg_val = total - land_price_case
            if bldg_val >= 0:
                building_values.append(bldg_val)
        if building_values:
            avg_building = _compute_robust_average(building_values)
            if avg_building is None:
                avg_building = 0
            land_value = land_value_base
            return land_value + avg_building, land_value, avg_building

    # フォールバック: 従来ロジック（築20年以下は残価率、築25年以上は0）
    if subject_building_age is None or subject_building_age >= 25:
        return land_value_base, land_value_base, 0
    residual = get_building_residual_rate_20y(float(subject_building_age))
    building_value = STANDARD_NEW_BUILDING_PRICE * residual
    land_value = land_value_base
    return land_value + building_value, land_value, building_value


def format_valuation_formula(
    property_type: str,
    valuation: float,
    avg_unit_price: float,
    building_age_correction: float,
    land_area: float,
    building_area: float,
    exclusive_area: float,
    kakuti_rate: float = 0.0,
    building_breakdown: Optional[float] = None,
    land_breakdown: Optional[float] = None,
) -> Tuple[str, str]:
    """LaTeX形式の算出式と数値入りの説明文を返す（画地補正含む）"""
    up = avg_unit_price / 10000
    val_man = valuation / 10000
    kakuti_pct = kakuti_rate * 100
    kakuti_str = f" × (1 + 画地補正{kakuti_pct:+.0f}%)" if kakuti_rate != 0 else ""

    if property_type == "土地":
        base_str = f"{land_area:.1f}㎡ × {up:.1f}万円/㎡"
        return (
            r"土地面積 \times ㎡単価 \times (1 + 画地補正) = 査定金額",
            f"{base_str}{kakuti_str} = {val_man:,.0f}万円",
        )
    if property_type == "中古住宅（戸建て）":
        building_val = building_breakdown if building_breakdown is not None else 0
        # 中古戸建は土地単価×土地面積×画地補正＋建物評価額で計算。土地単価はland_breakdownから逆算
        if land_breakdown is not None and land_area > 0:
            denom = land_area * (1.0 + kakuti_rate)
            land_unit_man = (land_breakdown / denom) / 10000 if denom > 0 else up
            if building_val == 0:
                base_str = f"{land_area:.1f}㎡×{land_unit_man:.1f}万円/㎡"
                return (
                    r"土地面積 \times 土地単価 \times (1 + 画地補正) = 査定金額",
                    f"{base_str}{kakuti_str} = {val_man:,.0f}万円",
                )
            bldg_man = building_val / 10000
            base_str = f"({land_area:.1f}㎡×{land_unit_man:.1f}万円/㎡){kakuti_str} + 建物{bldg_man:,.0f}万円"
            return (
                r"(土地面積 \times 土地単価 \times (1 + 画地補正)) + 建物評価額 = 査定金額",
                f"{base_str} = {val_man:,.0f}万円",
            )
        adj = (avg_unit_price * building_age_correction) / 10000
        if building_age_correction != 1.0:
            base_str = f"({land_area:.1f}×{up:.1f} + {building_area:.1f}×{adj:.1f})"
        else:
            base_str = f"({land_area:.1f}×{up:.1f} + {building_area:.1f}×{up:.1f})"
        return (
            r"(土地 + 建物) \times (1 + 画地補正) = 査定金額",
            f"{base_str}{kakuti_str} = {val_man:,.0f}万円",
        )
    adj = (avg_unit_price * building_age_correction) / 10000
    base_str = f"{exclusive_area:.1f}㎡ × {adj:.1f}万円/㎡"
    return (
        r"専有面積 \times ㎡単価 \times (1 + 画地補正) = 査定金額",
        f"{base_str}{kakuti_str} = {val_man:,.0f}万円",
    )


def get_corner_correction_rate(is_corner: bool) -> float:
    """角地・準角地の補正率（%）。ONなら+5%"""
    return 0.05 if is_corner else 0.0


def get_road_width_correction_rate(road_width_m: float) -> float:
    """道路幅員の補正率（%）
    4.0m未満:-10% / 4.0-6.0m:-5% / 6.0-8.0m:0% / 8.0m以上:+3%
    """
    if road_width_m < 4.0:
        return -0.10
    if road_width_m < 6.0:
        return -0.05
    if road_width_m < 8.0:
        return 0.0
    return 0.03


def get_frontage_correction_rate(frontage_m: float) -> float:
    """接道幅（間口）の補正率（%）
    4.0m未満:-15% / 4.0-8.0m:-5% / 8.0-15.0m:0% / 15.0m以上:+5%
    """
    if frontage_m < 4.0:
        return -0.15
    if frontage_m < 8.0:
        return -0.05
    if frontage_m < 15.0:
        return 0.0
    return 0.05


def get_building_age_correction_factor(building_age: Optional[int]) -> float:
    """築年数に応じた平米単価の補正係数を返す"""
    if building_age is None or building_age <= 0:
        return 1.0
    if building_age < 20:
        return 1.2
    if building_age < 30:
        return 1.0
    if building_age < 40:
        return 0.9
    return 0.8


def get_depreciation_advice(building_age: Optional[int], property_type: str) -> Optional[str]:
    """戸建ての築年数に応じた減価修正アドバイス"""
    if property_type != "中古住宅（戸建て）" or building_age is None:
        return None
    if building_age >= 44:
        return "⚠️ 昭和56年以前の建物は、評価は０（リフォームされていても）"
    if building_age >= 35:
        return "⚠️ 築35年以上の建物は、基本的な評価は０円となりますが、リフォーム・リノベーション等により建物評価が変わる可能性があります。"
    elif building_age >= 20:
        return "📌 築20年以上の建物は減価が進んでおり、建物価値は比較的低めに見積もられますが、リフォーム等により建物評価額が変わる可能性があります。"
    elif building_age >= 10:
        return "📌 築10〜20年は減価の進行がみられますが、建物価値は一定程度残っています。"
    return "📌 築10年未満は比較的減価が少なく、建物価値が残っています。"


def _abbreviate_zoning(zoning: str) -> str:
    """用途地域を略称に変換（PDF用・はみ出し防止）"""
    if zoning is None or (isinstance(zoning, float) and pd.isna(zoning)):
        return "-"
    s = str(zoning).strip()
    if not s or s == "-" or s.lower() == "nan":
        return "-"
    mapping = [
        ("第１種低層住居専用地域", "1低"),
        ("第1種低層住居専用地域", "1低"),
        ("第一種低層住居専用地域", "1低"),
        ("第２種低層住居専用地域", "2低"),
        ("第2種低層住居専用地域", "2低"),
        ("第二種低層住居専用地域", "2低"),
        ("低層住居専用地域", "1低"),
        ("田園住居地域", "田住"),
        ("第１種中高層住居専用地域", "1中高"),
        ("第1種中高層住居専用地域", "1中高"),
        ("第一種中高層住居専用地域", "1中高"),
        ("第２種中高層住居専用地域", "2中高"),
        ("第2種中高層住居専用地域", "2中高"),
        ("第二種中高層住居専用地域", "2中高"),
        ("第１種住居地域", "一住"),
        ("第1種住居地域", "一住"),
        ("第一種住居地域", "一住"),
        ("第２種住居地域", "二住"),
        ("第2種住居地域", "二住"),
        ("第二種住居地域", "二住"),
        ("準住居地域", "準住"),
        ("近隣商業地域", "近商"),
        ("商業地域", "商業"),
        ("工業専用地域", "工専"),
        ("準工業地域", "準工"),
        ("工業地域", "工業"),
    ]
    for full, abbr in mapping:
        if full in s:
            return abbr
    return s[:8] if len(s) > 8 else s


def _format_display_value(val: Any, is_numeric: bool = False, decimals: int = 1) -> str:
    """表示用に値をフォーマット"""
    if val is None or val == "" or str(val).strip() in ("-", "－", "―"):
        return "-"
    if is_numeric:
        try:
            return f"{float(val):,.{decimals}f}"
        except (ValueError, TypeError):
            return str(val)
    return str(val)


def build_csv_reference_table(
    csv_features: List[Dict],
    limit: int = MAX_REFERENCE_CASES,
    for_pdf: bool = False,
) -> pd.DataFrame:
    """CSV事例を11項目（築年数含む）含むDataFrameに変換。for_pdf=Trueで用途地域を略称に"""
    rows = []
    for i, f in enumerate(csv_features[:limit]):
        p = f.get("properties", {})
        unit_price = get_unit_price(f)
        area = parse_numeric(p.get("u_area_ja")) or parse_numeric(p.get("u_building_total_floor_area_ja"))
        total_price = parse_numeric(p.get("u_transaction_price_total_ja"))
        if unit_price is None and total_price and area and area > 0:
            unit_price = total_price / area
        land_a = parse_numeric(p.get("土地面積_数値")) if p.get("土地面積_数値") is not None else None
        bldg_a = parse_numeric(p.get("建物面積_数値")) or area
        excl_a = parse_numeric(p.get("専有面積_数値")) or area
        age_at_contract = p.get("築年数_成約時")
        age_str = f"{age_at_contract:.0f}年" if age_at_contract is not None else "-"
        zoning_val = _format_display_value(p.get("用途地域"))
        if for_pdf:
            zoning_val = _abbreviate_zoning(zoning_val)
        rows.append({
            "No.": i + 1,
            "所在地": _format_display_value(p.get("district_name_ja")),
            "成約価格(万円)": _format_display_value(total_price / 10000 if total_price else None, True),
            "成約年月日": _format_display_value(p.get("point_in_time_name_ja")),
            "築年数（成約時）": age_str,
            "物件項目": _format_display_value(p.get("floor_plan_name_ja")),
            "用途地域": zoning_val,
            "土地面積(㎡)": _format_display_value(land_a, True),
            "建物面積(㎡)": _format_display_value(bldg_a, True),
            "専有面積(㎡)": _format_display_value(excl_a, True),
            "接道状況": _format_display_value(p.get("接道状況")),
            "接道1": _format_display_value(p.get("接道1")),
            "㎡単価(万円/㎡)": _format_display_value(unit_price / 10000 if unit_price else None, True),
        })
    return pd.DataFrame(rows)


def _compute_robust_average(values: List[float]) -> Optional[float]:
    """外れ値を除外して平均値を算出する（四分位範囲：IQR を利用）"""
    if not values:
        return None
    if len(values) < 4:
        # データが少ない場合は単純平均
        return sum(values) / len(values)
        
    arr = np.array(values)
    q1 = np.percentile(arr, 25)
    q3 = np.percentile(arr, 75)
    iqr = q3 - q1
    lower_bound = q1 - (iqr * 1.5)
    upper_bound = q3 + (iqr * 1.5)
    
    # さらに極端に高い外れ値（中央値の2倍以上など）も念のためカット
    median_val = np.median(arr)
    if median_val > 0:
        upper_bound = min(upper_bound, median_val * 2.0)
        lower_bound = max(lower_bound, median_val * 0.2)
        
    filtered = [v for v in values if lower_bound <= v <= upper_bound]
    if not filtered:
        return sum(values) / len(values)
    return sum(filtered) / len(filtered)

def compute_avg_unit_price(csv_features: List[Dict]) -> Tuple[Optional[float], int]:
    """
    CSV事例から㎡単価の平均を算出。戻り値: (平均単価, 件数)
    外れ値（IQRなどを用いた堅牢な手法）は計算から除外する。
    """
    units = []
    for f in csv_features:
        up = get_unit_price(f)
        if up is not None and up > 0:
            units.append(up)
    if not units:
        return None, 0
    avg_price = _compute_robust_average(units)
    return avg_price, len(units)


def _format_date_for_display(val: Any) -> str:
    """成約日を YYYY/MM/DD 形式に変換"""
    if val is None or str(val).strip() in ("-", ""):
        return "-"
    s = str(val).strip()
    m = re.search(r"(\d{4})[/年.-](\d{1,2})[/月.-](\d{1,2})", s)
    if m:
        return f"{m.group(1)}/{m.group(2).zfill(2)}/{m.group(3).zfill(2)}"
    return s


def build_price_trend_chart(csv_features: List[Dict]) -> Optional[Any]:
    """価格推移グラフを生成（Plotly版・スマホ対応）"""
    rows = []
    for f in csv_features:
        p = f.get("properties", {})
        total = parse_numeric(p.get("u_transaction_price_total_ja"))
        area = parse_numeric(p.get("u_area_ja")) or parse_numeric(p.get("u_building_total_floor_area_ja"))
        if not total or not area or area <= 0:
            continue
        dt = _parse_date_ymd(p.get("point_in_time_name_ja") or p.get("成約年月日") or "")
        if dt:
            rows.append({"dt": dt, "unit_price": total / area})

    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("dt")

    # 万円表示で見やすく（不動産の㎡単価は通常1〜50万円/㎡程度）
    df = df.copy()
    df["unit_price_man"] = df["unit_price"] / 10000

    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["dt"],
        y=df["unit_price_man"],
        mode="markers",
        name="成約事例",
        marker=dict(color="#3498db", size=10, opacity=0.6),
    ))

    if len(df) >= 2:
        x_numeric = df["dt"].map(datetime.toordinal)
        z = np.polyfit(x_numeric, df["unit_price_man"], 1)
        poly = np.poly1d(z)
        fig.add_trace(go.Scatter(
            x=df["dt"],
            y=poly(x_numeric),
            mode="lines",
            name="トレンド",
            line=dict(color="#e74c3c", width=2),
        ))

    # スマホ向け：余白・フォント・高さを最適化
    fig.update_layout(
        title=dict(text="周辺の価格推移（過去3年間）", font=dict(size=16)),
        xaxis=dict(
            title="成約日",
            tickformat="%y/%m",
            tickangle=-30,
            tickfont=dict(size=11),
        ),
        yaxis=dict(
            title="㎡単価（万円/㎡）",
            tickformat=",.1f",
            tickfont=dict(size=11),
        ),
        margin=dict(l=55, r=30, t=50, b=60),
        height=320,
        autosize=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(248,249,250,0.8)",
    )

    return fig


def get_price_trend_analysis(csv_features: List[Dict]) -> Optional[str]:
    """
    直近1年と3年前の平均単価を比較し、分析コメントを生成。
    外れ値の影響を抑えるためIQR等を利用して外れ値を除外した上で平均値を使用する。
    """
    rows = []
    for f in csv_features:
        up = get_unit_price(f)
        if up is None or up <= 0:
            continue
        p = f.get("properties", {})
        date_str = p.get("point_in_time_name_ja") or p.get("成約年月日") or ""
        dt = _parse_date_ymd(date_str)
        if dt is None:
            continue
        rows.append({"dt": dt, "unit_price": up})
        
    if len(rows) < 2:
        return None
        
    df = pd.DataFrame(rows)
    # 外れ値除外（四分位範囲）
    units = df["unit_price"].tolist()
    if len(units) >= 4:
        arr = np.array(units)
        q1 = np.percentile(arr, 25)
        q3 = np.percentile(arr, 75)
        iqr = q3 - q1
        lower_bound = q1 - (iqr * 1.5)
        upper_bound = q3 + (iqr * 1.5)
        median_val = np.median(arr)
        if median_val > 0:
            upper_bound = min(upper_bound, median_val * 2.0)
            lower_bound = max(lower_bound, median_val * 0.2)
        df = df[(df["unit_price"] >= lower_bound) & (df["unit_price"] <= upper_bound)]
        
    if len(df) < 2:
        return None
        
    now = datetime.now()
    one_year_ago = datetime(now.year - 1, now.month, 1)
    two_years_ago = datetime(now.year - 2, now.month, 1)
    three_years_ago = datetime(now.year - 3, now.month, 1)
    
    recent = df[df["dt"] >= one_year_ago]["unit_price"]
    old = df[(df["dt"] >= three_years_ago) & (df["dt"] < two_years_ago)]["unit_price"]
    
    if len(recent) == 0 or len(old) == 0:
        return None
        
    recent_avg = recent.mean()
    old_avg = old.mean()
    if pd.isna(old_avg) or old_avg <= 0:
        return None
        
    pct = (recent_avg - old_avg) / old_avg * 100
    direction = "上昇" if pct > 0 else "下落"
    return f"直近1年間の平均単価は、3年前と比較して **{abs(pct):.1f}% {direction}** しています。（外れ値を除外し平均値で算出）"


def _build_marker_tooltip_html(feature: Dict) -> str:
    """featureからツールチップ・ポップアップ用のHTMLを生成"""
    p = feature.get("properties", {})
    addr = html.escape(str(p.get("district_name_ja") or p.get("所在地") or "-"))
    total = parse_numeric(p.get("u_transaction_price_total_ja"))
    price_str = f"{total/10000:,.0f}万円" if total else "-"
    land_a = parse_numeric(p.get("土地面積_数値"))
    bldg_a = parse_numeric(p.get("建物面積_数値"))
    land_str = f"{land_a:,.1f}" if land_a is not None else "-"
    bldg_str = f"{bldg_a:,.1f}" if bldg_a is not None else "-"
    zoning = html.escape(str(p.get("用途地域") or "-"))
    road_status = html.escape(str(p.get("接道状況") or "-"))
    road_width = str(p.get("接道1") or "-")
    road_str = f"{road_status}（{road_width}m）" if road_width and road_width != "-" else road_status
    date_str = _format_date_for_display(p.get("point_in_time_name_ja") or p.get("成約年月日"))
    age_str = f"{age_at_contract:.0f}年" if (age_at_contract := p.get("築年数_成約時")) is not None else "-"
    return (
        f"<b>{addr}</b><br>"
        f"成約価格: {price_str}<br>"
        f"土地面積: {land_str}㎡ / 建物面積: {bldg_str}㎡<br>"
        f"築年数: {age_str}<br>"
        f"用途地域: {zoning}<br>"
        f"接道: {road_str}<br>"
        f"成約日: {date_str}"
    )


def _get_marker_color_by_price(price: Optional[float]) -> str:
    """価格帯に応じたピン色を返す（低:緑 中:青 高:オレンジ）"""
    if price is None or price <= 0:
        return "#95a5a6"
    man = price / 10000
    if man < 1000:
        return "#27ae60"
    if man < 2000:
        return "#3498db"
    return "#e67e22"


def _get_marker_color_by_contract_date(feature: Dict) -> Tuple[str, str]:
    """成約時期に応じたピン色を返す（1年以内=濃い青、3年前=薄い青）"""
    p = feature.get("properties", {})
    date_str = p.get("point_in_time_name_ja") or p.get("成約年月日") or ""
    contract_dt = _parse_date_ymd(date_str)
    if contract_dt is None:
        return "#3498db", "#5dade2"
    now = datetime.now()
    delta_days = (now - contract_dt).days
    if delta_days <= 365:
        return "#1a5276", "#2980b9"
    if delta_days <= 730:
        return "#2471a3", "#3498db"
    return "#5dade2", "#85c1e9"


def build_folium_map(
    center_lat: float, center_lon: float,
    csv_features: List[Dict],
    search_address: str,
    zoom: int = 14,
):
    """Folium地図を構築（実務向け・ツールチップ・ポップアップ・検索・全画面対応）"""
    import folium
    import folium.plugins

    # CartoDB Voyager: 道路・街区が見やすいタイル
    tiles_url = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
    attr_carto = "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors &copy; <a href='https://carto.com/attributions'>CARTO</a>"

    # ズームレベルを上げて事例を視覚的に探しやすく（最低15）
    zoom_level = max(zoom, 15)

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom_level,
        tiles=None,
        control_scale=True,
    )

    # 通常の地図（CartoDB Voyager）
    folium.TileLayer(
        tiles=tiles_url,
        attr=attr_carto,
        name="地図",
        overlay=False,
        control=True,
    ).add_to(m)

    # 航空写真（Esri World Imagery）
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri, Maxar, Earthstar Geographics",
        name="航空写真",
        overlay=False,
        control=True,
    ).add_to(m)

    # 右上で地図/航空写真を切り替えるレイヤーコントロール
    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    # 住所検索バー（Geocoder）
    folium.plugins.Geocoder(
        collapsed=False,
        position="topleft",
        add_marker=True,
        zoom=zoom_level,
    ).add_to(m)

    # 全画面表示ボタン
    folium.plugins.Fullscreen(
        position="topright",
        title="全画面表示",
        title_cancel="終了",
        force_separate_button=True,
    ).add_to(m)

    # 査定対象地（中心点）: 赤色の大きな星型アイコン
    star_icon = folium.DivIcon(
        html='<div style="font-size: 28px; color: #e74c3c;">★</div>',
        icon_size=(28, 28),
        icon_anchor=(14, 14),
    )
    folium.Marker(
        [center_lat, center_lon],
        tooltip=folium.Tooltip(f"<b>査定対象地</b><br>{html.escape(search_address)}", sticky=True),
        popup=folium.Popup(f"<b>査定対象地</b><br>{html.escape(search_address)}", max_width=320),
        icon=star_icon,
    ).add_to(m)

    # 成約事例: 成約時期で色分け（1年以内=濃い青、3年前=薄い青）
    for f in csv_features:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if lon == 0 and lat == 0:
            continue
        html_content = _build_marker_tooltip_html(f)
        color, fill_color = _get_marker_color_by_contract_date(f)
        folium.CircleMarker(
            [lat, lon],
            radius=10,
            color=color,
            fill=True,
            fill_color=fill_color,
            fill_opacity=0.9,
            weight=2,
            tooltip=folium.Tooltip(html_content, sticky=True),
            popup=folium.Popup(html_content, max_width=340),
        ).add_to(m)
    return m


def build_map_dataframe(
    center_lat: float, center_lon: float,
    csv_features: List[Dict]
) -> pd.DataFrame:
    """地図表示用のDataFrameを構築（PDF用・検索住所 + CSV事例）"""
    rows = []
    rows.append({"lat": center_lat, "lon": center_lon, "type": "検索住所", "size": 80})
    for f in csv_features:
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates", [])
        if len(coords) >= 2:
            lon, lat = float(coords[0]), float(coords[1])
            if lon != 0 or lat != 0:
                rows.append({"lat": lat, "lon": lon, "type": "取引事例", "size": 50})
    return pd.DataFrame(rows)


def _plotly_fig_to_png(fig: Any) -> Optional[bytes]:
    """FigureをPNG画像のバイト列に変換（Matplotlib対応版）"""
    if fig is None:
        return None
    import io
    import matplotlib.pyplot as plt
    try:
        buf = io.BytesIO()
        # figがMatplotlibのFigureオブジェクトか確認して保存
        if hasattr(fig, "savefig"):
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        else:
            # 念のため古いPlotly形式も対応させておく
            buf.write(fig.to_image(format="png", scale=2))
        buf.seek(0)
        return buf.read()
    except Exception:
        return None


def _get_reportlab_japanese_font() -> str:
    """ReportLab用の日本語フォントを登録し、フォント名を返す"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    
    # --- 修正：GitHubにアップロードした ipaexg.ttf を使う設定を追加 ---
    font_file = Path(__file__).resolve().parent / "ipaexg.ttf"
    
    if font_file.exists():
        try:
            name = "JPFont"
            pdfmetrics.registerFont(TTFont(name, str(font_file)))
            return name
        except Exception:
            pass
    # -----------------------------------------------------------
    
    # 以下の既存のフォント探し（Windows用など）は予備として残します
    font_paths = [
        Path("C:/Windows/Fonts/meiryo.ttf"),
        Path("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"),
    ]
    for fp in font_paths:
        if fp.exists():
            try:
                name = "JPFont"
                pdfmetrics.registerFont(TTFont(name, str(fp)))
                return name
            except Exception:
                continue
    return "Helvetica"


def _create_map_image(map_df: pd.DataFrame) -> Optional[bytes]:
    """地図データをmatplotlibでプロットし、PNG画像のバイト列を返す"""
    if map_df is None or len(map_df) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        
        # --- 修正：matplotlib にも日本語フォントを教える ---
        from matplotlib import font_manager
        font_file = Path(__file__).resolve().parent / "ipaexg.ttf"
        if font_file.exists():
            font_prop = font_manager.FontProperties(fname=str(font_file))
            plt.rcParams['font.family'] = font_prop.get_name()
        # -----------------------------------------------

        fig, ax = plt.subplots(figsize=(6, 5))
        colors = {"検索住所": "#e74c3c", "取引事例": "#3498db"}
        for pt_type in map_df["type"].unique():
            subset = map_df[map_df["type"] == pt_type]
            c = colors.get(pt_type, "#95a5a6")
            ax.scatter(subset["lon"], subset["lat"], c=c, label=pt_type, s=30, alpha=0.7)
        ax.set_xlabel("経度")
        ax.set_ylabel("緯度")
        ax.legend(loc="upper right", fontsize=8)
        ax.set_title("周辺の取引事例の位置")
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.3)
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close()
        buf.seek(0)
        return buf.read()
    except Exception:
        return None


def generate_valuation_pdf(
    address: str,
    property_type: str,
    area_input: float,
    building_age: int,
    valuation: float,
    avg_unit_price: float,
    correction: float,
    adjusted_unit_price: float,
    transaction_count: int,
    df_reference: pd.DataFrame,
    map_df: Optional[pd.DataFrame],
    price_chart: Optional[Any] = None,
    **kwargs: Any,
) -> bytes:
    """査定報告書をPDFとして生成（A4・1〜2枚）"""
    try:
        return _generate_valuation_pdf_impl(
            address, property_type, area_input, building_age,
            valuation, avg_unit_price, correction, adjusted_unit_price,
            transaction_count, df_reference, map_df, price_chart, **kwargs
        )
    except Exception:
        return _generate_valuation_pdf_minimal(
            address, property_type, valuation, building_age, **kwargs
        )


def _generate_valuation_pdf_minimal(
    address: str, property_type: str, valuation: float, building_age: int, **kwargs: Any
) -> bytes:
    """フォールバック：最小限のPDFを生成（日本語フォント使用・横向き）"""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    font_name = _get_reportlab_japanese_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=8*mm, leftMargin=8*mm, topMargin=10*mm, bottomMargin=10*mm)
    t_style = ParagraphStyle(name="T", fontName=font_name, fontSize=18, alignment=1)
    s_style = ParagraphStyle(name="S", fontName=font_name, fontSize=9)
    b_style = ParagraphStyle(name="B", fontName=font_name, fontSize=10)
    val_style = ParagraphStyle(name="Val", fontName=font_name, fontSize=16)
    elements = []
    elements.append(Paragraph("不動産査定報告書（簡易）", t_style))
    elements.append(Paragraph(f"作成日: {datetime.now().strftime('%Y年%m月%d日')}", s_style))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(f"住所: {address}", b_style))
    elements.append(Paragraph(f"種別: {property_type}", b_style))
    elements.append(Paragraph(f"査定額: {valuation/10000:,.0f} 万円", val_style))
    doc.build(elements)
    buf.seek(0)
    return buf.read()


def _generate_valuation_pdf_impl(
    address: str,
    property_type: str,
    area_input: float,
    building_age: int,
    valuation: float,
    avg_unit_price: float,
    correction: float,
    adjusted_unit_price: float,
    transaction_count: int,
    df_reference: pd.DataFrame,
    map_df: Optional[pd.DataFrame],
    price_chart: Optional[Any],
    **kwargs: Any,
) -> bytes:
    """査定報告書のPDF本体（横向き・印刷レイアウト最適化）"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

    font_name = _get_reportlab_japanese_font()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=8*mm, leftMargin=8*mm, topMargin=10*mm, bottomMargin=10*mm)
    title_style = ParagraphStyle(name="Title", fontName=font_name, fontSize=16, spaceAfter=8, alignment=1)
    heading_style = ParagraphStyle(name="Heading", fontName=font_name, fontSize=11, spaceAfter=4)
    body_style = ParagraphStyle(name="Body", fontName=font_name, fontSize=9)
    small_style = ParagraphStyle(name="Small", fontName=font_name, fontSize=8, textColor=colors.grey)

    # 横向きA4: 余白縮小で有効幅 281mm（近隣事例リストを横に伸ばす）
    page_w = 297*mm - 16*mm  # 281mm

    elements = []
    elements.append(Paragraph("不動産査定報告書（簡易）", title_style))
    elements.append(Paragraph(f"作成日: {datetime.now().strftime('%Y年%m月%d日')}", small_style))
    elements.append(Spacer(1, 8))

    land_a = kwargs.get("land_area_input", area_input if property_type == "土地" else 0)
    bldg_a = kwargs.get("building_area_input", 0)
    excl_a = kwargs.get("exclusive_area_input", area_input if property_type == "中古マンション" else 0)
    if property_type == "土地":
        area_str = f"土地面積: {land_a:.1f}㎡"
    elif property_type == "中古住宅（戸建て）":
        area_str = f"土地: {land_a:.1f}㎡、建物: {bldg_a:.1f}㎡"
    else:
        area_str = f"専有面積: {excl_a:.1f}㎡"

    elements.append(Paragraph("■ 対象物件概要", heading_style))
    info_data = [
        ["住所", address],
        ["種別", property_type],
        ["面積", area_str],
        ["築年数", f"{building_age}年" if building_age > 0 else "未入力"],
    ]
    info_table = Table(info_data, colWidths=[28*mm, page_w - 28*mm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
        ("FONT", (0, 0), (-1, -1), font_name, 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 6))

    # 査定額とトレンドグラフを横並び（左:査定結果+画地補正、右:グラフ）
    val_man = valuation / 10000
    val_style = ParagraphStyle(
        name="Val",
        fontName=font_name,
        fontSize=18,
        textColor=colors.HexColor("#1a5276"),
        alignment=0,
        leftIndent=0,
        rightIndent=0,
        spaceBefore=4,
        spaceAfter=8,
    )
    left_cell_contents = [
        Paragraph("■ 査定結果", heading_style),
        Paragraph(f"査定額：{val_man:,.0f} 万円", val_style),
    ]
    building_breakdown = kwargs.get("building_breakdown")
    land_breakdown = kwargs.get("land_breakdown")
    if property_type == "中古住宅（戸建て）" and (land_breakdown is not None or building_breakdown is not None):
        ld = land_breakdown or 0
        bd = building_breakdown or 0
        breakdown_data = [
            ["内訳", "金額"],
            ["土地価格", f"{ld:,.0f}円"],
            ["建物評価", f"{bd:,.0f}円"],
        ]
        bd_table = Table(breakdown_data, colWidths=[30*mm, 35*mm])
        bd_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
            ("FONT", (0, 0), (-1, -1), font_name, 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        left_cell_contents.append(Spacer(1, 4))
        left_cell_contents.append(bd_table)
    kakuti_rate = kwargs.get("kakuti_rate", 0.0)
    corner_rate = get_corner_correction_rate(kwargs.get("corner_check", False))
    left_cell_contents.append(Spacer(1, 4))
    left_cell_contents.append(Paragraph("■ 画地補正の内訳", heading_style))
    kakuti_data = [
        ["項目", "適用率"],
        ["角地・準角地", f"{corner_rate*100:+.0f}%"],
        ["合計画地補正率", f"{kakuti_rate*100:+.0f}%"],
    ]
    kakuti_table = Table(kakuti_data, colWidths=[40*mm, 30*mm])
    kakuti_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3498db")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#fafafa")),
        ("FONT", (0, 0), (-1, -1), font_name, 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    left_cell_contents.append(kakuti_table)

    chart_img = _plotly_fig_to_png(price_chart) if price_chart is not None else None
    right_cell_contents = []
    if chart_img:
        right_cell_contents.append(Paragraph("■ 価格トレンドグラフ", heading_style))
        chart_w = page_w * 0.58  # 右側約58%
        chart_h = 55*mm
        right_cell_contents.append(Image(io.BytesIO(chart_img), width=chart_w, height=chart_h))
    else:
        right_cell_contents.append(Paragraph("■ 価格トレンドグラフ", heading_style))
        right_cell_contents.append(Paragraph("（データなし）", body_style))

    left_w = page_w * 0.40  # 左40%
    right_w = page_w * 0.60  # 右60%
    two_col_table = Table(
        [[left_cell_contents, right_cell_contents]],
        colWidths=[left_w, right_w]
    )
    two_col_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 5*mm),
        ("RIGHTPADDING", (0, 0), (0, -1), 4*mm),
        ("LEFTPADDING", (1, 0), (1, -1), 6*mm),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2*mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2*mm),
    ]))
    elements.append(two_col_table)
    elements.append(Spacer(1, 6))

    # 簡易版：近隣事例リストは非表示（HP用）

    doc.build(elements)
    buf.seek(0)
    return buf.read()


# ========== UI ==========
if "search_result" not in st.session_state:
    st.session_state.search_result = None
if "csv_cases" not in st.session_state:
    st.session_state.csv_cases = []
if "csv_df" not in st.session_state:
    st.session_state.csv_df = pd.DataFrame()

# 起動時にCSVを読み込み（latitude/longitude があれば座標計算スキップ）
try:
    csv_path = _ensure_reins_data_3years()
    csv_mtime = csv_path.stat().st_mtime if csv_path.exists() else 0.0
    with st.spinner("3年分のデータを解析中...（初回のみ時間がかかります）"):
        cases, csv_df = load_data(str(csv_path), csv_mtime)
        st.session_state.csv_cases = cases
        st.session_state.csv_df = csv_df
except Exception:
    st.session_state.csv_cases = []
    st.session_state.csv_df = pd.DataFrame()

with st.sidebar:
    st.markdown("### 📄 データソース")
    n_csv = len(st.session_state.csv_cases)
    st.info(f"**CSV（過去3年分）**: {n_csv} 件")
    if n_csv == 0:
        st.warning("data/reins_data_3years.csv が見つかりません。")
st.title("🏠 AI査定")
st.caption("スマホでも見やすいシンプルな査定フォームです")

st.markdown("""
<style>
    @media (max-width: 768px) { div[data-testid="column"] { min-width: 100% !important; } }
</style>
""", unsafe_allow_html=True)

st.markdown("**物件種別**")
property_type = st.radio(
    "種別",
    options=["土地", "中古住宅（戸建て）", "中古マンション"],
    horizontal=True,
    label_visibility="collapsed",
    key="property_type_selector",
)

# 面積・築年数はフォーム外に配置（フォーム内だと初回送信で値が正しく取得されないため）
if property_type == "土地":
    land_unit = st.radio("土地面積の単位", ["坪", "㎡"], horizontal=True, key="land_unit_tochi")
    if land_unit == "㎡":
        land_area_input = st.number_input("土地面積（㎡）", min_value=1.0, max_value=10000.0, value=100.0, step=1.0, key="land_area_tochi_m2")
    else:
        land_tsubo = st.number_input("土地面積（坪）", min_value=0.5, max_value=3500.0, value=30.0, step=0.5, key="land_area_tochi_tsubo")
        land_area_input = land_tsubo * M2_TO_TSUBO
    building_area_input = 0.0
    exclusive_area_input = 0.0
    building_age = 0
elif property_type == "中古住宅（戸建て）":
    land_unit = st.radio("土地面積の単位", ["坪", "㎡"], horizontal=True, key="land_unit_house")
    if land_unit == "㎡":
        land_area_input = st.number_input("土地面積（㎡）", min_value=0.0, max_value=10000.0, value=100.0, step=1.0, key="land_area_house_m2")
    else:
        land_tsubo = st.number_input("土地面積（坪）", min_value=0.0, max_value=3500.0, value=30.0, step=0.5, key="land_area_house_tsubo")
        land_area_input = land_tsubo * M2_TO_TSUBO

    bldg_unit = st.radio("建物延床面積の単位", ["坪", "㎡"], horizontal=True, key="bldg_unit_house")
    if bldg_unit == "㎡":
        building_area_input = st.number_input("建物延床面積（㎡）", min_value=1.0, max_value=1000.0, value=100.0, step=1.0, key="bldg_area_house_m2")
    else:
        bldg_tsubo = st.number_input("建物延床面積（坪）", min_value=0.5, max_value=300.0, value=30.0, step=0.5, key="bldg_area_house_tsubo")
        building_area_input = bldg_tsubo * M2_TO_TSUBO

    exclusive_area_input = 0.0
    building_age = st.number_input("築年数（年）", min_value=0, max_value=100, value=0, step=1, key="building_age_input")
else:
    land_area_input = 0.0
    building_area_input = 0.0
    exclusive_area_input = st.number_input("専有面積（㎡）", min_value=1.0, max_value=500.0, value=50.0, step=0.1, key="exclusive_area_mansion")
    building_age = st.number_input("築年数（年）", min_value=0, max_value=100, value=0, step=1, key="building_age_input")

# 住所入力（地図ボタンと連動するためフォーム外）
st.markdown("**住所**")
st.caption("住所がわからない場合は「地図で選択」ボタンで地図から選べます")
addr_col, map_col = st.columns([4, 1])
with addr_col:
    if st.session_state.get("address_from_map"):
        st.session_state["address_value"] = st.session_state.pop("address_from_map")
    if "address_value" not in st.session_state:
        st.session_state["address_value"] = ""
    address = st.text_input(
        "住所",
        value=st.session_state["address_value"],
        placeholder="例: 北海道旭川市神居一条18丁目",
        label_visibility="collapsed",
    )
    st.session_state["address_value"] = address
with map_col:
    map_btn = st.button("🗺️ 地図で選択", use_container_width=True)

if map_btn:
    if "show_map" not in st.session_state or not st.session_state.get("show_map"):
        st.session_state["show_map"] = True
    st.rerun()

if st.session_state.get("show_map"):
    with st.expander("地図で住所を選択（地図をクリックすると住所が自動入力されます）", expanded=True):
        import folium
        from streamlit_folium import st_folium
        m = folium.Map(location=[43.77, 142.36], zoom_start=12)
        map_data = st_folium(m, height=400, key="address_map")
        clicked = (map_data or {}).get("last_clicked")
        if clicked:
            lat = clicked.get("lat")
            lng = clicked.get("lng")
            if lat is not None and lng is not None:
                with st.spinner("住所を取得しています..."):
                    addr = reverse_geocode(lat, lng)
                if addr:
                    st.session_state["address_from_map"] = addr
                    st.session_state["show_map"] = False
                    st.success(f"住所を設定しました: {addr}")
                    st.rerun()
                else:
                    st.warning("この位置の住所を取得できませんでした。別の場所をクリックしてください。")
        if st.button("地図を閉じる"):
            st.session_state["show_map"] = False
            st.rerun()

with st.form("search_form"):
    # 旭川市内は半径2km、市外は半径5kmに固定
    radius_km = 2.0 if "旭川市" in (address or "") else 5.0
    st.info(f"💡 検索半径は自動で設定されます（旭川市内: 2km、市外: 5km / 今回は **{radius_km}km** で検索します）")

    st.caption(f"半径{radius_km}㎞の、過去3年の成約事例データを参考にしています。")

    corner_check = False  # 角地・準角地の補正は無効化

    st.markdown("---")
    st.markdown("**ご連絡情報の入力をお願いします。**")
    contact_name = st.text_input("お名前（必須）", placeholder="例: 山田 太郎")
    contact_phone = st.text_input("電話番号（必須）", placeholder="例: 090-1234-5678")
    contact_email = st.text_input("メールアドレス（必須）", placeholder="例: example@email.com")

    st.markdown("**個人情報の取り扱い（必須）**")
    st.markdown(
        '<a href="https://www.kyouei-asahikawa.com/privacy.html" target="_blank" rel="noopener noreferrer">『個人情報の取り扱い等について』</a>をお読みいただき、ご同意のうえ査定してください。',
        unsafe_allow_html=True,
    )
    privacy_agree = st.checkbox("同意する", value=False, key="privacy_agree")

    submitted = st.form_submit_button("査定を実行")

if submitted:
    if property_type == "土地":
        area_input = land_area_input
    elif property_type == "中古住宅（戸建て）":
        area_input = land_area_input + building_area_input
    else:
        area_input = exclusive_area_input

if st.button("結果をクリア"):
    st.session_state.search_result = None
    st.rerun()

# 結果表示エリア
if submitted:
    if not contact_name or not contact_name.strip():
        st.error("お名前（必須）を入力してください。")
    elif not contact_phone or not contact_phone.strip():
        st.error("電話番号（必須）を入力してください。")
    elif not contact_email or not contact_email.strip():
        st.error("メールアドレス（必須）を入力してください。")
    elif not privacy_agree:
        st.error("個人情報の取り扱いにご同意いただく必要があります。")
    elif not address or not address.strip():
        st.error("住所を入力してください。")
    elif not st.session_state.csv_cases:
        st.error("data/reins_data_3years.csv が読み込まれていません。")
    else:
        coords = geocode_address(address)
        if coords:
            lat, lon = coords
            st.success(f"住所を変換しました（緯度: {lat:.5f}, 経度: {lon:.5f}）")

            search_radius_m = float(radius_km) * 1000
            csv_raw = st.session_state.get("csv_cases", [])
            csv_df = st.session_state.get("csv_df")
            needs_geocode = any(c.get("lat") is None or c.get("lon") is None for c in csv_raw)
            if needs_geocode:
                st.info("新しい住所の座標を取得してCSVに保存しています... 次回からはこの処理はスキップされます")
            csv_features = filter_csv_by_distance(csv_raw, lat, lon, search_radius_m, csv_df=csv_df)

            if not csv_features:
                st.warning(f"半径{radius_km}km以内に取引事例が見つかりませんでした。別の住所でお試しください。")
                st.session_state.search_result = {
                    "has_valuation": False,
                    "address": address,
                    "lat": lat,
                    "lon": lon,
                    "property_type": property_type,
                    "radius_km": radius_km,
                    "csv_features": [],
                }
            else:
                # 簡易版：物件種別で同様事例を絞り込み、築年数±5年でフィルタ
                filter_type = PROPERTY_TYPE_TO_CSV_TYPE.get(property_type, [])
                age_center = int(building_age) if building_age is not None else 0
                filter_age_min = max(0, age_center - 5)
                filter_age_max = min(50, age_center + 5)
                if age_center == 0:
                    filter_age_min, filter_age_max = 0, 10
                filter_contract_value = "all"
                csv_filtered = apply_case_filters(csv_features, filter_type, filter_age_min, filter_age_max, filter_contract_value)
                total_count = len(csv_features)
                if not csv_filtered:
                    st.warning("条件に合う事例が見つかりません。フィルターを緩めてください。")
                    st.session_state.search_result = {
                        "has_valuation": False,
                        "address": address,
                        "lat": lat,
                        "lon": lon,
                        "property_type": property_type,
                        "radius_km": radius_km,
                        "csv_features": csv_features,
                        "land_area_input": land_area_input,
                        "building_area_input": building_area_input,
                        "exclusive_area_input": exclusive_area_input,
                        "building_age": building_age,
                        "corner_check": corner_check,
                    }
                else:
                    avg_unit_price, csv_count = compute_avg_unit_price(csv_filtered)
                    if avg_unit_price is None or avg_unit_price <= 0:
                        st.warning("㎡単価を算出できる取引データがありませんでした。")
                        st.session_state.search_result = {
                            "has_valuation": False,
                            "address": address,
                            "lat": lat,
                            "lon": lon,
                            "property_type": property_type,
                            "radius_km": radius_km,
                            "csv_features": csv_features,
                        }
                    else:
                        building_age_val = int(building_age) if building_age is not None and building_age > 0 else None
                        building_age_correction = 1.0 if property_type == "土地" else get_building_age_correction_factor(building_age_val)
                        corner_rate = get_corner_correction_rate(corner_check)
                        kakuti_rate = corner_rate
                        csv_2km = None
                        csv_2km_land = None
                        if property_type == "中古住宅（戸建て）" and building_age_val is not None and building_age_val <= 34:
                            csv_2km_raw = filter_csv_by_distance(st.session_state.csv_cases, lat, lon, 2000, csv_df=st.session_state.csv_df)
                            csv_2km = apply_case_filters(csv_2km_raw, filter_type, 0, 50, filter_contract_value)
                            csv_2km_land = apply_case_filters(csv_2km_raw, PROPERTY_TYPE_TO_CSV_TYPE.get("土地", []), 0, 50, filter_contract_value)
                        result = compute_valuation(
                            property_type, avg_unit_price, building_age_correction,
                            land_area_input, building_area_input, exclusive_area_input,
                            kakuti_rate=kakuti_rate,
                            subject_building_age=building_age_val if property_type == "中古住宅（戸建て）" else None,
                            csv_features=csv_filtered if property_type == "中古住宅（戸建て）" else None,
                            csv_features_2km=csv_2km,
                            csv_features_2km_land=csv_2km_land,
                        )
                        valuation = result[0]
                        land_breakdown = result[1]
                        building_breakdown = result[2]
                        adjusted_unit_price = avg_unit_price * building_age_correction

                        st.markdown("---")
                        st.markdown("### 📊 仮査定結果")
                        st.markdown(
                            f'<p style="font-size: 2.5rem; font-weight: bold; color: #1f77b4;">'
                            f'仮査定金額：<span style="font-size: 3rem;">{valuation/10000:,.0f}</span> 万円</p>',
                            unsafe_allow_html=True
                        )
                        price_chart = build_price_trend_chart(csv_filtered)
                        map_df = build_map_dataframe(lat, lon, csv_filtered)
                        df = build_csv_reference_table(csv_filtered, limit=MAX_REFERENCE_CASES)
                        df_pdf = build_csv_reference_table(csv_filtered, limit=MAX_REFERENCE_CASES, for_pdf=True)
                        pdf_bytes = None
                        try:
                            pdf_bytes = generate_valuation_pdf(
                                address=address,
                                property_type=property_type,
                                area_input=area_input,
                                building_age=int(building_age) if building_age > 0 else 0,
                                valuation=valuation,
                                avg_unit_price=avg_unit_price,
                                correction=building_age_correction,
                                adjusted_unit_price=adjusted_unit_price,
                                transaction_count=csv_count,
                                df_reference=df_pdf,
                                map_df=map_df,
                                price_chart=price_chart,
                                land_area_input=land_area_input,
                                building_area_input=building_area_input,
                                exclusive_area_input=exclusive_area_input,
                                building_breakdown=building_breakdown if property_type == "中古住宅（戸建て）" else None,
                                land_breakdown=land_breakdown if property_type == "中古住宅（戸建て）" else None,
                                kakuti_rate=kakuti_rate,
                                corner_check=corner_check,
                            )
                        except Exception as e:
                            st.warning(f"PDF生成中にエラーが発生しました: {e}")
                            pdf_bytes = b""
                        if pdf_bytes:
                            st.download_button(
                                label="📄 査定書をPDFでダウンロード",
                                data=pdf_bytes,
                                file_name=f"査定報告書_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                                mime="application/pdf",
                                type="primary",
                                key="pdf_download",
                            )
                        else:
                            st.info("PDFの生成に失敗しました。reportlab が正しくインストールされているか確認してください。")
                        # 土地の場合は成約ベースから20%上乗せで表示。
                        # 中古戸建の場合はそのまま（上乗せなし）
                        if property_type == "中古住宅（戸建て）" and land_breakdown is not None:
                            display_avg = (land_breakdown / land_area_input) / (1.0 + kakuti_rate) if land_area_input > 0 else 0
                            display_adj = display_avg
                        else:
                            _apply_markup = property_type == "土地"
                            display_avg = (avg_unit_price * LAND_MARKUP_RATE) if _apply_markup else avg_unit_price
                            display_adj = (adjusted_unit_price * LAND_MARKUP_RATE) if _apply_markup else adjusted_unit_price
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            tsubo_avg = (display_avg / 10000) * M2_TO_TSUBO
                            st.metric("㎡単価の平均", f"{display_avg/10000:,.1f} 万円/㎡", f"坪: {tsubo_avg:,.1f} 万円/坪")
                        with col2:
                            st.metric("築年数補正係数", f"{building_age_correction:.2f}")
                        with col3:
                            tsubo_adj = (display_adj / 10000) * M2_TO_TSUBO
                            st.metric("補正後㎡単価", f"{display_adj/10000:,.1f} 万円/㎡", f"坪: {tsubo_adj:,.1f} 万円/坪")
                        with col4:
                            st.metric("参考取引件数", f"{csv_count} 件")
                        count_msg = f"{total_count}件中 {csv_count}件を表示中" if total_count != csv_count else f"{csv_count}件"
                        st.caption(f"※ 半径{radius_km}㎞の、過去3年の成約事例データを参考にしています。（{count_msg}）")
                        if property_type == "土地":
                            st.caption("※ 成約ベースの価格から、㎡単価・坪単価に20%を上乗せしています。")

                        if kakuti_rate != 0:
                            st.markdown("**補正内訳（画地補正）**")
                            kakuti_rows = [
                                ("角地・準角地", corner_rate, f"{corner_rate*100:+.0f}%"),
                            ]
                            kakuti_df = pd.DataFrame(
                                [(n, r) for n, _, r in kakuti_rows],
                                columns=["項目", "補正率"]
                            )
                            st.dataframe(kakuti_df, use_container_width=True, hide_index=True)
                            st.caption(f"合計画地補正率: {kakuti_rate*100:+.0f}%")

                        latex_f, detail_f = format_valuation_formula(
                            property_type, valuation, display_avg, building_age_correction,
                            land_area_input, building_area_input, exclusive_area_input,
                            kakuti_rate=kakuti_rate,
                            building_breakdown=building_breakdown if property_type == "中古住宅（戸建て）" else None,
                            land_breakdown=land_breakdown if property_type == "中古住宅（戸建て）" else None,
                        )
                        st.markdown(f"**算出式**: ${latex_f}$")
                        st.caption(f"※ {detail_f}（参考値です）")

                        if property_type == "中古住宅（戸建て）" and land_breakdown is not None:
                            suffix = (
                                "（昭和56年以前のため評価0・リフォームされていても）"
                                if (building_breakdown or 0) == 0 and (building_age_val or 0) >= 44
                                else "（リフォーム等の状況により価格が変わる）"
                                if (building_breakdown or 0) == 0 and (building_age_val or 0) >= 35
                                else "（築25年以上のため古家付き土地）"
                                if (building_breakdown or 0) == 0
                                else f"（築{building_age_val or 0}年による減価後）"
                            )
                            st.markdown(
                                f"**算出根拠**: 土地価格：{land_breakdown:,.0f}円 ＋ "
                                f"建物評価：{building_breakdown or 0:,.0f}円{suffix}"
                            )

                        st.subheader("📈 価格トレンドグラフ")
                        if price_chart is not None:
                            st.plotly_chart(
                                price_chart,
                                use_container_width=True,
                                config=dict(
                                    displayModeBar=False,
                                    responsive=True,
                                    displaylogo=False,
                                    scrollZoom=False,
                                    staticPlot=True,
                                ),
                            )
                            trend_comment = get_price_trend_analysis(csv_filtered)
                            if trend_comment:
                                st.markdown(trend_comment)

                        if property_type == "中古住宅（戸建て）":
                            advice = get_depreciation_advice(building_age_val if building_age_val else None, property_type)
                            if advice:
                                st.warning(advice)

                        # お客様情報・査定結果を自動転送
                        payload = {
                            "送信日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "お客様情報": {
                                "お名前": contact_name.strip(),
                                "電話番号": contact_phone.strip(),
                                "メールアドレス": contact_email.strip(),
                            },
                            "物件情報": {
                                "住所": address,
                                "物件種別": property_type,
                                "土地面積（㎡）": land_area_input,
                                "建物面積（㎡）": building_area_input,
                                "専有面積（㎡）": exclusive_area_input,
                                "築年数（年）": int(building_age) if building_age is not None else 0,
                                "検索半径（km）": radius_km,
                                "角地・準角地": corner_check,
                            },
                            "査定結果": {
                                "仮査定金額（万円）": round(valuation / 10000, 0),
                                "㎡単価の平均（万円/㎡）": round(display_avg / 10000, 1),
                                "坪単価の平均（万円/坪）": round((display_avg / 10000) * M2_TO_TSUBO, 1),
                                "築年数補正係数": building_age_correction,
                                "参照事例数": csv_count,
                            },
                        }
                        ok, err = send_inquiry_to_webhook(payload)
                        if ok:
                            st.toast("お客様情報を送信しました。", icon="✅")
                        elif _get_webhook_url():
                            msg = f"送信に失敗しました。{err}" if err else "送信に失敗しました。"
                            st.error(msg)
                            with st.expander("🔧 トラブルシューティング"):
                                st.markdown("""
                                - **WEBHOOK_URL** が正しく設定されているか確認してください
                                - Google Chat の Webhook URL は `https://chat.googleapis.com/v1/spaces/...` で始まります
                                - Streamlit Cloud: Settings → Secrets で設定後、アプリが再起動するまで数十秒かかります
                                - ローカル: `.streamlit/secrets.toml` に `WEBHOOK_URL = "URL"` の形式で記載
                                """)

                        st.markdown("---")
                        st.markdown(
                            '<p style="text-align: center; font-size: 1rem; font-weight: bold; color: #1f77b4; '
                            'background: linear-gradient(135deg, #f0f8ff 0%, #e6f3ff 100%); padding: 16px; '
                            'border-radius: 8px; border-left: 4px solid #1f77b4;">'
                            '📞 詳しくはお問い合わせください<br>'
                            '<span style="font-size: 1.1rem;">株式会社　杏栄</span><br>'
                            '旭川市永山2条19丁目4－1　TEL: 0166－48－2349'
                            '</p>',
                            unsafe_allow_html=True,
                        )

                        st.session_state.search_result = {
                            "has_valuation": True,
                            "address": address,
                            "lat": lat,
                            "lon": lon,
                            "property_type": property_type,
                            "radius_km": radius_km,
                            "csv_features": csv_features,
                            "csv_count": csv_count,
                            "valuation": valuation,
                            "avg_unit_price": avg_unit_price,
                            "correction": building_age_correction,
                            "adjusted_unit_price": adjusted_unit_price,
                            "kakuti_rate": kakuti_rate,
                            "corner_check": corner_check,
                            "land_area_input": land_area_input,
                            "building_area_input": building_area_input,
                            "exclusive_area_input": exclusive_area_input,
                            "building_age": building_age,
                            "area_input": area_input,
                            "building_breakdown": building_breakdown if property_type == "中古住宅（戸建て）" else None,
                            "land_breakdown": land_breakdown if property_type == "中古住宅（戸建て）" else None,
                        }

elif st.session_state.search_result is not None:
    sr = st.session_state.search_result
    lat, lon = sr["lat"], sr["lon"]
    address = sr["address"]
    property_type = sr["property_type"]
    radius_km = sr["radius_km"]
    csv_features = sr.get("csv_features") or []

    filter_type = PROPERTY_TYPE_TO_CSV_TYPE.get(property_type, [])
    building_age_saved = sr.get("building_age", 0)
    age_center = int(building_age_saved) if building_age_saved is not None else 0
    filter_age_min = max(0, age_center - 5)
    filter_age_max = min(50, age_center + 5)
    if age_center == 0:
        filter_age_min, filter_age_max = 0, 10
    filter_contract_value = "all"
    csv_filtered = apply_case_filters(csv_features, filter_type, filter_age_min, filter_age_max, filter_contract_value)
    total_count = len(csv_features)

    if not csv_filtered:
        if not csv_features:
            st.info(f"前回の検索: {address}（半径{radius_km}km・{property_type}）— 取引事例0件")
        else:
            st.warning("条件に合う事例が見つかりません。フィルターを緩めてください。")
    else:
        land_a = sr.get("land_area_input", 0)
        bldg_a = sr.get("building_area_input", 0)
        excl_a = sr.get("exclusive_area_input", 0)
        kakuti_rate = sr.get("kakuti_rate", 0.0)
        corner_check = sr.get("corner_check", False)
        building_age = sr.get("building_age", 0)

        avg_unit_price, csv_count = compute_avg_unit_price(csv_filtered)
        if avg_unit_price is None or avg_unit_price <= 0:
            st.warning("条件に合う事例から㎡単価を算出できませんでした。")
        else:
            building_age_val = int(building_age) if building_age is not None and building_age > 0 else None
            correction = 1.0 if property_type == "土地" else get_building_age_correction_factor(building_age_val)
            adjusted_unit_price = avg_unit_price * correction
            csv_2km_prev = None
            if property_type == "中古住宅（戸建て）" and building_age_val is not None and building_age_val <= 34:
                csv_raw_prev = st.session_state.get("csv_cases", [])
                csv_df_prev = st.session_state.get("csv_df")
                csv_2km_raw = filter_csv_by_distance(csv_raw_prev, lat, lon, 2000, csv_df=csv_df_prev)
                csv_2km_prev = apply_case_filters(csv_2km_raw, filter_type, 0, 50, "all")
            result = compute_valuation(
                property_type, avg_unit_price, correction,
                land_a, bldg_a, excl_a,
                kakuti_rate=kakuti_rate,
                subject_building_age=building_age_val if property_type == "中古住宅（戸建て）" else None,
                csv_features=csv_filtered if property_type == "中古住宅（戸建て）" else None,
                csv_features_2km=csv_2km_prev,
            )
            valuation = result[0]
            land_breakdown = result[1]
            building_breakdown = result[2]

            st.markdown("---")
            st.markdown("### 📊 仮査定結果（前回の検索）")
            st.markdown(
                f'<p style="font-size: 2.5rem; font-weight: bold; color: #1f77b4;">'
                f'仮査定金額：<span style="font-size: 3rem;">{valuation/10000:,.0f}</span> 万円</p>',
                unsafe_allow_html=True
            )
            price_chart = build_price_trend_chart(csv_filtered)
            map_df = build_map_dataframe(lat, lon, csv_filtered)
            df = build_csv_reference_table(csv_filtered, limit=MAX_REFERENCE_CASES)
            df_pdf_prev = build_csv_reference_table(csv_filtered, limit=MAX_REFERENCE_CASES, for_pdf=True)
            pdf_bytes_prev = None
            try:
                area_input_val = land_a + bldg_a if property_type == "中古住宅（戸建て）" else (land_a if property_type == "土地" else excl_a)
                pdf_bytes_prev = generate_valuation_pdf(
                    address=address,
                    property_type=property_type,
                    area_input=area_input_val,
                    building_age=int(building_age) if building_age and building_age > 0 else 0,
                    valuation=valuation,
                    avg_unit_price=avg_unit_price,
                    correction=correction,
                    adjusted_unit_price=adjusted_unit_price,
                    transaction_count=csv_count,
                    df_reference=df_pdf_prev.head(5),
                    map_df=map_df,
                    price_chart=price_chart,
                    land_area_input=land_a,
                    building_area_input=bldg_a,
                    exclusive_area_input=excl_a,
                    building_breakdown=building_breakdown if property_type == "中古住宅（戸建て）" else None,
                    land_breakdown=land_breakdown if property_type == "中古住宅（戸建て）" else None,
                    kakuti_rate=kakuti_rate,
                    corner_check=corner_check,
                )
            except Exception as e:
                st.warning(f"PDF生成中にエラーが発生しました: {e}")
                pdf_bytes_prev = b""
            if pdf_bytes_prev:
                st.download_button(
                    label="📄 査定書をPDFでダウンロード",
                    data=pdf_bytes_prev,
                    file_name=f"査定報告書_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                    mime="application/pdf",
                    type="primary",
                    key="pdf_download_prev",
                )
            else:
                st.info("PDFの生成に失敗しました。reportlab が正しくインストールされているか確認してください。")
            if property_type == "中古住宅（戸建て）" and land_breakdown is not None:
                display_avg_prev = (land_breakdown / land_a) / (1.0 + kakuti_rate) if land_a > 0 else 0
                display_adj_prev = display_avg_prev
            else:
                _apply_markup_prev = property_type == "土地"
                display_avg_prev = (avg_unit_price * LAND_MARKUP_RATE) if _apply_markup_prev else avg_unit_price
                display_adj_prev = (adjusted_unit_price * LAND_MARKUP_RATE) if _apply_markup_prev else adjusted_unit_price
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                tsubo_avg = (display_avg_prev / 10000) * M2_TO_TSUBO
                st.metric("㎡単価の平均", f"{display_avg_prev/10000:,.1f} 万円/㎡", f"坪: {tsubo_avg:,.1f} 万円/坪")
            with col2:
                st.metric("築年数補正係数", f"{correction:.2f}")
            with col3:
                tsubo_adj = (display_adj_prev / 10000) * M2_TO_TSUBO
                st.metric("補正後㎡単価", f"{display_adj_prev/10000:,.1f} 万円/㎡", f"坪: {tsubo_adj:,.1f} 万円/坪")
            with col4:
                st.metric("参考取引件数", f"{csv_count} 件")
            count_msg = f"{total_count}件中 {csv_count}件を表示中" if total_count != csv_count else f"{csv_count}件"
            st.caption(f"※ 半径{radius_km}㎞の、過去3年の成約事例データを参考にしています。（{count_msg}、住所: {address}）")
            if property_type == "土地":
                st.caption("※ 成約ベースの価格から、㎡単価・坪単価に20%を上乗せしています。")

            if kakuti_rate != 0:
                corner_rate = get_corner_correction_rate(corner_check)
                st.markdown("**補正内訳（画地補正）**")
                kakuti_rows = [
                    ("角地・準角地", f"{corner_rate*100:+.0f}%"),
                ]
                kakuti_df = pd.DataFrame(kakuti_rows, columns=["項目", "補正率"])
                st.dataframe(kakuti_df, use_container_width=True, hide_index=True)
                st.caption(f"合計画地補正率: {kakuti_rate*100:+.0f}%")

            latex_f, detail_f = format_valuation_formula(
                property_type, valuation, display_avg_prev, correction,
                land_a, bldg_a, excl_a,
                kakuti_rate=kakuti_rate,
                building_breakdown=building_breakdown if property_type == "中古住宅（戸建て）" else None,
                land_breakdown=land_breakdown if property_type == "中古住宅（戸建て）" else None,
            )
            st.markdown(f"**算出式**: ${latex_f}$")
            st.caption(f"※ {detail_f}（参考値です）")

            if property_type == "中古住宅（戸建て）" and land_breakdown is not None:
                suffix_prev = (
                    "（昭和56年以前のため評価0・リフォームされていても）"
                    if (building_breakdown or 0) == 0 and (building_age_val or 0) >= 44
                    else "（リフォーム等の状況により価格が変わる）"
                    if (building_breakdown or 0) == 0 and (building_age_val or 0) >= 35
                    else "（築25年以上のため古家付き土地）"
                    if (building_breakdown or 0) == 0
                    else f"（築{building_age_val or 0}年による減価後）"
                )
                st.markdown(
                    f"**算出根拠**: 土地価格：{land_breakdown:,.0f}円 ＋ "
                    f"建物評価：{building_breakdown or 0:,.0f}円{suffix_prev}"
                )
            if property_type == "中古住宅（戸建て）":
                advice = get_depreciation_advice(building_age_val, property_type)
                if advice:
                    st.warning(advice)

            st.subheader("📈 価格トレンドグラフ")
            if price_chart is not None:
                st.plotly_chart(
                    price_chart,
                    use_container_width=True,
                    config=dict(
                        displayModeBar=False,
                        responsive=True,
                        displaylogo=False,
                        scrollZoom=False,
                        staticPlot=True,
                    ),
                )
                trend_comment = get_price_trend_analysis(csv_filtered)
                if trend_comment:
                    st.markdown(trend_comment)

            st.markdown("---")
            st.markdown(
                '<p style="text-align: center; font-size: 1rem; font-weight: bold; color: #1f77b4; '
                'background: linear-gradient(135deg, #f0f8ff 0%, #e6f3ff 100%); padding: 16px; '
                'border-radius: 8px; border-left: 4px solid #1f77b4;">'
                '📞 詳しくはお問い合わせください<br>'
                '<span style="font-size: 1.1rem;">株式会社　杏栄</span><br>'
                '旭川市永山2条19丁目4－1　TEL: 0166－48－2349'
                '</p>',
                unsafe_allow_html=True,
            )

