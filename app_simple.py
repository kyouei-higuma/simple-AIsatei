"""
簡易AI査定アプリ（お客様向け・HP用）
社員向けの本番査定は main.py（地図・成約事例一覧あり＝対面説明用）。
本ファイルはお客様が手軽に使う版のため、画面上では地図・参照成約事例の一覧は出さない。
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

# data/ フォルダのパス（成約CSV。査定では過去5年分を参照）
DATA_DIR = Path(__file__).resolve().parent / "data"
CSV_PATH_3YEARS = DATA_DIR / "seiyaku_20260321_10year_date.csv"
CSV_PATH_LEGACY = DATA_DIR / "reins_data.csv"

# ページ設定
st.set_page_config(
    page_title="AI査定",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Streamlit全体の余白を極限まで削るCSS
st.markdown("""
<style>
/* 1. 全体のコンテナ余白をゼロにする */
.block-container {
    padding-top: 0rem !important;
    padding-bottom: 0rem !important;
    margin-top: 0 !important;
}
/* 2. ツールバーとヘッダーを非表示 */
[data-testid="stToolbar"], [data-testid="stHeader"], footer { display: none !important; }
.stDeployButton, #stDecoration { display: none !important; }
/* 3. 各要素間の下マージンを最小限にする */
[data-testid="element-container"] { margin-bottom: 0.2rem !important; }
</style>
""", unsafe_allow_html=True)

# 翻訳プロンプト抑制（※Streamlit Community Cloud のブランディングはプラットフォーム制限により非表示不可。セルフホスティングで解消。docs/セルフホスティング手順.md 参照）
st.components.v1.html("""
<script>
(function(){
  function run(doc) {
    try {
      if (!doc || !doc.documentElement) return;
      var d = doc.documentElement;
      d.setAttribute('translate', 'no');
      d.setAttribute('lang', 'ja');
      d.classList.add('notranslate');
      var m = doc.querySelector('meta[name="google"]');
      if (!m) {
        m = doc.createElement('meta');
        m.name = 'google';
        m.content = 'notranslate';
        if (doc.head) doc.head.appendChild(m);
      }
    } catch(e) {}
  }
  run(document);
  var w = window;
  while (w && w !== w.top) {
    try { run(w.document); } catch(e) {}
    w = w.parent;
  }
  if (w && w !== window) run(w.document);
})();
</script>
""", height=0)

# 定数
MAX_REFERENCE_CASES = 20
M2_TO_TSUBO = 3.30578  # 1坪 = 3.30578㎡（坪単価換算用）
LAND_MARKUP_RATE = 1.20  # 土地単価の20%上乗せ（成約ベースの補正）
# 建物付き土地等で平均より著しく低い成約総額を単価算出から除外（円）
LAND_UNDERPRICE_VS_MEAN_YEN = 2_000_000

# Webhook転送用（環境変数 WEBHOOK_URL または Streamlit Secrets で設定）
def _get_webhook_url() -> Optional[str]:
    """転送先URLを取得（一時停止中：再開時は下の return None をやめ、Secrets/環境変数から返す）"""
    return None
    # try:
    #     if hasattr(st, "secrets") and st.secrets.get("WEBHOOK_URL"):
    #         u = str(st.secrets["WEBHOOK_URL"]).strip()
    #         if u:
    #             return u
    # except Exception:
    #     pass
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
        f"坪単価: *{result.get('坪単価の平均（万円/坪）', '-')}万円/坪*（㎡: {result.get('㎡単価の平均（万円/㎡）', '-')}万円/㎡） / 参照: {result.get('参照事例数', '-')}件",
    ]
    z = result.get("土地ボリュームゾーン")
    if z:
        lines.append(f"土地ボリュームゾーン: {z}")
    bz = result.get("建物ボリュームゾーン")
    if bz:
        lines.append(f"建物ボリュームゾーン: {bz}")
    lines.extend([
        f"",
        f"送信日時: {payload.get('送信日時', '-')}",
    ])
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
    "中古住宅（戸建て）": ["中古戸建", "既存住宅", "中古住宅", "一戸建"],
    "中古マンション": ["中古マンション", "既存ＭＳ", "マンション", "既存マンション"],
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


def _haversine_m_np(
    center_lon: float,
    center_lat: float,
    lon_arr: np.ndarray,
    lat_arr: np.ndarray,
) -> np.ndarray:
    """中心点から各点までの距離（m）。lon_arr / lat_arr は同長の1次元配列。"""
    R = 6371000.0
    phi1 = math.radians(center_lat)
    phi2 = np.radians(lat_arr.astype(np.float64, copy=False))
    dphi = np.radians(lat_arr.astype(np.float64, copy=False) - center_lat)
    dlambda = np.radians(lon_arr.astype(np.float64, copy=False) - center_lon)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * (np.sin(dlambda / 2.0) ** 2)
    a = np.clip(a, 0.0, 1.0)
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
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
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(" ", "").replace("㎡", "").replace("m²", "").strip()
    if not s: return None
    
    # "30.5坪" のような表記に対応
    is_tsubo = "坪" in s
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            val = float(m.group(1))
            if is_tsubo:
                val = val / 0.3025  # 坪を㎡に変換（1坪 ≒ 3.30578㎡）
            return val
        except ValueError:
            return None
    return None


def _parse_price_man(value: Any) -> Optional[int]:
    """価格文字列（例: '320万円'）を数値に変換"""
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, (int, float)):
        # 数値の場合は、1億以下なら万円単位とみなすロジックがあったが、
        # 新しいCSVは生の円単位（14,000,000など）なので、
        # 10万以上なら円、10万未満なら万円とみなす
        v = float(value)
        if v >= 100000:
            return int(v)
        return int(v * 10000)
    
    s = str(value).replace(",", "").strip()
    if not s: return None
    
    is_man = "万円" in s or "万" in s
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            val = float(m.group(1))
            if is_man:
                val *= 10000
            elif val < 100000:
                # 単位がなく、かつ数値が小さい場合は万円単位と推測
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
    """建築年月を datetime に変換。YYYY/MM、Mon-YY（例: Nov-75）、YYYY.0（年のみ）に対応"""
    if cy_val is None or (isinstance(cy_val, float) and pd.isna(cy_val)):
        return None
    s = str(cy_val).strip()
    if not s:
        return None
    
    # float型（例: 1984.0）の場合は年のみとして処理
    m_float = re.match(r"^(\d{4})\.0$", s)
    if m_float:
        try:
            return datetime(int(m_float.group(1)), 1, 1)
        except ValueError:
            return None
            
    m = re.search(r"(\d{4})[/年.-](\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        try:
            # ".0" がマッチしてしまった場合（上でも弾くが念のため）
            if mo == 0:
                mo = 1
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
    """取引事例CSVパスの取得"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    CSVデータを読み込み、築年数計算・クレンジングを行う。
    BOM対策や列名のゆらぎ吸収を強化。
    """
    if not csv_path or csv_mtime <= 0:
        return [], pd.DataFrame()
    path = Path(csv_path)
    if not path.exists():
        return [], pd.DataFrame()
    
    df = None
    encodings = ["utf-8-sig", "utf-8", "cp932"]
    for enc in encodings:
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except Exception:
            continue
            
    if df is None or df.empty:
        return [], pd.DataFrame()

    # 列名のクリーニング（BOM除去、空白除去）
    df.columns = [str(c).strip().replace('\ufeff', '') for c in df.columns]
    
    if "latitude" not in df.columns:
        df["latitude"] = np.nan
    if "longitude" not in df.columns:
        df["longitude"] = np.nan
        
    cases = []
    for idx, row in df.iterrows():
        case = _load_case_from_row(row, df.columns, idx)
        if not case.get("所在地"):
            continue
        
        lat = row.get("latitude")
        lon = row.get("longitude")
        if _is_valid_coord(lat) and _is_valid_coord(lon):
            case["lat"] = float(lat)
            case["lon"] = float(lon)
            
        case["_df_index"] = idx
        cases.append(case)
        
    return cases, df


def _load_case_from_row(row: pd.Series, columns: Any, df_index: int) -> Dict[str, Any]:
    """DataFrameの1行からcase辞書を構築（列名の柔軟なマッチング）"""
    # 列名の対応マップ
    col_map = {
        "address": ["address", "所在地", "住所"],
        "price": ["contract_price", "price", "成約価格", "成約価格_円", "価格"],
        "date": ["contract_date", "成約日", "成約年月日", "point_in_time_name_ja"],
        "type": ["type", "物件項目", "物件種別", "floor_plan_name_ja"],
        "zoning": ["zoning", "用途地域"],
        "land_area": ["land_area", "土地面積", "土地面積_数値", "u_area_ja"],
        "building_area": ["building_area", "建物面積", "建物面積_数値", "u_building_total_floor_area_ja"],
        "floor_area": ["floor_area", "専有面積", "専有面積_数値", "u_area_ja"],
        "const_year": ["construction_year", "築年数", "建築年", "建築年月", "u_construction_year_ja"],
        "floor_plan": ["floor_plan", "間取り"],
        "road_status": ["road_status", "接道状況"],
        "road_width": ["road_width", "接道幅", "接道1", "road_width_m"]
    }
    
    def get_val(keys):
        for k in keys:
            if k in columns and pd.notna(row[k]):
                return row[k]
        return None

    addr = str(get_val(col_map["address"]) or "").strip()
    price_raw = get_val(col_map["price"])
    price = _parse_price_man(price_raw)
    
    date_raw = get_val(col_map["date"])
    contract_dt = _parse_date_ymd(date_raw)
    
    # 建築年月/築年数の処理
    age_at_contract = None
    const_val = get_val(col_map["const_year"])
    construction_dt = _parse_construction_date(const_val)
    
    if contract_dt and construction_dt:
        delta = contract_dt - construction_dt
        age_at_contract = max(0, delta.days / 365.25)
    elif pd.notna(const_val):
        # 数値（築年数そのもの）が入っている場合の処理
        try:
            val = float(str(const_val).replace("年", "").strip())
            if val < 150: # 築年数として妥当な数字なら採用
                age_at_contract = val
        except:
            pass
        
    zoning_raw = str(get_val(col_map["zoning"]) or "")
    if " / " in zoning_raw:
        zoning_raw = zoning_raw.split(" / ")[-1]
        
    return {
        "所在地": addr if addr and addr.lower() not in ("nan", "none") else None,
        "成約価格_円": price,
        "成約年月日": str(date_raw or ""),
        "物件項目": str(get_val(col_map["type"]) or ""),
        "用途地域": zoning_raw,
        "土地面積_数値": _parse_area_to_sqm(get_val(col_map["land_area"])),
        "建物面積_数値": _parse_area_to_sqm(get_val(col_map["building_area"])),
        "専有面積_数値": _parse_area_to_sqm(get_val(col_map["floor_area"])),
        "間取り": str(get_val(col_map["floor_plan"]) or ""),
        "接道状況": str(get_val(col_map["road_status"]) or ""),
        "接道1": str(get_val(col_map["road_width"]) or ""),
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
    elif df is not None and row.get("_df_index") is not None:
        try:
            la = df.at[row["_df_index"], "latitude"]
            lo = df.at[row["_df_index"], "longitude"]
            if _is_valid_coord(la) and _is_valid_coord(lo):
                lat, lon = float(la), float(lo)
                row["lat"], row["lon"] = lat, lon
        except Exception:
            pass

    if (row.get("lat") is None or row.get("lon") is None) and address:
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


def _build_radius_hint_from_df(
    csv_df: pd.DataFrame,
    center_lat: float,
    center_lon: float,
    radius_m: float,
) -> Dict[Any, Optional[bool]]:
    """
    DataFrame の latitude/longitude から、各行が半径内かを事前判定。
    戻り値: インデックスラベル -> True=圏内, False=圏外（座標あり）, None=座標なし（要ジオコーディングの可能性）
    """
    hint: Dict[Any, Optional[bool]] = {}
    if csv_df is None or csv_df.empty:
        return hint
    if "latitude" not in csv_df.columns or "longitude" not in csv_df.columns:
        return hint
    lat_a = pd.to_numeric(csv_df["latitude"], errors="coerce").to_numpy(dtype=np.float64)
    lon_a = pd.to_numeric(csv_df["longitude"], errors="coerce").to_numpy(dtype=np.float64)
    n = len(csv_df)
    valid = np.isfinite(lat_a) & np.isfinite(lon_a)
    dist = np.full(n, np.inf, dtype=np.float64)
    if np.any(valid):
        dist[valid] = _haversine_m_np(center_lon, center_lat, lon_a[valid], lat_a[valid])
    for i in range(n):
        lab = csv_df.index[i]
        if not valid[i]:
            hint[lab] = None
        else:
            hint[lab] = bool(dist[i] <= radius_m)
    return hint


def filter_features_by_distance(
    features: List[Dict],
    center_lat: float,
    center_lon: float,
    radius_m: float,
) -> List[Dict]:
    """既に feature 化済みの事例から、中心から radius_m 以内だけを抽出（全件CSV再走査なし）。"""
    out: List[Dict] = []
    for feat in features:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        plon, plat = float(coords[0]), float(coords[1])
        if plon == 0.0 and plat == 0.0:
            continue
        if haversine_distance(center_lon, center_lat, plon, plat) <= radius_m:
            out.append(feat)
    return out


# 1回の距離フィルタあたり、座標欠損行に対するジオコーディング上限（大量CSVでフリーズしないため）
_MAX_GEOCODE_PER_DISTANCE_FILTER = 800


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
    DataFrame に緯度経度がある行はベクトル距離で圏外を即スキップ（ジオコーディングしない）。
    座標がない行のみジオコーディング。件数上限あり。10件ごとにCSV保存。
    """
    SAVE_INTERVAL = 10
    radius_hint = _build_radius_hint_from_df(csv_df, center_lat, center_lon, radius_m) if csv_df is not None else {}
    geocode_budget = _MAX_GEOCODE_PER_DISTANCE_FILTER
    features: List[Dict] = []
    geocode_count = 0

    for row in csv_cases:
        idx = row.get("_df_index")
        hint = radius_hint.get(idx) if radius_hint else None

        if hint is False:
            continue

        if hint is True and csv_df is not None and idx is not None:
            try:
                la = csv_df.at[idx, "latitude"]
                lo = csv_df.at[idx, "longitude"]
                if _is_valid_coord(la) and _is_valid_coord(lo):
                    row["lat"] = float(la)
                    row["lon"] = float(lo)
            except Exception:
                pass

        if hint is None and geocode_budget <= 0:
            if row.get("lat") is None or row.get("lon") is None:
                continue

        feat, needs_save = csv_row_to_feature(row, center_lon, center_lat, csv_df)
        if needs_save:
            geocode_count += 1
            geocode_budget -= 1
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
    contract_period: "1year" | "2years" | "3years" | "5years" | "all"
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
            elif contract_period == "3years":
                three_years_ago = datetime(now.year - 3, now.month, now.day)
                if dt < three_years_ago:
                    continue
            elif contract_period == "5years":
                five_years_ago = datetime(now.year - 5, now.month, now.day)
                if dt < five_years_ago:
                    continue
        filtered.append(f)
    return filtered


