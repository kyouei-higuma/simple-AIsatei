"""
不動産仮査定アプリ
国土交通省「不動産情報ライブラリAPI」を使用して、中古マンションの仮査定を算出
"""

import math
import os
from typing import Dict, List, Optional, Tuple

import streamlit as st
import requests
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# ページ設定
st.set_page_config(
    page_title="不動産仮査定",
    page_icon="🏠",
    layout="wide"
)

# API設定
API_BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
API_KEY = os.environ.get("518fc7dadb6b44c29624a3755c481750")


def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    """住所を緯度・経度に変換"""
    if not address or not address.strip():
        return None
    try:
        geolocator = Nominatim(user_agent="real_estate_app")
        geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
        # 日本の住所検索のため「日本」を付加
        query = f"日本 {address.strip()}" if "日本" not in address else address.strip()
        location = geocode(query)
        if location:
            return (location.latitude, location.longitude)
    except Exception as e:
        st.error(f"住所の変換に失敗しました: {e}")
    return None


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> Tuple[int, int]:
    """緯度経度をタイル座標（XYZ方式）に変換"""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(lat_rad)) / math.pi) / 2 * n)
    return (x, y)


def haversine_distance(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """2点間の距離（メートル）を Haversine 公式で計算"""
    R = 6371000  # 地球の半径（メートル）
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def fetch_transaction_data(
    lat: float, lon: float, api_key: str
) -> List[Dict]:
    """
    住所周辺（半径500m）の過去2年間の中古マンション取引データを取得
    XPT001 API（ポイントAPI）を使用
    """
    if not api_key:
        return []

    zoom = 15
    cx, cy = lonlat_to_tile(lon, lat, zoom)

    # 半径500mをカバーするため、3x3タイルを取得
    all_features = []
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            x, y = cx + dx, cy + dy
            url = (
                f"{API_BASE_URL}/XPT001"
                f"?response_format=geojson"
                f"&z={zoom}&x={x}&y={y}"
                f"&from=20221"   # 2022年Q1
                f"&to=20244"     # 2024年Q4（過去2年程度）
                f"&landTypeCode=07"  # 中古マンション等
            )
            headers = {"Ocp-Apim-Subscription-Key": api_key}
            try:
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if "features" in data:
                        all_features.extend(data["features"])
            except Exception as e:
                st.warning(f"API取得中にエラー: {e}")

    # 重複除去（同一物件の可能性があるため）
    seen = set()
    unique_features = []
    for f in all_features:
        props = f.get("properties", {})
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [])
        if coords:
            key = (coords[0], coords[1], props.get("u_transaction_price_total_ja"), props.get("u_area_ja"))
            if key not in seen:
                seen.add(key)
                unique_features.append(f)

    # 半径500m以内にフィルタ
    result = []
    for f in unique_features:
        geom = f.get("geometry", {})
        coords = geom.get("coordinates", [])
        if len(coords) >= 2:
            plon, plat = coords[0], coords[1]
            if haversine_distance(lon, lat, plon, plat) <= 500:
                result.append(f)

    return result


def parse_unit_price(value) -> Optional[float]:
    """㎡単価の文字列を数値に変換"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(" ", "").replace("円", "").replace("/m²", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_area(value) -> Optional[float]:
    """面積の文字列を数値に変換"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(" ", "").replace("㎡", "").replace("m²", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_price(value) -> Optional[float]:
    """価格の文字列を数値に変換"""
    return parse_unit_price(value)


def build_table_df(features: List[Dict]) -> pd.DataFrame:
    """取引データをDataFrameに変換"""
    rows = []
    for f in features:
        p = f.get("properties", {})
        unit_price = parse_unit_price(p.get("u_transaction_price_unit_price_square_meter_ja"))
        area = parse_area(p.get("u_area_ja"))
        total_price = parse_price(p.get("u_transaction_price_total_ja"))
        if unit_price is None and total_price is not None and area is not None and area > 0:
            unit_price = total_price / area

        rows.append({
            "地区名": p.get("district_name_ja", "-"),
            "間取り": p.get("floor_plan_name_ja", "-"),
            "面積(㎡)": area,
            "取引価格(万円)": round(total_price / 10000, 1) if total_price else "-",
            "㎡単価(万円/㎡)": round(unit_price / 10000, 1) if unit_price else "-",
            "取引時点": p.get("point_in_time_name_ja", "-"),
            "建築年": p.get("u_construction_year_ja", "-"),
            "構造": p.get("building_structure_name_ja", "-"),
        })
    return pd.DataFrame(rows)


# ========== UI ==========
st.title("🏠 不動産仮査定アプリ")
st.caption("国土交通省　不動産情報ライブラリのデータを利用しています")

if not API_KEY:
    st.warning(
        "⚠️ APIキーが設定されていません。環境変数 `REINFOLIB_API_KEY` に "
        "不動産情報ライブラリのAPIキーを設定してください。（[API利用申請](https://www.reinfolib.mlit.go.jp/)）"
    )

with st.form("search_form"):
    address = st.text_input(
        "住所",
        placeholder="例: 東京都渋谷区神宮前1-2-3",
        help="査定したい物件の住所を入力してください"
    )
    area_input = st.number_input(
        "物件の専有面積（㎡）",
        min_value=1.0,
        max_value=500.0,
        value=50.0,
        step=0.1,
        help="仮査定を算出する物件の面積を入力してください"
    )
    submitted = st.form_submit_button("査定を実行")

if submitted:
    if not address or not address.strip():
        st.error("住所を入力してください。")
    elif not API_KEY:
        st.error("APIキーを設定してください。")
    else:
        with st.spinner("住所を変換し、取引データを取得しています..."):
            coords = geocode_address(address)
            if not coords:
                st.error("住所を緯度・経度に変換できませんでした。住所を確認してください。")
            else:
                lat, lon = coords
                st.success(f"住所を変換しました（緯度: {lat:.5f}, 経度: {lon:.5f}）")

                transactions = fetch_transaction_data(lat, lon, API_KEY)

                if not transactions:
                    st.warning(
                        "半径500m以内で過去2年間の中古マンション取引データが見つかりませんでした。"
                        "別の住所でお試しください。"
                    )
                else:
                    # ㎡単価を抽出
                    unit_prices = []
                    for f in transactions:
                        p = f.get("properties", {})
                        up = parse_unit_price(p.get("u_transaction_price_unit_price_square_meter_ja"))
                        if up is None:
                            total = parse_price(p.get("u_transaction_price_total_ja"))
                            ar = parse_area(p.get("u_area_ja"))
                            if total and ar and ar > 0:
                                up = total / ar
                        if up is not None and up > 0:
                            unit_prices.append(up)

                    if unit_prices:
                        avg_unit_price = sum(unit_prices) / len(unit_prices)
                        valuation = avg_unit_price * area_input

                        st.subheader("📊 仮査定結果")
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("㎡単価の平均", f"{avg_unit_price/10000:,.1f} 万円/㎡")
                        with col2:
                            st.metric("参考取引件数", f"{len(transactions)} 件")
                        with col3:
                            st.metric("**仮査定金額**", f"**{valuation/10000:,.0f} 万円**")

                        st.info(
                            f"※ {area_input}㎡ × {avg_unit_price/10000:,.1f}万円/㎡ = "
                            f"{valuation/10000:,.0f}万円（参考値です）"
                        )

                        st.subheader("📋 参考にした取引事例")
                        df = build_table_df(transactions)
                        st.dataframe(df, use_container_width=True, hide_index=True)
                    else:
                        st.warning("㎡単価を算出できる取引データがありませんでした。")