def _deal_area_sqm_for_unit_price(p: Dict[str, Any]) -> Optional[float]:
    """
    成約総額を割る面積（㎡）。
    土地と建物延床の両方が取れるときは (土地+延床) を使い、建物単価を土地㎡だけで割った過大な㎡単価にならないようにする。
    """
    land = parse_numeric(p.get("土地面積_数値"))
    bldg = parse_numeric(p.get("建物面積_数値")) or parse_numeric(p.get("u_building_total_floor_area_ja"))
    if land is not None and bldg is not None and land > 0 and bldg > 0:
        return float(land + bldg)
    if land is not None and land > 0:
        return float(land)
    if bldg is not None and bldg > 0:
        return float(bldg)
    u = parse_numeric(p.get("u_area_ja"))
    if u is not None and u > 0:
        return float(u)
    return None


def get_unit_price(feature: Dict) -> Optional[float]:
    """取引データから㎡単価を取得（戸建は原則 成約総額÷(土地㎡+延床㎡)）"""
    p = feature.get("properties", {})
    total = parse_numeric(p.get("u_transaction_price_total_ja"))
    area = _deal_area_sqm_for_unit_price(p)
    if total and area and area > 0:
        return total / area
    return None


# 中古戸建の建物減価償却：20年でゼロになる線形減価
DETACHED_DEPRECIATION_YEARS = 20
STANDARD_NEW_BUILDING_PRICE = 15_000_000  # 標準的な新築建物価格（万円）

# 建物坪単価の参考（円/坪）：帯ごとのボリュームゾーンと中央値。帯内は一定、帯をまたぐと段階的に変化
_BUILDING_TSUBO_REF_BANDS: List[Dict[str, Any]] = [
    {"a1": 1, "a2": 3, "lo": 544_000, "hi": 734_000, "med": 616_000, "label": "築2～3年帯"},
    {"a1": 4, "a2": 5, "lo": 458_000, "hi": 665_000, "med": 569_000, "label": "築4～5年帯"},
    {"a1": 6, "a2": 10, "lo": 381_000, "hi": 531_000, "med": 449_000, "label": "築6～10年帯"},
    {"a1": 11, "a2": 15, "lo": 290_000, "hi": 412_000, "med": 348_000, "label": "築11～15年帯"},
    {"a1": 16, "a2": 20, "lo": 202_000, "hi": 334_000, "med": 275_000, "label": "築16～20年帯"},
]
_REF_TSUBO_BASELINE_YEN = 616_000.0
# 築21年以降：築11～15年帯と16～20年帯の中央値差を年換算した傾きで外挿
_BUILDING_TSUBO_EXTRAP_SLOPE = (275_000.0 - 348_000.0) / (20.0 - 15.0)
_BUILDING_TSUBO_EXTRAP_FLOOR = 40_000.0


def _lookup_building_tsubo_band(age_years: int) -> Optional[Dict[str, Any]]:
    if age_years <= 0:
        return None
    for b in _BUILDING_TSUBO_REF_BANDS:
        if b["a1"] <= age_years <= b["a2"]:
            return b
    return None


def _median_tsubo_yen_for_age(age_years: float) -> float:
    """参考テーブルに沿った坪単価中央値（円/坪）。帯内は一定、築21年以降は外挿"""
    if age_years <= 0:
        return _REF_TSUBO_BASELINE_YEN
    a = max(0, int(round(age_years)))
    band = _lookup_building_tsubo_band(a)
    if band is not None:
        return float(band["med"])
    y = 275_000.0 + (float(a) - 20.0) * _BUILDING_TSUBO_EXTRAP_SLOPE
    return max(_BUILDING_TSUBO_EXTRAP_FLOOR, y)


def format_building_volume_zone_caption(building_age: Optional[int]) -> Optional[str]:
    """築年数に対応する建物坪単価の参考ボリュームゾーン文言（戸建・マンション表示用）"""
    if building_age is None or building_age <= 0:
        return None
    a = int(building_age)
    b = _lookup_building_tsubo_band(a)
    if b is not None:
        return (
            f"建物坪単価の参考ボリュームゾーン：{b['lo']:,.0f}円/坪～{b['hi']:,.0f}円/坪、"
            f"中央値{b['med']:,.0f}円/坪（{b['label']}）"
        )
    med = int(round(_median_tsubo_yen_for_age(float(a))))
    return (
        f"建物坪単価の参考（外挿）：中央目安{med:,.0f}円/坪（築{a}年。参考テーブルは築20年帯までのため外挿）"
    )


def _building_age_market_ratio(age_years: Optional[float]) -> float:
    """築2～3年帯中央値を1.0としたときの相対係数（㎡単価補正・建物評価の目安に使用）"""
    if age_years is None or age_years <= 0:
        return 1.0
    med = _median_tsubo_yen_for_age(float(age_years))
    return med / _REF_TSUBO_BASELINE_YEN if _REF_TSUBO_BASELINE_YEN > 0 else 1.0


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
    csv_features_500m_land: Optional[List[Dict]] = None,
    land_volume_zone_caption: Optional[str] = None,
) -> Tuple[float, Optional[float], Optional[float], Optional[float], Optional[str]]:
    """
    種別に応じた査定金額を算出。
    中古戸建は「土地単価×土地面積×画地補正＋建物評価額」で計算。
    戻り値: (査定額, 土地価格, 建物評価額, 参考500m土地単価, 土地ボリュームゾーン説明)
    """
    if property_type == "土地":
        avg_with_markup = avg_unit_price * LAND_MARKUP_RATE
        land_val = land_area * avg_with_markup
        return (
            land_val * (1.0 + kakuti_rate),
            land_val * (1.0 + kakuti_rate),
            None,
            None,
            land_volume_zone_caption,
        )
    elif property_type == "中古住宅（戸建て）" and csv_features is not None:
        result = _compute_valuation_detached(
            csv_features,
            land_area,
            subject_building_age,
            kakuti_rate,
            subject_building_area_sqm=building_area,
            csv_features_2km=csv_features_2km,
            csv_features_2km_land=csv_features_2km_land,
            avg_unit_price=avg_unit_price,
            csv_features_500m_land=csv_features_500m_land,
        )
        if result is not None:
            return result
    land_val = land_area * avg_unit_price
    bldg_val = building_area * avg_unit_price * building_age_correction if property_type == "中古住宅（戸建て）" else 0
    base = land_val + (bldg_val if property_type == "中古住宅（戸建て）" else exclusive_area * avg_unit_price * building_age_correction)
    if property_type == "中古住宅（戸建て）":
        return base * (1.0 + kakuti_rate), land_val * (1.0 + kakuti_rate), bldg_val * (1.0 + kakuti_rate), None, None
    return base * (1.0 + kakuti_rate), None, None, None, None


def _compute_valuation_detached(
    csv_features: List[Dict],
    land_area: float,
    subject_building_age: Optional[int],
    kakuti_rate: float,
    subject_building_area_sqm: float = 0.0,
    csv_features_2km: Optional[List[Dict]] = None,
    csv_features_2km_land: Optional[List[Dict]] = None,
    avg_unit_price: Optional[float] = None,
    csv_features_500m_land: Optional[List[Dict]] = None,
) -> Optional[Tuple[float, float, float, Optional[float], Optional[str]]]:
    """
    中古戸建の査定：
    ・昭和56年以前（築44年以上）: 建物評価0（リフォームされていても）
    ・築35年以上: 建物基本評価0、リフォーム等で変動
    ・築34年以下: 土地2km・売買価格差額から「建物の延床㎡あたり円」を集計し、対象物件の延床㎡に按分
    土地㎡単価は成約総額フィルタ＋坪単価ボリュームゾーン平均を使用。
    """
    # 500m圏内の土地単価（参考・同じロジックで集約）
    avg_land_500m: Optional[float] = None
    if csv_features_500m_land:
        pairs_500m = _collect_land_transaction_pairs(csv_features_500m_land)
        if pairs_500m:
            avg_land_500m, _, _ = _land_volume_zone_avg_from_pairs(pairs_500m)

    # 土地単価用ペア（2km「土地」優先 → 築25年以上戸建 → フォールバック）
    land_pairs: List[Tuple[float, float]] = []
    if csv_features_2km_land and subject_building_age is not None and subject_building_age <= 34:
        land_pairs.extend(_collect_land_transaction_pairs(csv_features_2km_land))

    if not land_pairs:
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
                land_pairs.append((float(total), float(total) / float(land_a)))

    if not land_pairs:
        fallback_data = csv_features_2km_land if csv_features_2km_land else (csv_features_2km if csv_features_2km else csv_features)
        land_pairs.extend(_collect_land_transaction_pairs(fallback_data))

    if not land_pairs:
        return None
    avg_land, land_zone_caption, _ = _land_volume_zone_avg_from_pairs(land_pairs)
    if avg_land is None:
        return None
    land_value_base = land_area * avg_land * (1.0 + kakuti_rate)

    # 昭和56年以前（築44年以上）: 建物評価0
    if subject_building_age is not None and subject_building_age >= 44:
        return land_value_base, land_value_base, 0, avg_land_500m, land_zone_caption

    # 築35年以上: 建物基本評価0
    if subject_building_age is None or subject_building_age >= 35:
        return land_value_base, land_value_base, 0, avg_land_500m, land_zone_caption

    # 築34年以下: 差額の「建物円/延床㎡」を事例ごとに求め堅牢平均 → 対象の延床㎡へ按分（土地㎡で割った建物総額の平均は延床の小さい物件で過大になる）
    if csv_features_2km and subject_building_age is not None and subject_building_age <= 34:
        building_yen_per_sqm: List[float] = []
        comp_ages: List[float] = []
        for f in csv_features_2km:
            p = f.get("properties", {})
            total = parse_numeric(p.get("u_transaction_price_total_ja"))
            land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
            bldg_sqm = parse_numeric(p.get("建物面積_数値")) or parse_numeric(p.get("u_building_total_floor_area_ja"))
            age = p.get("築年数_成約時")
            age_f = float(age) if age is not None else 0
            if not total or total <= 0 or not land_a or land_a <= 0:
                continue
            if age_f > 34:
                continue
            land_price_case = land_a * avg_land * (1.0 + kakuti_rate)
            bldg_val = total - land_price_case
            if bldg_val >= 0 and bldg_sqm is not None and bldg_sqm > 0:
                building_yen_per_sqm.append(bldg_val / float(bldg_sqm))
                if age is not None:
                    comp_ages.append(max(1.0, min(120.0, float(age))))
        if building_yen_per_sqm and subject_building_area_sqm > 0:
            avg_rate = _compute_robust_average(building_yen_per_sqm)
            if avg_rate is None:
                avg_rate = 0.0
            avg_building = avg_rate * float(subject_building_area_sqm)
            r_sub = _building_age_market_ratio(float(subject_building_age))
            if comp_ages:
                r_comp = _building_age_market_ratio(sum(comp_ages) / len(comp_ages))
                if r_comp > 1e-6:
                    avg_building *= r_sub / r_comp
            land_value = land_value_base
            return land_value + avg_building, land_value, avg_building, avg_land_500m, land_zone_caption

    # フォールバック: 従来ロジック（築20年以下は残価率、築25年以上は0）
    if subject_building_age is None or subject_building_age >= 25:
        return land_value_base, land_value_base, 0, avg_land_500m, land_zone_caption
    residual = get_building_residual_rate_20y(float(subject_building_age))
    building_value = STANDARD_NEW_BUILDING_PRICE * residual
    land_value = land_value_base
    return land_value + building_value, land_value, building_value, avg_land_500m, land_zone_caption


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
    """築年数に応じた㎡単価の補正係数。建物坪単価は帯ごとに段階変化し、築2～3年帯中央値を1.0とする相対値。"""
    if building_age is None or building_age <= 0:
        return 1.0
    return _building_age_market_ratio(float(building_age))


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


def _collect_land_transaction_pairs(csv_features: List[Dict]) -> List[Tuple[float, float]]:
    """各事例の (成約総額[円], 土地㎡単価[円/㎡]) を収集（土地面積ベース）。"""
    pairs: List[Tuple[float, float]] = []
    for f in csv_features:
        p = f.get("properties", {})
        total = parse_numeric(p.get("u_transaction_price_total_ja"))
        land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
        if total and land_a and land_a > 0:
            pairs.append((float(total), float(total) / float(land_a)))
    return pairs


def _land_volume_zone_avg_from_pairs(
    pairs: List[Tuple[float, float]],
) -> Tuple[Optional[float], Optional[str], int]:
    """
    土地成約の (総額, ㎡単価) から代表㎡単価を算出する。
    1) 成約総額が「平均総額 − 200万円」未満の事例を除外
    2) 坪単価の 25〜75% 帯をボリュームゾーンとし、帯内の坪単価平均を㎡単価に換算
    戻り値: (代表㎡単価[円/㎡], 画面表示用キャプション, ②の帯に使った件数)
    """
    if not pairs:
        return None, None, 0
    totals = np.array([t for t, _ in pairs], dtype=float)
    mean_tot = float(np.mean(totals))
    cut = LAND_UNDERPRICE_VS_MEAN_YEN
    filtered = [(t, u) for t, u in pairs if t >= mean_tot - cut]
    if not filtered:
        filtered = list(pairs)
    tsubo_rows: List[Tuple[float, float, float]] = []
    for t, u in filtered:
        tsubo_man = (u / 10000.0) * M2_TO_TSUBO
        tsubo_rows.append((t, u, tsubo_man))
    m_arr = np.array([r[2] for r in tsubo_rows], dtype=float)
    if len(m_arr) == 0:
        return None, None, 0
    if len(m_arr) < 4:
        avg_u = float(np.mean([u for _, u in filtered]))
        m0 = (avg_u / 10000.0) * M2_TO_TSUBO
        cap = f"（ボリュームゾーン：参照件数が少ないため平均 {m0:.1f}万円/坪）"
        return avg_u, cap, len(filtered)
    p25, p75 = np.percentile(m_arr, [25, 75])
    in_band = [(t, u, m) for t, u, m in tsubo_rows if p25 <= m <= p75]
    if not in_band:
        in_band = tsubo_rows
    avg_tsubo = float(np.mean([m for _, _, m in in_band]))
    avg_sqm = avg_tsubo * 10000.0 / M2_TO_TSUBO
    cap = (
        f"（ボリュームゾーン：{p25:.1f}万円/坪～{p75:.1f}万円/坪　"
        f"平均値{avg_tsubo:.1f}万円/坪）"
    )
    return avg_sqm, cap, len(in_band)


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
        area = _deal_area_sqm_for_unit_price(p)
        if not total or not area or area <= 0:
            continue
        dt = _parse_date_ymd(p.get("point_in_time_name_ja") or p.get("成約年月日") or "")
        if dt:
            rows.append({"dt": dt, "unit_price": total / area})

    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("dt")

    # 坪単価（万円/坪）で表示。年別平均を折れ線で表示
    df = df.copy()
    df["unit_price_tsubo_man"] = (df["unit_price"] / 10000) * M2_TO_TSUBO
    df["year"] = pd.to_datetime(df["dt"]).dt.year
    line_df = df.groupby("year", as_index=False)["unit_price_tsubo_man"].mean().sort_values("year")
    line_df["period"] = pd.to_datetime(line_df["year"].astype(str) + "-07-01")
    df["dt_plot"] = pd.to_datetime(df["dt"])

    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["dt_plot"],
        y=df["unit_price_tsubo_man"],
        mode="markers",
        name="成約物件",
        marker=dict(color="rgba(52, 152, 219, 0.5)", size=7),
        hovertemplate="成約日: %{x|%Y-%m-%d}<br>坪単価: %{y:.2f} 万円/坪<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=line_df["period"],
        y=line_df["unit_price_tsubo_man"],
        mode="lines+markers",
        name="成約（年別平均）",
        line=dict(color="#3498db", width=2, shape="linear"),
        marker=dict(size=9, color="#3498db"),
        text=line_df["year"].astype(str) + "年",
        hovertemplate="%{text}<br>坪単価: %{y:.2f} 万円/坪<extra></extra>",
    ))

    if len(line_df) >= 2:
        x_numeric = line_df["period"].map(datetime.toordinal)
        z = np.polyfit(x_numeric, line_df["unit_price_tsubo_man"], 1)
        poly = np.poly1d(z)
        fig.add_trace(go.Scatter(
            x=line_df["period"],
            y=poly(x_numeric),
            mode="lines",
            name="トレンド",
            line=dict(color="#e74c3c", width=2, dash="dash"),
        ))

    # スマホ向け：余白・フォント・高さを最適化
    fig.update_layout(
        title=dict(text="周辺の価格推移（成約物件＋年別平均・坪単価）", font=dict(size=16)),
        xaxis=dict(
            title="成約日",
            tickformat="%y/%m",
            tickangle=-30,
            tickfont=dict(size=11),
        ),
        yaxis=dict(
            title="坪単価（万円/坪）",
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
    直近1年と5年前付近の帯の平均㎡単価を比較し、分析コメントを生成。
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
    four_years_ago = datetime(now.year - 4, now.month, 1)
    five_years_ago = datetime(now.year - 5, now.month, 1)

    recent = df[df["dt"] >= one_year_ago]["unit_price"]
    old = df[(df["dt"] >= five_years_ago) & (df["dt"] < four_years_ago)]["unit_price"]

    if len(recent) == 0 or len(old) == 0:
        return None

    recent_avg = recent.mean()
    old_avg = old.mean()
    if pd.isna(old_avg) or old_avg <= 0:
        return None

    pct = (recent_avg - old_avg) / old_avg * 100
    direction = "上昇" if pct > 0 else "下落"
    return f"直近1年間の平均㎡単価は、5年前付近の水準と比較して **{abs(pct):.1f}% {direction}** しています。（外れ値を除外し平均値で算出）"


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
    zone_cap = (kwargs.get("land_volume_zone_caption") or "").strip()
    bld_zone = (kwargs.get("building_volume_zone_caption") or "").strip()
    if zone_cap or bld_zone:
        from xml.sax.saxutils import escape as _xml_escape
        elements.append(Spacer(1, 6))
        if zone_cap:
            elements.append(Paragraph(_xml_escape(zone_cap), s_style))
        if bld_zone:
            elements.append(Paragraph(_xml_escape(bld_zone), s_style))
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
    
    avg_land_500m = kwargs.get("avg_land_500m", None)
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
    zone_cap = (kwargs.get("land_volume_zone_caption") or "").strip()
    if zone_cap:
        from xml.sax.saxutils import escape as _xml_escape
        left_cell_contents.append(Paragraph(_xml_escape(zone_cap), small_style))
    bld_zone = (kwargs.get("building_volume_zone_caption") or "").strip()
    if bld_zone:
        from xml.sax.saxutils import escape as _xml_escape
        left_cell_contents.append(Paragraph(_xml_escape(bld_zone), small_style))
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

    if avg_land_500m is not None and avg_land_500m > 0:
        left_cell_contents.append(Spacer(1, 4))
        left_cell_contents.append(Paragraph("■ 参考情報", heading_style))
        ref_data = [
            ["500m圏内土地平均"],
            [f"平米: {avg_land_500m/10000:,.1f} 万円"],
            [f"坪: {(avg_land_500m/10000)*M2_TO_TSUBO:,.1f} 万円"],
        ]
        ref_table = Table(ref_data, colWidths=[70*mm])
        ref_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#f0f8ff")),
            ("FONT", (0, 0), (-1, -1), font_name, 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        left_cell_contents.append(ref_table)
    elif avg_land_500m is not None and avg_land_500m == 0:
        pass # Explicitly handle 0 case if needed

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

import base64
from PIL import Image

def get_optimized_image_base64(img_path: Path, width: int = 400) -> str:
    """画像をリサイズしてBase64文字列に変換する（表示安定化のため）"""
    if img_path.exists():
        try:
            img = Image.open(img_path)
            # アスペクト比を維持してリサイズ
            w_percent = (width / float(img.size[0]))
            h_size = int((float(img.size[1]) * float(w_percent)))
            img = img.resize((width, h_size), Image.LANCZOS)
            
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return ""
    return ""

# ========== UI ==========
# 状態の初期化
if "search_result" not in st.session_state:
    st.session_state.search_result = None
if "csv_cases" not in st.session_state:
    st.session_state.csv_cases = []
if "csv_df" not in st.session_state:
    st.session_state.csv_df = pd.DataFrame()
if "initial_load_done" not in st.session_state:
    st.session_state.initial_load_done = False

# 起動時に一度だけCSVを読み込み
if not st.session_state.initial_load_done:
    try:
        csv_path = _ensure_reins_data_3years()
        if not csv_path.exists():
            st.error(f"CSVファイルが見つかりません: {csv_path.name}")
            st.info(f"期待されるパス: {csv_path.absolute()}")
        else:
            csv_mtime = csv_path.stat().st_mtime
            with st.spinner("データの解析中..."):
                cases, csv_df = load_data(str(csv_path), csv_mtime)
                if not cases:
                    st.error(f"CSVファイルから有効なデータを読み込めませんでした: {csv_path.name}")
                    # デバッグ用にファイルの中身を少し確認
                    try:
                        with open(csv_path, 'r', encoding='utf-8-sig') as f:
                            head = [f.readline() for _ in range(3)]
                        st.info(f"ファイル先頭のデータ: {head}")
                    except:
                        pass
                else:
                    # 有効なデータのみに絞り込み
                    valid_cases = [c for c in cases if (c.get("成約価格_円") or 0) > 0]
                    st.session_state.csv_cases = valid_cases
                    st.session_state.csv_df = csv_df
                    st.session_state.initial_load_done = True
                    if not valid_cases:
                        st.error("有効な成約価格（>0）を持つデータが0件です。")
    except Exception as e:
        st.error(f"データ読み込み中に致命的なエラーが発生しました: {e}")
        import traceback
        st.code(traceback.format_exc())
        st.session_state.csv_cases = []
        st.session_state.csv_df = pd.DataFrame()

with st.sidebar:
    st.markdown("### 📄 データソース")
    n_csv = len(st.session_state.csv_cases)
    st.info(f"**取引データ**: {n_csv} 件")
    if n_csv == 0:
        st.warning("CSVファイルから有効なデータが読み込まれていません。")
    if st.button("キャッシュクリア・再読み込み"):
        st.session_state.initial_load_done = False
        st.cache_data.clear()
        st.rerun()

# ランディングページ風ヘッダーの構築
character_path = Path(__file__).parent / "assets" / "Copilot_20260324_100708.png"

# 1. スタイル定義（Markdownの字下げによるコード化を防ぐため左寄せで記述）
st.markdown("""
<style>
.main .block-container {
    padding-bottom: 200px !important;
}
@media (max-width: 768px) {
    div[data-testid="column"] { min-width: 100% !important; }
}
.hero-wrapper {
    background: linear-gradient(135deg, #f0f8ff 0%, #e6f2ff 100%);
    border-radius: 15px;
    padding: 25px;
    margin-bottom: 30px;
    border: 1px solid #d1e3f8;
    box-shadow: 0 10px 20px rgba(0,0,0,0.05);
}
.hero-logo-box {
    display: flex;
    align-items: center;
    margin-bottom: 10px;
}
.hero-logo-k {
    background-color: #1f77b4;
    color: white;
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 5px;
    margin-right: 10px;
    font-size: 24px;
    font-weight: 900;
}
.hero-logo-text {
    font-size: 28px;
    font-weight: 900;
    color: #2c3e50;
}
.hero-title {
    font-size: 24px;
    font-weight: 800;
    color: #1a4f76;
    line-height: 1.4;
    margin-bottom: 10px;
}
.hero-subtitle {
    font-size: 15px;
    color: #4a6fa5;
    font-weight: 700;
    margin-bottom: 20px;
    background: white;
    display: inline-block;
    padding: 4px 12px;
    border-radius: 50px;
}
.feature-item {
    font-size: 16px;
    font-weight: 700;
    color: #333;
    margin-bottom: 10px;
}
.feature-check {
    color: #28a745;
    margin-right: 8px;
}
</style>
""", unsafe_allow_html=True)

# 2. UI構築（安定性を重視しつつ余白を最小化）
def render_valuation_result(sr, is_previous=False):
    """査定結果のレンダリングを共通化"""
    title_prefix = "### 📊 仮査定結果（前回の検索結果）" if is_previous else "### 📊 仮査定結果"
    st.markdown("---")
    st.markdown(title_prefix)
    
    valuation = sr["valuation"]
    st.markdown(
        f'<p style="font-size: 2.5rem; font-weight: bold; color: #1f77b4;">'
        f'仮査定金額：<span style="font-size: 3rem;">{valuation/10000:,.0f}</span> 万円</p>',
        unsafe_allow_html=True
    )
    lzc = sr.get("land_volume_zone_caption")
    if lzc:
        st.markdown(
            f'<p style="font-size: 1rem; color: #555; margin-top: 0.25rem;">（{html.escape(str(lzc))}）</p>',
            unsafe_allow_html=True,
        )
    bzc = sr.get("building_volume_zone_caption")
    if bzc:
        st.markdown(
            f'<p style="font-size: 1rem; color: #555; margin-top: 0.25rem;">（{html.escape(str(bzc))}）</p>',
            unsafe_allow_html=True,
        )

    # PDFダウンロードボタン
    pdf_bytes = sr.get("pdf_bytes")
    if pdf_bytes:
        st.download_button(
            label="📄 査定書をPDFでダウンロード",
            data=pdf_bytes,
            file_name=f"査定報告書_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            type="primary",
            key="pdf_download_prev" if is_previous else "pdf_download",
        )

    # 指標の表示
    avg_unit_price = sr["avg_unit_price"]
    correction = sr["correction"]
    adjusted_unit_price = sr["adjusted_unit_price"]
    csv_count = sr["csv_count"]
    property_type = sr["property_type"]
    land_area_input = sr["land_area_input"]
    kakuti_rate = sr["kakuti_rate"]
    land_breakdown = sr.get("land_breakdown")
    building_breakdown = sr.get("building_breakdown")
    building_age_val = sr.get("building_age_val")
    avg_land_500m = sr.get("avg_land_500m")
    radius_km = sr["radius_km"]
    address = sr["address"]

    if property_type == "中古住宅（戸建て）" and land_breakdown is not None:
        display_avg = (land_breakdown / land_area_input) / (1.0 + kakuti_rate) if land_area_input > 0 else 0
        display_adj = display_avg
        _house_label_avg = "土地ベース・坪単価（推定）"
        _house_label_adj = "土地ベース・補正後（参考）"
        _m2_unit_caption = "土地㎡単価"
    else:
        _house_label_avg = "坪単価の平均"
        _house_label_adj = "補正後坪単価"
        _m2_unit_caption = "㎡単価"
        _apply_markup = (property_type == "土地")
        display_avg = (avg_unit_price * LAND_MARKUP_RATE) if _apply_markup else avg_unit_price
        display_adj = (adjusted_unit_price * LAND_MARKUP_RATE) if _apply_markup else adjusted_unit_price

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        tsubo_avg = (display_avg / 10000) * M2_TO_TSUBO
        st.markdown(
            f'<p style="font-size:0.8rem;margin:0;color:#666;">{_house_label_avg}</p>'
            f'<p style="font-size:1.85rem;font-weight:700;color:#1a5276;margin:0;line-height:1.2;">{tsubo_avg:,.1f}<span style="font-size:1rem;font-weight:600;"> 万円/坪</span></p>'
            f'<p style="font-size:0.78rem;color:#888;margin:0.35rem 0 0 0;">{_m2_unit_caption} {display_avg/10000:,.1f} 万円/㎡</p>',
            unsafe_allow_html=True,
        )
    with col2:
        st.metric("築年数補正係数", f"{correction:.2f}")
    with col3:
        tsubo_adj = (display_adj / 10000) * M2_TO_TSUBO
        st.markdown(
            f'<p style="font-size:0.8rem;margin:0;color:#666;">{_house_label_adj}</p>'
            f'<p style="font-size:1.85rem;font-weight:700;color:#1a5276;margin:0;line-height:1.2;">{tsubo_adj:,.1f}<span style="font-size:1rem;font-weight:600;"> 万円/坪</span></p>'
            f'<p style="font-size:0.78rem;color:#888;margin:0.35rem 0 0 0;">{_m2_unit_caption} {display_adj/10000:,.1f} 万円/㎡</p>',
            unsafe_allow_html=True,
        )
    with col4:
        st.metric("参考取引件数", f"{csv_count} 件")

    building_area_input = sr.get("building_area_input") or 0.0
    if (
        property_type == "中古住宅（戸建て）"
        and (building_breakdown or 0) > 0
        and building_area_input > 0
    ):
        b_m2 = building_breakdown / building_area_input
        b_tsubo_man = (b_m2 / 10000) * M2_TO_TSUBO
        st.markdown(
            f'<div style="background-color: #f8f9fa; padding: 10px; border-radius: 6px; margin-top: 8px;">'
            f'<span style="font-size:0.85rem;color:#444;">建物評価ベース（入力の延床面積で按分）：'
            f'<strong style="color:#1a5276;">{b_tsubo_man:,.1f} 万円/坪</strong>　'
            f'延床㎡単価 {b_m2/10000:,.1f} 万円/㎡</span></div>',
            unsafe_allow_html=True,
        )

    if property_type == "中古住宅（戸建て）":
        if avg_land_500m is not None and avg_land_500m > 0:
            st.markdown(
                f'<div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin-top: 10px; margin-bottom: 10px;">'
                f'<strong>💡 参考情報</strong><br>'
                f'半径500m以内の成約ベースの土地価格平均値： '
                f'<span style="font-size: 1.35rem; font-weight: bold; color: #1f77b4;">{(avg_land_500m/10000)*M2_TO_TSUBO:,.1f} 万円/坪</span> '
                f'<span style="font-size: 0.95rem; color: #555;">（㎡単価 {avg_land_500m/10000:,.1f} 万円/㎡）</span>'
                f'</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin-top: 10px; margin-bottom: 10px;">'
                f'<strong>💡 参考情報</strong><br>'
                f'半径500m以内の土地取引データがありませんでした。'
                f'</div>',
                unsafe_allow_html=True
            )

    st.caption(f"※ 半径{radius_km}㎞の、過去5年の成約事例データを参考にしています。（住所: {address}）")
    if property_type == "土地":
        st.caption("※ 成約ベースの価格から、坪単価・㎡単価に20%を上乗せしています。")

    # 算出式
    try:
        latex_f, detail_f = format_valuation_formula(
            property_type, valuation, display_avg, correction,
            sr["land_area_input"], sr["building_area_input"], sr["exclusive_area_input"],
            kakuti_rate=kakuti_rate,
            building_breakdown=building_breakdown,
            land_breakdown=land_breakdown,
        )
        st.markdown(f"**算出式**: ${latex_f}$")
        st.caption(f"※ {detail_f}（参考値です）")
    except Exception:
        st.caption(f"※ 査定金額：{valuation/10000:,.0f}万円（参考値です）")

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

    # チャート
    price_chart = sr.get("price_chart")
    if price_chart:
        st.subheader("📈 価格トレンドグラフ")
        st.plotly_chart(price_chart, use_container_width=True)
        trend_comment = get_price_trend_analysis(sr.get("csv_filtered", []))
        if trend_comment:
            st.markdown(trend_comment)

    if property_type == "中古住宅（戸建て）":
        advice = get_depreciation_advice(building_age_val, property_type)
        if advice:
            st.warning(advice)

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

import base64
def get_b64(path):
    if not path.exists(): return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

# ロゴは標準機能で表示（巨大なBase64によるクラッシュを回避）
logo_path = Path(__file__).parent / "assets" / "company_logo_large.png"
if not logo_path.exists():
    logo_path = Path(__file__).parent / "assets" / "company_logo.png"

with st.container():
    col_l1, col_l2, col_l3 = st.columns([2, 2, 2])
    with col_l2:
        if logo_path.exists():
            st.image(str(logo_path), use_column_width=True)
        else:
            st.markdown('<h3 style="text-align:center; margin-bottom: 0;">株式会社 杏栄</h3>', unsafe_allow_html=True)
        st.markdown(
            '<p style="text-align:center; color: #ff0000; font-weight: bold; font-size: 20px; margin-top: -5px; margin-bottom: 10px;">準備中</p>',
            unsafe_allow_html=True,
        )

# ヒーローセクション（余白を詰め、安定したHTMLで記述）
st.markdown("""
<div style="width: 100%; max-width: 800px; margin: -10px auto 0 auto; font-family: 'Helvetica Neue', Arial, sans-serif;">
<div style="background: linear-gradient(135deg, #f0faff 0%, #e6f5ff 100%); 
border-radius: 15px; border: 2px solid #bde0fe; 
box-shadow: 0 10px 25px rgba(0,0,0,0.06); text-align: center; padding: 20px; position: relative; overflow: hidden;">
<h1 style="font-size: 24px; color: #1a4f76; margin: 0 0 8px 0; font-weight: 800; line-height: 1.3;">
スマホで最短1分査定！<br>旭川の家の価値、カンタン価格診断
</h1>
<div style="background: white; color: #4a6fa5; display: inline-block; padding: 4px 18px; 
border-radius: 50px; font-weight: 800; font-size: 16px; border: 1.2px solid #d1e3f8; margin-bottom: 20px;">
最短60秒・匿名OK・営業なしで安心
</div>
<div style="max-width: 420px; margin: 0 auto; text-align: left;">
<div style="font-size: 16px; font-weight: 800; color: #2c3e50; margin-bottom: 10px; display: flex; align-items: center;">
<span style="color: #28a745; margin-right: 10px; font-size: 20px;">✅</span> 旭川相場データをAIが自動分析
</div>
<div style="font-size: 16px; font-weight: 800; color: #2c3e50; margin-bottom: 10px; display: flex; align-items: center;">
<span style="color: #28a745; margin-right: 10px; font-size: 20px;">✅</span> 地域密着の安心サポート
</div>
<div style="font-size: 16px; font-weight: 800; color: #2c3e50; display: flex; align-items: center;">
<span style="color: #28a745; margin-right: 10px; font-size: 20px;">✅</span> 旭川の相場に最適化
</div>
</div>
</div>
</div>
<div style="margin-bottom: 25px;"></div>
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

    st.caption(f"半径{radius_km}㎞の、過去5年の成約事例データを参考にしています。")

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
    try:
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
            st.error("取引データが読み込まれていません。サイドバーから再読み込みを試してください。")
        else:
            # 面積入力の確定
            if property_type == "土地":
                area_input = land_area_input
            elif property_type == "中古住宅（戸建て）":
                area_input = land_area_input + building_area_input
            else:
                area_input = exclusive_area_input

            status_text = st.empty()
            with st.spinner("査定計算を開始します..."):
                status_text.info("📍 住所を座標に変換中...")
                coords = geocode_address(address)
                
                if not coords:
                    st.error("住所の変換（ジオコーディング）に失敗しました。住所を正しく入力するか、地図から選択してください。")
                else:
                    lat, lon = coords
                    status_text.info("📍 取引データを照合中...")

                    search_radius_m = float(radius_km) * 1000
                    csv_raw = st.session_state.get("csv_cases", [])
                    csv_df = st.session_state.get("csv_df")
                    csv_features = filter_csv_by_distance(csv_raw, lat, lon, search_radius_m, csv_df=csv_df)

                    if not csv_features:
                        st.warning(f"半径{radius_km}km以内に取引事例が見つかりませんでした。別の住所でお試しください。")
                        st.session_state.search_result = {
                            "has_valuation": False,
                            "address": address, "lat": lat, "lon": lon,
                            "property_type": property_type, "radius_km": radius_km,
                        }
                    else:
                        status_text.info("📊 データを分析し、査定金額を算出中...")
                        # 事例の絞り込み
                        filter_type = PROPERTY_TYPE_TO_CSV_TYPE.get(property_type, [])
                        age_center = int(building_age) if building_age is not None else 0
                        filter_age_min = max(0, age_center - 5)
                        filter_age_max = min(50, age_center + 5)
                        if age_center == 0:
                            filter_age_min, filter_age_max = 0, 10
                        filter_contract_value = "5years"
                        csv_filtered = apply_case_filters(csv_features, filter_type, filter_age_min, filter_age_max, filter_contract_value)
                        
                        if not csv_filtered:
                            st.warning("条件（物件種別・築年数）に合う事例が見つかりません。")
                            st.session_state.search_result = {
                                "has_valuation": False,
                                "address": address, "lat": lat, "lon": lon,
                                "property_type": property_type, "radius_km": radius_km,
                            }
                        else:
                            land_volume_zone_caption: Optional[str] = None
                            if property_type == "土地":
                                land_pairs = _collect_land_transaction_pairs(csv_filtered)
                                avg_unit_price, land_volume_zone_caption, _ = _land_volume_zone_avg_from_pairs(land_pairs)
                                csv_count = len(csv_filtered)
                            else:
                                avg_unit_price, csv_count = compute_avg_unit_price(csv_filtered)
                            if avg_unit_price is None or avg_unit_price <= 0:
                                st.warning("㎡単価を算出できる取引データがありませんでした。")
                                st.session_state.search_result = {
                                    "has_valuation": False,
                                    "address": address, "lat": lat, "lon": lon,
                                    "property_type": property_type, "radius_km": radius_km,
                                }
                            else:
                                building_age_val = int(building_age) if building_age is not None and building_age > 0 else None
                                building_age_correction = 1.0 if property_type == "土地" else get_building_age_correction_factor(building_age_val)
                                kakuti_rate = get_corner_correction_rate(corner_check)
                                
                                # 特殊な計算（戸建て2kmなど）
                                csv_2km = None
                                csv_2km_land = None
                                csv_500m_land = None
                                if property_type == "中古住宅（戸建て）":
                                    if building_age_val is not None and building_age_val <= 34:
                                        csv_2km_raw = filter_features_by_distance(csv_features, lat, lon, 2000)
                                        csv_2km = apply_case_filters(csv_2km_raw, filter_type, 0, 50, filter_contract_value)
                                        csv_2km_land = apply_case_filters(csv_2km_raw, PROPERTY_TYPE_TO_CSV_TYPE.get("土地", []), 0, 50, filter_contract_value)

                                    csv_500m_raw = filter_features_by_distance(csv_features, lat, lon, 500)
                                    land_types = PROPERTY_TYPE_TO_CSV_TYPE.get("土地", ["売地", "土地", "宅地"])
                                    csv_500m_land = apply_case_filters(csv_500m_raw, land_types, 0, 50, filter_contract_value)

                                # 査定計算の実行
                                result = compute_valuation(
                                    property_type, avg_unit_price, building_age_correction,
                                    land_area_input, building_area_input, exclusive_area_input,
                                    kakuti_rate=kakuti_rate,
                                    subject_building_age=building_age_val if property_type == "中古住宅（戸建て）" else None,
                                    csv_features=csv_filtered if property_type == "中古住宅（戸建て）" else None,
                                    csv_features_2km=csv_2km,
                                    csv_features_2km_land=csv_2km_land,
                                    csv_features_500m_land=csv_500m_land,
                                    land_volume_zone_caption=land_volume_zone_caption if property_type == "土地" else None,
                                )
                                
                                valuation = result[0]
                                land_breakdown = result[1]
                                building_breakdown = result[2]
                                avg_land_500m = result[3] if len(result) > 3 else None
                                land_vol_cap = result[4] if len(result) > 4 else None
                                if property_type != "土地" and land_vol_cap:
                                    land_volume_zone_caption = land_vol_cap
                                adjusted_unit_price = avg_unit_price * building_age_correction
                                building_volume_zone_caption: Optional[str] = None
                                if property_type in ("中古住宅（戸建て）", "中古マンション") and building_age_val:
                                    building_volume_zone_caption = format_building_volume_zone_caption(building_age_val)

                                status_text.info("📄 査定報告書（PDF）を作成中...")
                                price_chart = build_price_trend_chart(csv_filtered)
                                map_df = build_map_dataframe(lat, lon, csv_filtered)
                                df_pdf = build_csv_reference_table(csv_filtered, limit=MAX_REFERENCE_CASES, for_pdf=True)
                                
                                # PDF生成
                                pdf_bytes = None
                                try:
                                    pdf_bytes = generate_valuation_pdf(
                                        address=address, property_type=property_type, area_input=area_input,
                                        building_age=int(building_age) if building_age > 0 else 0,
                                        valuation=valuation, avg_unit_price=avg_unit_price,
                                        correction=building_age_correction, adjusted_unit_price=adjusted_unit_price,
                                        transaction_count=csv_count, df_reference=df_pdf,
                                        map_df=map_df, price_chart=price_chart,
                                        land_area_input=land_area_input, building_area_input=building_area_input,
                                        exclusive_area_input=exclusive_area_input,
                                        building_breakdown=building_breakdown if property_type == "中古住宅（戸建て）" else None,
                                        land_breakdown=land_breakdown if property_type == "中古住宅（戸建て）" else None,
                                        kakuti_rate=kakuti_rate, corner_check=corner_check,
                                        avg_land_500m=avg_land_500m if property_type == "中古住宅（戸建て）" else None,
                                        land_volume_zone_caption=land_volume_zone_caption,
                                        building_volume_zone_caption=building_volume_zone_caption,
                                    )
                                except Exception as e:
                                    st.warning(f"PDF生成中にエラーが発生しました: {e}")

                                # セッション状態の保存
                                res_data = {
                                    "has_valuation": True,
                                    "address": address, "lat": lat, "lon": lon,
                                    "property_type": property_type, "radius_km": radius_km,
                                    "csv_filtered": csv_filtered, "csv_count": csv_count,
                                    "valuation": valuation, "avg_unit_price": avg_unit_price,
                                    "correction": building_age_correction, "adjusted_unit_price": adjusted_unit_price,
                                    "kakuti_rate": kakuti_rate, "corner_check": corner_check,
                                    "land_area_input": land_area_input, "building_area_input": building_area_input,
                                    "exclusive_area_input": exclusive_area_input,
                                    "building_age": building_age, "building_age_val": building_age_val,
                                    "area_input": area_input,
                                    "building_breakdown": building_breakdown, "land_breakdown": land_breakdown,
                                    "avg_land_500m": avg_land_500m, "pdf_bytes": pdf_bytes,
                                    "price_chart": price_chart,
                                    "land_volume_zone_caption": land_volume_zone_caption,
                                    "building_volume_zone_caption": building_volume_zone_caption,
                                }
                                st.session_state.search_result = res_data

                                status_text.info("✉️ 査定依頼を送信中...")
                                # Webhook通知
                                payload = {
                                    "送信日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "お客様情報": {
                                        "お名前": contact_name.strip(), "電話番号": contact_phone.strip(), "メールアドレス": contact_email.strip(),
                                    },
                                    "物件情報": {
                                        "住所": address, "物件種別": property_type,
                                        "土地面積（㎡）": land_area_input, "建物面積（㎡）": building_area_input, "専有面積（㎡）": exclusive_area_input,
                                        "築年数（年）": int(building_age) if building_age is not None else 0,
                                        "検索半径（km）": radius_km, "角地・準角地": corner_check,
                                    },
                                    "査定結果": {
                                        "仮査定金額（万円）": round(valuation / 10000, 0),
                                        "坪単価の平均（万円/坪）": round((avg_unit_price / 10000) * M2_TO_TSUBO, 1),
                                        "㎡単価の平均（万円/㎡）": round(avg_unit_price / 10000, 1),
                                        "参照事例数": csv_count,
                                        **({"土地ボリュームゾーン": land_volume_zone_caption} if land_volume_zone_caption else {}),
                                        **({"建物ボリュームゾーン": building_volume_zone_caption} if building_volume_zone_caption else {}),
                                    },
                                }
                                try:
                                    send_inquiry_to_webhook(payload)
                                except:
                                    pass # Webhookエラーで査定結果表示を妨げない

                                status_text.success("✅ 査定が完了しました！結果を表示します。")
            st.rerun()
    except Exception as e:
        st.error(f"査定計算中に予期しないエラーが発生しました: {e}")
        import traceback
        st.code(traceback.format_exc())

elif st.session_state.search_result is not None:
    sr = st.session_state.search_result
    if sr.get("has_valuation"):
        render_valuation_result(sr, is_previous=True)
        # お客様向け簡易版: 参照成約事例テーブル・周辺マップは出さない（社員向けは main.py）
    else:
        st.info(f"前回の検索: {sr.get('address')} — 査定結果を出せませんでした。")

# ページ下部の余白確保
st.markdown('<div style="height: 100px;"></div>', unsafe_allow_html=True)

