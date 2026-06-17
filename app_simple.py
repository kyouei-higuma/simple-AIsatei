"""
簡易AI査定アプリ（お客様向け・HP用）
社員向けの本番査定は main.py（地図・成約事例一覧あり＝対面説明用）。
本ファイルはお客様が手軽に使う版のため、画面上では地図・参照成約事例の一覧は出さない。
"""

import hashlib
import html
import io
import logging
import math
import os
import re
from urllib.parse import unquote
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import streamlit as st
import requests
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

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
/* ── 全体 ── */
.block-container {
    padding-top: 0rem !important;
    padding-bottom: 0rem !important;
    margin-top: 0 !important;
    max-width: 720px !important;
    background: #f0f6ff !important;
}
[data-testid="stToolbar"], [data-testid="stHeader"], footer { display: none !important; }
.stDeployButton, #stDecoration { display: none !important; }

/* ── ラジオボタン ── */
.stRadio label { font-weight: 600; color: #1a3a6b; }
.stRadio div[role="radiogroup"] label {
    background: #edf4fb;
    border: 1.5px solid #c5d8ee;
    border-radius: 8px;
    padding: 6px 14px;
    margin-right: 6px;
    color: #1a3a6b;
    font-weight: 600;
}
.stRadio div[role="radiogroup"] label:hover {
    background: #d6eaf8;
    border-color: #1a5fa8;
}

/* ── 入力フォーム ── */
.stTextInput input, .stNumberInput input {
    border: 1.5px solid #c5d8ee !important;
    border-radius: 8px !important;
    background: #fff !important;
    color: #1a3a6b !important;
    padding: 8px 12px !important;
}
.stTextInput input:focus, .stNumberInput input:focus {
    border-color: #1a5fa8 !important;
    box-shadow: 0 0 0 2px rgba(26,95,168,0.15) !important;
}

/* ── ボタン ── */
.stButton > button {
    background: #1a5fa8 !important;
    color: #fff !important;
    border: none !important;
    border-radius: 50px !important;
    padding: 10px 28px !important;
    font-weight: 700 !important;
    font-size: 15px !important;
    width: 100% !important;
    transition: opacity 0.15s !important;
}
.stButton > button:hover { opacity: 0.88 !important; }

/* ── フォーム送信ボタン ── */
.stFormSubmitButton > button {
    background: #1a5fa8 !important;
    color: #fff !important;
    border: none !important;
    border-radius: 50px !important;
    padding: 12px 28px !important;
    font-weight: 700 !important;
    font-size: 16px !important;
    width: 100% !important;
}

/* ── チェックボックス ── */
.stCheckbox label { color: #1a3a6b; font-weight: 600; }

/* ── セクション見出し ── */
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
    color: #0f2a5e;
    font-weight: 700;
}

/* ── info・warning ── */
.stAlert {
    border-radius: 10px !important;
    border-left: 4px solid #1a5fa8 !important;
}

/* ── サイドバー（ダークネイビー） ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #12213d 0%, #1a3560 100%) !important;
    border-right: none !important;
    box-shadow: 3px 0 16px rgba(0,0,0,0.18) !important;
}
[data-testid="stSidebar"] * { color: #dce8ff !important; }
[data-testid="stSidebar"] .stMarkdown h3 {
    color: #7eb6ff !important;
    font-size: 0.78rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
    border-bottom: 1px solid rgba(126,182,255,0.25) !important;
    padding-bottom: 5px !important;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.1) !important;
}
[data-testid="stSidebar"] [data-testid="stNotification"] {
    background: rgba(126,182,255,0.15) !important;
    border: 1px solid rgba(126,182,255,0.3) !important;
    border-radius: 8px !important;
}

/* ── selectbox ── */
.stSelectbox select {
    border: 1.5px solid #c5d8ee !important;
    border-radius: 8px !important;
    color: #1a3a6b !important;
}
</style>
""", unsafe_allow_html=True)

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
M2_TO_TSUBO = 3.30578
LAND_MARKUP_RATE = 1.20
LAND_UNDERPRICE_VS_MEAN_YEN = 2_000_000
FOLLOWUP_NOTICE = "後日、弊社担当者からご連絡を差し上げる場合があります。ご了承ください。"

# 査定完了時の Google Chat 自動通知（一時停止中。復旧時は True に変更）
# 「担当者査定はこちら」ボタンからの手動通知は WEBHOOK_URL が設定されていれば常に送信されます。
WEBHOOK_AUTO_NOTIFY_ENABLED = False

def _normalize_webhook_url(raw: Optional[str]) -> str:
    """
    環境変数や secrets に付いた引用符・前後空白・KEY=VALUE 形式を正規化して URL だけを返す。
    例: 'WEBHOOK_URL=https://...' → 'https://...'
        '"https://..."'          → 'https://...'
    """
    if raw is None:
        return ""
    s = str(raw).strip().strip("'\"").strip()
    # "KEY=value" 形式で誤入力された場合（例: WEBHOOK_URL=https://...）
    if not s.lower().startswith("http") and "=" in s:
        s = s.split("=", 1)[1].strip().strip("'\"").strip()
    return s


def _get_webhook_url() -> Tuple[Optional[str], str]:
    """
    Google Chat Incoming Webhook の URL。
    非空の環境変数 WEBHOOK_URL を最優先（Cloud Run 等で secrets と食い違うと通知が別 URL になるのを防ぐ）。
    戻り値: (url or None, "env" | "st.secrets" | "none")
    """
    env_u = _normalize_webhook_url(os.environ.get("WEBHOOK_URL"))
    if env_u:
        return env_u, "env"
    if hasattr(st, "secrets"):
        try:
            sec_u = _normalize_webhook_url(str(st.secrets["WEBHOOK_URL"]))
            if sec_u:
                return sec_u, "st.secrets"
        except Exception:
            pass
    return None, "none"


def _build_ai_notify_chat_body(
    ptype_display: str,
    address: str,
    name: str,
    phone: str,
    email: str,
    land_m2: Any,
    bldg_m2: Any,
    excl_m2: Any,
    age: Any,
    valuation: Optional[float] = None,
    avg_unit_price: Optional[float] = None,
    csv_count: Optional[int] = None,
    land_volume_zone_caption: Optional[str] = None,
    building_volume_zone_caption: Optional[str] = None,
) -> Dict[str, str]:
    """Google Chat Incoming Webhook 用ペイロード（text のみ）。"""
    lines = [
        "【AI査定】新規お問い合わせ",
        "",
        "■ お客様情報",
        f"お名前: {name}",
        f"電話番号: {phone}",
        f"メール: {email}",
        "",
        "■ 物件情報",
        f"住所: {address}",
        f"種別: {ptype_display}",
        f"土地: {land_m2}㎡ / 建物: {bldg_m2}㎡ / 専有: {excl_m2}㎡",
        f"築年数: {age}年",
    ]
    if valuation is not None:
        lines += [
            "",
            "■ 査定結果",
            f"仮査定金額: *{valuation/10000:,.0f}万円*",
        ]
        if avg_unit_price:
            tsubo = (avg_unit_price / 10000) * M2_TO_TSUBO
            lines.append(f"坪単価: {tsubo:.1f}万円/坪（㎡単価: {avg_unit_price/10000:.1f}万円/㎡）")
        if csv_count is not None:
            lines.append(f"参照事例数: {csv_count}件")
        if land_volume_zone_caption:
            lines.append(f"土地ボリュームゾーン: {land_volume_zone_caption}")
        if building_volume_zone_caption:
            lines.append(f"建物ボリュームゾーン: {building_volume_zone_caption}")
    lines += ["", f"送信日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    return {"text": "\n".join(lines)}


def _build_staff_valuation_request_body(sr: Dict[str, Any]) -> Dict[str, str]:
    """担当者査定希望ボタン用の Chat ペイロード。"""
    valuation = sr.get("valuation")
    val_line = f"仮査定金額: {valuation / 10000:,.0f}万円" if valuation else "仮査定金額: -"
    lines = [
        "担当者査定希望",
        "",
        f"お名前: {sr.get('contact_name') or '-'}",
        f"電話番号: {sr.get('contact_phone') or '-'}",
        f"メール: {sr.get('contact_email') or '-'}",
        f"住所: {sr.get('address') or '-'}",
        f"種別: {sr.get('property_type') or '-'}",
        val_line,
        "",
        f"送信日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    return {"text": "\n".join(lines)}


def _mark_url_auto_valuation_processed() -> None:
    """GET 自動査定とフォーム査定の二重実行を防ぐため、処理済み署名を記録する。"""
    bundle = parse_url_auto_valuation_bundle_if_present()
    if bundle is not None:
        sig, _ = bundle
        st.session_state.setdefault("_url_auto_val_state", {})[sig] = "ok"


def send_inquiry_to_webhook(body: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Chat Webhook に JSON を POST。ペイロードは原則 {"text":"..."}
    """
    env_set = bool(os.environ.get("WEBHOOK_URL", "").strip())
    logger.info("[webhook] WEBHOOK_URL env set: %s", env_set)
    secrets_set = False
    if hasattr(st, "secrets"):
        try:
            secrets_set = "WEBHOOK_URL" in st.secrets and bool(str(st.secrets["WEBHOOK_URL"]).strip())
        except Exception:
            secrets_set = False
    logger.info("[webhook] WEBHOOK_URL st.secrets set: %s", secrets_set)
    url, url_source = _get_webhook_url()
    logger.info("[webhook] Resolved WEBHOOK URL configured: %s (source=%s)", bool(url), url_source)
    if not url:
        logger.warning("[webhook] WEBHOOK_URL not configured; skip POST")
        return False, None
    try:
        # Google Chat は application/json が一般的（charset 付きで弾くプロキシ対策）
        resp = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=(5, 15),
        )
        logger.info("[webhook] POST status_code=%s", resp.status_code)
        if 200 <= resp.status_code < 300:
            return True, None
        err_msg = f"HTTP {resp.status_code}"
        try:
            err_body = resp.text[:500] if resp.text else ""
            if err_body:
                err_msg += f": {err_body}"
        except Exception as ex:
            logger.warning("[webhook] response body read error: %s", ex)
        logger.warning("[webhook] POST failed: %s", err_msg)
        return False, err_msg
    except requests.exceptions.Timeout:
        logger.exception("[webhook] timeout")
        return False, "タイムアウト（接続が遅い可能性があります）"
    except requests.exceptions.ConnectionError:
        logger.exception("[webhook] connection error")
        return False, "接続エラー（URLまたはネットワークを確認してください）"
    except Exception as e:
        logger.exception("[webhook] unexpected error: %s", e)
        return False, str(e)[:200]


PROPERTY_TYPE_TO_CSV_TYPE = {
    "土地": ["売地", "土地", "宅地"],
    "中古住宅（戸建て）": ["中古戸建", "既存住宅", "中古住宅", "一戸建"],
    "中古マンション": ["中古マンション", "既存ＭＳ", "マンション", "既存マンション"],
}


def _query_param_first(param: str) -> Optional[str]:
    """GET クエリの先頭値（URLエンコード対応）。"""
    try:
        if not hasattr(st, "query_params") or param not in st.query_params:
            return None
        raw = st.query_params[param]
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        if raw is None:
            return None
        return unquote(str(raw).strip())
    except Exception as e:
        logger.debug("[url_auto] query param %s: %s", param, e)
        return None


def _parse_query_float(val: Optional[str], default: float = 0.0) -> float:
    if val is None or str(val).strip() == "":
        return default
    try:
        return float(str(val).strip().replace(",", ""))
    except (TypeError, ValueError):
        return default


def _map_query_ptype_to_property_type(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = unquote(str(raw)).strip()
    if s in PROPERTY_TYPE_TO_CSV_TYPE:
        return s
    key = s.lower().replace(" ", "").replace("_", "")
    asc = {
        "tochi": "土地",
        "land": "土地",
        "1": "土地",
        "kodate": "中古住宅（戸建て）",
        "house": "中古住宅（戸建て）",
        "detached": "中古住宅（戸建て）",
        "2": "中古住宅（戸建て）",
        "mansion": "中古マンション",
        "ms": "中古マンション",
        "condo": "中古マンション",
        "3": "中古マンション",
    }
    return asc.get(key)


def _url_auto_signature(
    ptype: str,
    address: str,
    name: str,
    phone: str,
    email: str,
    land_m2_s: str,
    bldg_m2_s: str,
    excl_m2_s: str,
    age_s: str,
) -> str:
    raw = "|".join([ptype, address, name, phone, email, land_m2_s, bldg_m2_s, excl_m2_s, age_s])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_url_auto_valuation_bundle_if_present() -> Optional[Tuple[str, Dict[str, Any]]]:
    """form.html 等からの GET パラメータ一式。自動査定対象なら (signature, fields) を返す。"""
    pt_raw = _query_param_first("ptype")
    addr = (_query_param_first("address") or "").strip()
    name = (_query_param_first("name") or "").strip()
    phone = (_query_param_first("phone") or "").strip()
    email = (_query_param_first("email") or "").strip()
    if not pt_raw or not addr or not name or not phone or not email:
        return None
    ptype_jp = _map_query_ptype_to_property_type(pt_raw)
    if ptype_jp is None:
        logger.warning("[url_auto] Unknown ptype=%s", pt_raw)
        return None
    lm = _parse_query_float(_query_param_first("land_m2"))
    bm = _parse_query_float(_query_param_first("bldg_m2"))
    ex = _parse_query_float(_query_param_first("excl_m2"))
    try:
        age_v = int(float(str(_query_param_first("age") or "0").strip()))
        age_v = max(0, min(100, age_v))
    except (TypeError, ValueError):
        age_v = 0
    if ptype_jp == "土地" and lm <= 0:
        lm = max(lm, 1.0)
    if ptype_jp == "中古住宅（戸建て）" and lm <= 0 and bm <= 0:
        lm, bm = max(lm, 1.0), max(bm, 1.0)
    if ptype_jp == "中古マンション" and ex <= 0:
        ex = max(ex, 1.0)
    sig = _url_auto_signature(
        pt_raw.strip(),
        addr,
        name,
        phone,
        email,
        str(lm),
        str(bm),
        str(ex),
        str(age_v),
    )
    fields = {
        "ptype_jp": ptype_jp,
        "address": addr,
        "contact_name": name,
        "contact_phone": phone,
        "contact_email": email,
        "land_area_input": lm,
        "building_area_input": bm,
        "exclusive_area_input": ex,
        "building_age": age_v,
    }
    return sig, fields


def _run_valuation_pipeline(
    *,
    address: str,
    property_type: str,
    land_area_input: float,
    building_area_input: float,
    exclusive_area_input: float,
    building_age: Any,
    contact_name: str,
    contact_phone: str,
    contact_email: str,
    radius_km: float,
    corner_check: bool,
) -> bool:
    """査定処理（フォーム送信・GET自動共有）。査定結果を保存し通知まで完了したら True。"""
    # 面積入力の確定
    if property_type == "土地":
        area_input = land_area_input
    elif property_type == "中古住宅（戸建て）":
        area_input = land_area_input + building_area_input
    else:
        area_input = exclusive_area_input

    import time as _time
    _t_start = _time.perf_counter()
    status_text = st.empty()
    with st.spinner("査定計算を開始します..."):
        status_text.info("📍 住所を座標に変換中...")
        coords = geocode_address(address)
        logger.info("[perf] geocode: %.3fs", _time.perf_counter() - _t_start)

        if not coords:
            st.error("住所の変換（ジオコーディング）に失敗しました。住所を正しく入力するか、地図から選択してください。")
            return False

        lat, lon = coords
        status_text.info("📍 取引データを照合中...")
        _t1 = _time.perf_counter()
        search_radius_m = float(radius_km) * 1000
        csv_raw = st.session_state.get("csv_cases", [])
        csv_df = st.session_state.get("csv_df")
        csv_features = filter_csv_by_distance(csv_raw, lat, lon, search_radius_m, csv_df=csv_df)
        logger.info("[perf] filter_distance: %.3fs", _time.perf_counter() - _t1)

        if not csv_features:
            st.warning(f"半径{radius_km}km以内に取引事例が見つかりませんでした。別の住所でお試しください。")
            st.session_state.search_result = {
                "has_valuation": False,
                "address": address, "lat": lat, "lon": lon,
                "property_type": property_type, "radius_km": radius_km,
            }
            return False

        status_text.info("📊 データを分析し、査定金額を算出中...")
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
            return False

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
            return False

        building_age_val = int(building_age) if building_age is not None and building_age > 0 else None
        building_age_correction = 1.0 if property_type == "土地" else get_building_age_correction_factor(building_age_val)
        kakuti_rate = get_corner_correction_rate(corner_check)

        csv_2km = None
        csv_2km_land = None
        csv_500m_land = None
        if property_type == "中古住宅（戸建て）":
            if building_age_val is not None and building_age_val <= 34:
                csv_2km_raw = filter_features_by_distance(csv_features, lat, lon, 1000)
                csv_2km = apply_case_filters(csv_2km_raw, filter_type, 0, 50, filter_contract_value)
                csv_2km_land = apply_case_filters(csv_2km_raw, PROPERTY_TYPE_TO_CSV_TYPE.get("土地", []), 0, 50, filter_contract_value)

            csv_500m_raw = filter_features_by_distance(csv_features, lat, lon, 500)
            land_types = PROPERTY_TYPE_TO_CSV_TYPE.get("土地", ["売地", "土地", "宅地"])
            csv_500m_land = apply_case_filters(csv_500m_raw, land_types, 0, 50, filter_contract_value)

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

        if WEBHOOK_AUTO_NOTIFY_ENABLED:
            status_text.info("✉️ Google Chat に通知送信中...")
            webhook_body = _build_ai_notify_chat_body(
                ptype_display=property_type,
                address=address,
                name=(contact_name or "").strip(),
                phone=(contact_phone or "").strip(),
                email=(contact_email or "").strip(),
                land_m2=land_area_input,
                bldg_m2=building_area_input,
                excl_m2=exclusive_area_input,
                age=int(building_age) if building_age is not None else 0,
                valuation=valuation,
                avg_unit_price=avg_unit_price,
                csv_count=csv_count,
                land_volume_zone_caption=land_volume_zone_caption,
                building_volume_zone_caption=building_volume_zone_caption,
            )
            ok_wh, err_wh = send_inquiry_to_webhook(webhook_body)
            if ok_wh:
                logger.info("[webhook] Notification sent OK")
                st.toast("✅ Google Chat に通知を送信しました", icon="✅")
            else:
                logger.warning("[webhook] Notification failed or skipped: %s", err_wh)
                _wh_url_check, _wh_src_check = _get_webhook_url()
                if not _wh_url_check:
                    st.warning("⚠️ WEBHOOK_URL が未設定です。Render の環境変数を確認してください。")
                else:
                    st.warning(f"⚠️ Google Chat 通知エラー（{_wh_src_check}）: {err_wh or '詳細はサーバーログを確認'}")
        else:
            logger.info("[webhook] Temporarily disabled; skip notification")

        # 価格グラフ（UI表示用）のみここで生成。PDF生成は結果表示後に遅延実行
        _t2 = _time.perf_counter()
        price_chart = build_price_trend_chart(csv_filtered)
        logger.info("[perf] chart: %.3fs", _time.perf_counter() - _t2)

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
            "avg_land_500m": avg_land_500m, "pdf_bytes": None,  # 結果表示後に生成
            "price_chart": price_chart,
            "land_volume_zone_caption": land_volume_zone_caption,
            "building_volume_zone_caption": building_volume_zone_caption,
            "contact_name": (contact_name or "").strip(),
            "contact_phone": (contact_phone or "").strip(),
            "contact_email": (contact_email or "").strip(),
            "staff_request_sent": False,
        }
        st.session_state.search_result = res_data
        _mark_url_auto_valuation_processed()

        logger.info("[perf] total pipeline: %.3fs", _time.perf_counter() - _t_start)
        status_text.success("✅ 査定が完了しました！結果を表示します。")
        return True


def _get_map_zoom_for_radius(radius_km: float) -> int:
    zoom = max(11, min(15, round(14 - math.log2(max(0.5, radius_km)))))
    return zoom


def _normalize_address_for_geocode(address: str) -> str:
    """全角数字・ハイフン類を半角に変換し、前後の空白を除去する。"""
    # 全角数字 10文字 + ハイフン類 4文字 = 14文字ずつ
    table = str.maketrans(
        "０１２３４５６７８９－‐―ー",
        "0123456789----",
    )
    return address.strip().translate(table)


def _shorten_address(address: str) -> list:
    """
    番地以降を段階的に除いた候補リストを返す。
    例: "旭川市永山3条12丁目1-2" → ["旭川市永山3条12丁目1-2", "旭川市永山3条12丁目", "旭川市永山3条"]
    """
    import re
    candidates = [address]
    # 丁目以降を除去
    m = re.sub(r"(\d+丁目).*", r"\1", address)
    if m != address:
        candidates.append(m)
    # 丁目ごと除去（条だけ残す）
    m2 = re.sub(r"\d+丁目.*", "", address).strip()
    if m2 and m2 not in candidates:
        candidates.append(m2)
    return candidates


def _geocode_gsi(address: str) -> Optional[Tuple[float, float]]:
    for candidate in _shorten_address(_normalize_address_for_geocode(address)):
        try:
            url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
            resp = requests.get(url, params={"q": candidate}, timeout=10)
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


def _geocode_heartrails(address: str) -> Optional[Tuple[float, float]]:
    """HeartRails Geocoder（日本住所専用フォールバック）。"""
    for candidate in _shorten_address(_normalize_address_for_geocode(address)):
        try:
            url = "https://geoapi.heartrails.com/api/json"
            resp = requests.get(url, params={"method": "searchByAddress", "address": candidate}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            locations = (data.get("response") or {}).get("location", [])
            if locations:
                lat = float(locations[0].get("y", 0))
                lon = float(locations[0].get("x", 0))
                if lat and lon:
                    return (lat, lon)
        except Exception:
            pass
    return None


def _geocode_nominatim(address: str) -> Optional[Tuple[float, float]]:
    for candidate in _shorten_address(_normalize_address_for_geocode(address)):
        try:
            from geopy.geocoders import Nominatim
            from geopy.extra.rate_limiter import RateLimiter
            geolocator = Nominatim(user_agent="real_estate_app")
            geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
            query = f"日本 {candidate}" if "日本" not in candidate else candidate
            location = geocode(query)
            if location:
                return (location.latitude, location.longitude)
        except Exception:
            pass
    return None


@st.cache_data(ttl=86400)
def _geocode_address_cached(address: str) -> Optional[Tuple[float, float]]:
    if not address or not address.strip():
        return None
    result = _geocode_gsi(address)
    if result:
        logger.info("[geocode] GSI OK: %s", address)
        return result
    result = _geocode_heartrails(address)
    if result:
        logger.info("[geocode] HeartRails OK: %s", address)
        return result
    result = _geocode_nominatim(address)
    if result:
        logger.info("[geocode] Nominatim OK: %s", address)
    else:
        logger.warning("[geocode] All geocoders failed: %s", address)
    return result


def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    result = _geocode_address_cached(address)
    if result is None and address and address.strip():
        st.error("住所を緯度・経度に変換できませんでした。住所を確認してください。")
    return result


@st.cache_data(ttl=86400)
def reverse_geocode(lat: float, lon: float) -> Optional[str]:
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
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _haversine_m_np(center_lon, center_lat, lon_arr, lat_arr):
    R = 6371000.0
    phi1 = math.radians(center_lat)
    phi2 = np.radians(lat_arr.astype(np.float64, copy=False))
    dphi = np.radians(lat_arr.astype(np.float64, copy=False) - center_lat)
    dlambda = np.radians(lon_arr.astype(np.float64, copy=False) - center_lon)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * (np.sin(dlambda / 2.0) ** 2)
    a = np.clip(a, 0.0, 1.0)
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return R * c


def parse_numeric(value, suffixes=(",", " ", "円", "/m²", "㎡", "m²", "万円", "万")):
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(" ", "")
    is_man = "万円" in s or "万" in s
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


def _parse_area_to_sqm(value):
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace(",", "").replace(" ", "").replace("㎡", "").replace("m²", "").strip()
    if not s: return None
    is_tsubo = "坪" in s
    m = re.search(r"([\d\.]+)", s)
    if m:
        try:
            val = float(m.group(1))
            if is_tsubo:
                val = val / 0.3025
            return val
        except ValueError:
            return None
    return None


def _parse_price_man(value):
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, (int, float)):
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
                val *= 10000
            return int(val)
        except ValueError:
            return None
    return None


def _parse_date_ymd(s):
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


def _parse_construction_date(cy_val):
    if cy_val is None or (isinstance(cy_val, float) and pd.isna(cy_val)):
        return None
    s = str(cy_val).strip()
    if not s:
        return None
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return CSV_PATH_3YEARS


def _is_valid_coord(val) -> bool:
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
def load_data(csv_path: str, csv_mtime: float):
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


def _load_case_from_row(row, columns, df_index):
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
    age_at_contract = None
    const_val = get_val(col_map["const_year"])
    construction_dt = _parse_construction_date(const_val)
    if contract_dt and construction_dt:
        delta = contract_dt - construction_dt
        age_at_contract = max(0, delta.days / 365.25)
    elif pd.notna(const_val) if const_val is not None else False:
        try:
            val = float(str(const_val).replace("年", "").strip())
            if val < 150:
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


def csv_row_to_feature(row, center_lon, center_lat, df=None):
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


def save_geocodes_to_csv(df) -> None:
    if df is None or df.empty:
        return
    try:
        df.to_csv(CSV_PATH_3YEARS, index=False, encoding="utf-8")
    except Exception:
        pass


def _build_radius_hint_from_df(csv_df, center_lat, center_lon, radius_m):
    hint = {}
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


def filter_features_by_distance(features, center_lat, center_lon, radius_m):
    out = []
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


_MAX_GEOCODE_PER_DISTANCE_FILTER = 50  # 座標未取得行のジオコーディング上限（大幅削減）


@st.cache_data(
    ttl=3600,
    hash_funcs={
        list: lambda x: len(x),
        pd.DataFrame: lambda df: (tuple(df.columns.tolist()), df.shape),
    },
    show_spinner=False,
)
def filter_csv_by_distance(csv_cases, center_lat, center_lon, radius_m, csv_df=None, progress_placeholder=None):
    """
    高速版: NumPy ベクトル演算で半径内インデックスを抽出し、
    該当行だけを Python ループで処理する（従来比 10〜50 倍高速）。
    """
    import time as _time
    _t0 = _time.perf_counter()

    # ── FAST PATH: csv_df に lat/lon があるとき ──────────────────
    if csv_df is not None and "latitude" in csv_df.columns and "longitude" in csv_df.columns:
        lat_a = pd.to_numeric(csv_df["latitude"], errors="coerce").to_numpy(dtype=np.float64)
        lon_a = pd.to_numeric(csv_df["longitude"], errors="coerce").to_numpy(dtype=np.float64)
        valid = np.isfinite(lat_a) & np.isfinite(lon_a)

        dist = np.full(len(csv_df), np.inf, dtype=np.float64)
        if np.any(valid):
            dist[valid] = _haversine_m_np(center_lon, center_lat, lon_a[valid], lat_a[valid])

        within_mask = dist <= radius_m
        within_idx_set = set(csv_df.index[within_mask].tolist())
        no_coord_idx_set = set(csv_df.index[~valid].tolist())

        # csv_cases を df_index で引けるよう辞書化（1回だけ）
        cases_by_idx: dict = {}
        cases_no_idx: list = []
        for row in csv_cases:
            i = row.get("_df_index")
            if i is not None:
                cases_by_idx[i] = row
            else:
                cases_no_idx.append(row)

        features = []
        geocode_count = 0

        # ① 半径内・座標あり → csv_df から直接 lat/lon をセットして変換
        for idx in within_idx_set:
            row = cases_by_idx.get(idx)
            if row is None:
                continue
            try:
                row["lat"] = float(csv_df.at[idx, "latitude"])
                row["lon"] = float(csv_df.at[idx, "longitude"])
            except Exception:
                pass
            feat, needs_save = csv_row_to_feature(row, center_lon, center_lat, csv_df)
            if needs_save:
                geocode_count += 1
            features.append(feat)

        # ② 座標なし → ジオコーディング（上限 _MAX_GEOCODE_PER_DISTANCE_FILTER 件）
        geocode_budget = _MAX_GEOCODE_PER_DISTANCE_FILTER
        for idx in no_coord_idx_set:
            if geocode_budget <= 0:
                break
            row = cases_by_idx.get(idx)
            if row is None:
                continue
            feat, needs_save = csv_row_to_feature(row, center_lon, center_lat, csv_df)
            if needs_save:
                geocode_count += 1
                geocode_budget -= 1
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates", [0, 0])
            if len(coords) >= 2:
                plon, plat = float(coords[0]), float(coords[1])
                if (plon != 0 or plat != 0) and haversine_distance(center_lon, center_lat, plon, plat) <= radius_m:
                    features.append(feat)

        if geocode_count > 0 and csv_df is not None and not csv_df.empty:
            save_geocodes_to_csv(csv_df)

        logger.info("[perf] filter_csv_by_distance fast: %d total → %d within %.0fm in %.3fs",
                    len(csv_cases), len(features), radius_m, _time.perf_counter() - _t0)
        return features

    # ── SLOW FALLBACK: csv_df なし（後方互換）────────────────────
    geocode_budget = _MAX_GEOCODE_PER_DISTANCE_FILTER
    features = []
    geocode_count = 0
    for row in csv_cases:
        if geocode_budget <= 0 and row.get("lat") is None:
            continue
        feat, needs_save = csv_row_to_feature(row, center_lon, center_lat, None)
        if needs_save:
            geocode_count += 1
            geocode_budget -= 1
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [0, 0])
        if len(coords) >= 2:
            plon, plat = float(coords[0]), float(coords[1])
            if (plon != 0 or plat != 0) and haversine_distance(center_lon, center_lat, plon, plat) <= radius_m:
                features.append(feat)
    logger.info("[perf] filter_csv_by_distance fallback: %d total → %d in %.3fs",
                len(csv_cases), len(features), _time.perf_counter() - _t0)
    return features


def apply_case_filters(csv_features, type_selected, age_min, age_max, contract_period):
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
                if dt < datetime(now.year - 1, now.month, now.day):
                    continue
            elif contract_period == "2years":
                if dt < datetime(now.year - 2, now.month, now.day):
                    continue
            elif contract_period == "3years":
                if dt < datetime(now.year - 3, now.month, now.day):
                    continue
            elif contract_period == "5years":
                if dt < datetime(now.year - 5, now.month, now.day):
                    continue
        filtered.append(f)
    return filtered


def _deal_area_sqm_for_unit_price(p):
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


def get_unit_price(feature):
    p = feature.get("properties", {})
    total = parse_numeric(p.get("u_transaction_price_total_ja"))
    area = _deal_area_sqm_for_unit_price(p)
    if total and area and area > 0:
        return total / area
    return None


DETACHED_DEPRECIATION_YEARS = 20
STANDARD_NEW_BUILDING_PRICE = 15_000_000

_BUILDING_TSUBO_REF_BANDS = [
    {"a1": 1, "a2": 3, "lo": 544_000, "hi": 734_000, "med": 616_000, "label": "築2～3年帯"},
    {"a1": 4, "a2": 5, "lo": 458_000, "hi": 665_000, "med": 569_000, "label": "築4～5年帯"},
    {"a1": 6, "a2": 10, "lo": 381_000, "hi": 531_000, "med": 449_000, "label": "築6～10年帯"},
    {"a1": 11, "a2": 15, "lo": 290_000, "hi": 412_000, "med": 348_000, "label": "築11～15年帯"},
    {"a1": 16, "a2": 20, "lo": 202_000, "hi": 334_000, "med": 275_000, "label": "築16～20年帯"},
]
_REF_TSUBO_BASELINE_YEN = 616_000.0
_BUILDING_TSUBO_EXTRAP_SLOPE = (275_000.0 - 348_000.0) / (20.0 - 15.0)
_BUILDING_TSUBO_EXTRAP_FLOOR = 40_000.0


def _lookup_building_tsubo_band(age_years):
    if age_years <= 0:
        return None
    for b in _BUILDING_TSUBO_REF_BANDS:
        if b["a1"] <= age_years <= b["a2"]:
            return b
    return None


def _median_tsubo_yen_for_age(age_years):
    if age_years <= 0:
        return _REF_TSUBO_BASELINE_YEN
    a = max(0, int(round(age_years)))
    band = _lookup_building_tsubo_band(a)
    if band is not None:
        return float(band["med"])
    y = 275_000.0 + (float(a) - 20.0) * _BUILDING_TSUBO_EXTRAP_SLOPE
    return max(_BUILDING_TSUBO_EXTRAP_FLOOR, y)


def format_building_volume_zone_caption(building_age):
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
    return f"建物坪単価の参考（外挿）：中央目安{med:,.0f}円/坪（築{a}年。参考テーブルは築20年帯までのため外挿）"


def _building_age_market_ratio(age_years):
    if age_years is None or age_years <= 0:
        return 1.0
    med = _median_tsubo_yen_for_age(float(age_years))
    return med / _REF_TSUBO_BASELINE_YEN if _REF_TSUBO_BASELINE_YEN > 0 else 1.0


def get_building_residual_rate_20y(age_years):
    if age_years is None or age_years < 0:
        return 1.0
    if age_years >= DETACHED_DEPRECIATION_YEARS:
        return 0.0
    return max(0.0, (DETACHED_DEPRECIATION_YEARS - age_years) / DETACHED_DEPRECIATION_YEARS)


def compute_valuation(property_type, avg_unit_price, building_age_correction, land_area, building_area, exclusive_area, kakuti_rate=0.0, subject_building_age=None, csv_features=None, csv_features_2km=None, csv_features_2km_land=None, csv_features_500m_land=None, land_volume_zone_caption=None):
    if property_type == "土地":
        avg_with_markup = avg_unit_price * LAND_MARKUP_RATE
        land_val = land_area * avg_with_markup
        return (land_val * (1.0 + kakuti_rate), land_val * (1.0 + kakuti_rate), None, None, land_volume_zone_caption)
    elif property_type == "中古住宅（戸建て）" and csv_features is not None:
        result = _compute_valuation_detached(csv_features, land_area, subject_building_age, kakuti_rate, subject_building_area_sqm=building_area, csv_features_2km=csv_features_2km, csv_features_2km_land=csv_features_2km_land, avg_unit_price=avg_unit_price, csv_features_500m_land=csv_features_500m_land)
        if result is not None:
            return result
    land_val = land_area * avg_unit_price
    bldg_val = building_area * avg_unit_price * building_age_correction if property_type == "中古住宅（戸建て）" else 0
    base = land_val + (bldg_val if property_type == "中古住宅（戸建て）" else exclusive_area * avg_unit_price * building_age_correction)
    if property_type == "中古住宅（戸建て）":
        return base * (1.0 + kakuti_rate), land_val * (1.0 + kakuti_rate), bldg_val * (1.0 + kakuti_rate), None, None
    return base * (1.0 + kakuti_rate), None, None, None, None


def _compute_valuation_detached(csv_features, land_area, subject_building_age, kakuti_rate, subject_building_area_sqm=0.0, csv_features_2km=None, csv_features_2km_land=None, avg_unit_price=None, csv_features_500m_land=None):
    avg_land_500m = None
    if csv_features_500m_land:
        pairs_500m = _collect_land_transaction_pairs(csv_features_500m_land)
        if pairs_500m:
            avg_land_500m, _, _ = _land_volume_zone_avg_from_pairs(pairs_500m)
    land_pairs = []
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
    if subject_building_age is not None and subject_building_age >= 44:
        return land_value_base, land_value_base, 0, avg_land_500m, land_zone_caption
    if subject_building_age is None or subject_building_age >= 35:
        return land_value_base, land_value_base, 0, avg_land_500m, land_zone_caption
    if csv_features_2km and subject_building_age is not None and subject_building_age <= 34:
        building_yen_per_sqm = []
        comp_ages = []
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
            return land_value_base + avg_building, land_value_base, avg_building, avg_land_500m, land_zone_caption
    if subject_building_age is None or subject_building_age >= 25:
        return land_value_base, land_value_base, 0, avg_land_500m, land_zone_caption
    residual = get_building_residual_rate_20y(float(subject_building_age))
    building_value = STANDARD_NEW_BUILDING_PRICE * residual
    return land_value_base + building_value, land_value_base, building_value, avg_land_500m, land_zone_caption


def format_valuation_formula(property_type, valuation, avg_unit_price, building_age_correction, land_area, building_area, exclusive_area, kakuti_rate=0.0, building_breakdown=None, land_breakdown=None):
    up = avg_unit_price / 10000
    val_man = valuation / 10000
    kakuti_pct = kakuti_rate * 100
    kakuti_str = f" × (1 + 画地補正{kakuti_pct:+.0f}%)" if kakuti_rate != 0 else ""
    if property_type == "土地":
        base_str = f"{land_area:.1f}㎡ × {up:.1f}万円/㎡"
        return (r"土地面積 \times ㎡単価 \times (1 + 画地補正) = 査定金額", f"{base_str}{kakuti_str} = {val_man:,.0f}万円")
    if property_type == "中古住宅（戸建て）":
        building_val = building_breakdown if building_breakdown is not None else 0
        if land_breakdown is not None and land_area > 0:
            denom = land_area * (1.0 + kakuti_rate)
            land_unit_man = (land_breakdown / denom) / 10000 if denom > 0 else up
            if building_val == 0:
                base_str = f"{land_area:.1f}㎡×{land_unit_man:.1f}万円/㎡"
                return (r"土地面積 \times 土地単価 \times (1 + 画地補正) = 査定金額", f"{base_str}{kakuti_str} = {val_man:,.0f}万円")
            bldg_man = building_val / 10000
            base_str = f"({land_area:.1f}㎡×{land_unit_man:.1f}万円/㎡){kakuti_str} + 建物{bldg_man:,.0f}万円"
            return (r"(土地面積 \times 土地単価 \times (1 + 画地補正)) + 建物評価額 = 査定金額", f"{base_str} = {val_man:,.0f}万円")
        adj = (avg_unit_price * building_age_correction) / 10000
        if building_age_correction != 1.0:
            base_str = f"({land_area:.1f}×{up:.1f} + {building_area:.1f}×{adj:.1f})"
        else:
            base_str = f"({land_area:.1f}×{up:.1f} + {building_area:.1f}×{up:.1f})"
        return (r"(土地 + 建物) \times (1 + 画地補正) = 査定金額", f"{base_str}{kakuti_str} = {val_man:,.0f}万円")
    adj = (avg_unit_price * building_age_correction) / 10000
    base_str = f"{exclusive_area:.1f}㎡ × {adj:.1f}万円/㎡"
    return (r"専有面積 \times ㎡単価 \times (1 + 画地補正) = 査定金額", f"{base_str}{kakuti_str} = {val_man:,.0f}万円")


def get_corner_correction_rate(is_corner):
    return 0.05 if is_corner else 0.0


def get_road_width_correction_rate(road_width_m):
    if road_width_m < 4.0: return -0.10
    if road_width_m < 6.0: return -0.05
    if road_width_m < 8.0: return 0.0
    return 0.03


def get_frontage_correction_rate(frontage_m):
    if frontage_m < 4.0: return -0.15
    if frontage_m < 8.0: return -0.05
    if frontage_m < 15.0: return 0.0
    return 0.05


def get_building_age_correction_factor(building_age):
    if building_age is None or building_age <= 0:
        return 1.0
    return _building_age_market_ratio(float(building_age))


def get_depreciation_advice(building_age, property_type):
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


def _abbreviate_zoning(zoning):
    if zoning is None or (isinstance(zoning, float) and pd.isna(zoning)):
        return "-"
    s = str(zoning).strip()
    if not s or s == "-" or s.lower() == "nan":
        return "-"
    mapping = [
        ("第１種低層住居専用地域", "1低"), ("第1種低層住居専用地域", "1低"), ("第一種低層住居専用地域", "1低"),
        ("第２種低層住居専用地域", "2低"), ("第2種低層住居専用地域", "2低"), ("第二種低層住居専用地域", "2低"),
        ("低層住居専用地域", "1低"), ("田園住居地域", "田住"),
        ("第１種中高層住居専用地域", "1中高"), ("第1種中高層住居専用地域", "1中高"), ("第一種中高層住居専用地域", "1中高"),
        ("第２種中高層住居専用地域", "2中高"), ("第2種中高層住居専用地域", "2中高"), ("第二種中高層住居専用地域", "2中高"),
        ("第１種住居地域", "一住"), ("第1種住居地域", "一住"), ("第一種住居地域", "一住"),
        ("第２種住居地域", "二住"), ("第2種住居地域", "二住"), ("第二種住居地域", "二住"),
        ("準住居地域", "準住"), ("近隣商業地域", "近商"), ("商業地域", "商業"),
        ("工業専用地域", "工専"), ("準工業地域", "準工"), ("工業地域", "工業"),
    ]
    for full, abbr in mapping:
        if full in s:
            return abbr
    return s[:8] if len(s) > 8 else s


def _format_display_value(val, is_numeric=False, decimals=1):
    if val is None or val == "" or str(val).strip() in ("-", "－", "―"):
        return "-"
    if is_numeric:
        try:
            return f"{float(val):,.{decimals}f}"
        except (ValueError, TypeError):
            return str(val)
    return str(val)


def build_csv_reference_table(csv_features, limit=MAX_REFERENCE_CASES, for_pdf=False):
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


def _compute_robust_average(values):
    if not values:
        return None
    if len(values) < 4:
        return sum(values) / len(values)
    arr = np.array(values)
    q1 = np.percentile(arr, 25)
    q3 = np.percentile(arr, 75)
    iqr = q3 - q1
    lower_bound = q1 - (iqr * 1.5)
    upper_bound = q3 + (iqr * 1.5)
    median_val = np.median(arr)
    if median_val > 0:
        upper_bound = min(upper_bound, median_val * 2.0)
        lower_bound = max(lower_bound, median_val * 0.2)
    filtered = [v for v in values if lower_bound <= v <= upper_bound]
    if not filtered:
        return sum(values) / len(values)
    return sum(filtered) / len(filtered)


def _collect_land_transaction_pairs(csv_features):
    pairs = []
    for f in csv_features:
        p = f.get("properties", {})
        total = parse_numeric(p.get("u_transaction_price_total_ja"))
        land_a = parse_numeric(p.get("土地面積_数値")) or parse_numeric(p.get("u_area_ja"))
        if total and land_a and land_a > 0:
            pairs.append((float(total), float(total) / float(land_a)))
    return pairs


def _land_volume_zone_avg_from_pairs(pairs):
    if not pairs:
        return None, None, 0
    totals = np.array([t for t, _ in pairs], dtype=float)
    mean_tot = float(np.mean(totals))
    cut = LAND_UNDERPRICE_VS_MEAN_YEN
    filtered = [(t, u) for t, u in pairs if t >= mean_tot - cut]
    if not filtered:
        filtered = list(pairs)
    tsubo_rows = []
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
    cap = f"（ボリュームゾーン：{p25:.1f}万円/坪～{p75:.1f}万円/坪　平均値{avg_tsubo:.1f}万円/坪）"
    return avg_sqm, cap, len(in_band)


def compute_avg_unit_price(csv_features):
    units = []
    for f in csv_features:
        up = get_unit_price(f)
        if up is not None and up > 0:
            units.append(up)
    if not units:
        return None, 0
    avg_price = _compute_robust_average(units)
    return avg_price, len(units)


def _format_date_for_display(val):
    if val is None or str(val).strip() in ("-", ""):
        return "-"
    s = str(val).strip()
    m = re.search(r"(\d{4})[/年.-](\d{1,2})[/月.-](\d{1,2})", s)
    if m:
        return f"{m.group(1)}/{m.group(2).zfill(2)}/{m.group(3).zfill(2)}"
    return s


def build_price_trend_chart(csv_features):
    """
    UI 表示用の価格トレンドチャート。
    matplotlib を使用（Plotly より高速・軽量）。
    戻り値は PNG bytes（st.image で表示）。
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import io as _io

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
    df["unit_price_tsubo_man"] = (df["unit_price"] / 10000) * M2_TO_TSUBO
    df["year"] = pd.to_datetime(df["dt"]).dt.year
    df["dt_plot"] = pd.to_datetime(df["dt"])
    line_df = df.groupby("year", as_index=False)["unit_price_tsubo_man"].mean().sort_values("year")
    line_df["period"] = pd.to_datetime(line_df["year"].astype(str) + "-07-01")

    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.scatter(df["dt_plot"], df["unit_price_tsubo_man"],
               color="#3498db", alpha=0.45, s=28, label="成約物件", zorder=2)
    ax.plot(line_df["period"], line_df["unit_price_tsubo_man"],
            color="#e74c3c", linewidth=2, marker="o", markersize=7, label="年別平均", zorder=3)
    if len(line_df) >= 2:
        x_num = np.array([d.toordinal() for d in line_df["period"]])
        z = np.polyfit(x_num, line_df["unit_price_tsubo_man"].values, 1)
        ax.plot(line_df["period"], np.poly1d(z)(x_num),
                color="#e74c3c", linewidth=1.5, linestyle="--", label="トレンド", zorder=2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    plt.xticks(rotation=30, fontsize=9)
    ax.set_ylabel("坪単価（万円/坪）", fontsize=9)
    ax.set_title("周辺の価格推移（坪単価）", fontsize=11)
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=90, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def get_price_trend_analysis(csv_features):
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


def _build_marker_tooltip_html(feature):
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
    return (f"<b>{addr}</b><br>成約価格: {price_str}<br>土地面積: {land_str}㎡ / 建物面積: {bldg_str}㎡<br>築年数: {age_str}<br>用途地域: {zoning}<br>接道: {road_str}<br>成約日: {date_str}")


def _get_marker_color_by_price(price):
    if price is None or price <= 0:
        return "#95a5a6"
    man = price / 10000
    if man < 1000: return "#27ae60"
    if man < 2000: return "#3498db"
    return "#e67e22"


def _get_marker_color_by_contract_date(feature):
    p = feature.get("properties", {})
    date_str = p.get("point_in_time_name_ja") or p.get("成約年月日") or ""
    contract_dt = _parse_date_ymd(date_str)
    if contract_dt is None:
        return "#3498db", "#5dade2"
    now = datetime.now()
    delta_days = (now - contract_dt).days
    if delta_days <= 365: return "#1a5276", "#2980b9"
    if delta_days <= 730: return "#2471a3", "#3498db"
    return "#5dade2", "#85c1e9"


def build_map_dataframe(center_lat, center_lon, csv_features):
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


def _price_trend_png_for_pdf(csv_features) -> Optional[bytes]:
    """PDF用価格推移チャート（matplotlib生成・kaleido不要）。"""
    if not csv_features:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        font_file = Path(__file__).resolve().parent / "ipaexg.ttf"
        if font_file.exists():
            from matplotlib import font_manager as _fm
            _fp = _fm.FontProperties(fname=str(font_file))
            plt.rcParams["font.family"] = _fp.get_name()

        rows = []
        for f in csv_features:
            p = f.get("properties", {})
            total = parse_numeric(p.get("u_transaction_price_total_ja"))
            area = _deal_area_sqm_for_unit_price(p)
            if not total or not area or area <= 0:
                continue
            dt = _parse_date_ymd(p.get("point_in_time_name_ja") or p.get("成約年月日") or "")
            if dt:
                rows.append({"dt": dt, "up_tsubo": (total / area / 10000) * M2_TO_TSUBO})
        if not rows:
            return None

        df_tmp = pd.DataFrame(rows).sort_values("dt")
        df_tmp["year"] = pd.to_datetime(df_tmp["dt"]).dt.year
        line_df = df_tmp.groupby("year", as_index=False)["up_tsubo"].mean().sort_values("year")

        fig, ax = plt.subplots(figsize=(6, 2.8))
        dates = [r["dt"] for r in rows]
        prices = [r["up_tsubo"] for r in rows]
        ax.scatter(dates, prices, color="#3498db", alpha=0.45, s=12, label="成約物件")
        year_dates = [datetime(int(y), 7, 1) for y in line_df["year"]]
        ax.plot(year_dates, line_df["up_tsubo"].values, color="#e74c3c",
                linewidth=1.8, marker="o", markersize=5, label="年別平均")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%y/%m"))
        ax.xaxis.set_major_locator(mdates.YearLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, fontsize=7)
        ax.set_ylabel("坪単価（万円）", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.warning("[pdf_chart] matplotlib chart error: %s", e)
        return None


def _plotly_fig_to_png(fig):
    """Plotly図→PNG（kaleido）。PDF遅延生成では _price_trend_png_for_pdf を優先使用。"""
    if fig is None:
        return None
    try:
        buf = io.BytesIO()
        if hasattr(fig, "savefig"):
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            return buf.read()
        else:
            buf.write(fig.to_image(format="png", scale=1))
            buf.seek(0)
            return buf.read()
    except Exception:
        return None


def _get_reportlab_japanese_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font_file = Path(__file__).resolve().parent / "ipaexg.ttf"
    if font_file.exists():
        try:
            name = "JPFont"
            pdfmetrics.registerFont(TTFont(name, str(font_file)))
            return name
        except Exception:
            pass
    font_paths = [Path("C:/Windows/Fonts/meiryo.ttf"), Path("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf")]
    for fp in font_paths:
        if fp.exists():
            try:
                name = "JPFont"
                pdfmetrics.registerFont(TTFont(name, str(fp)))
                return name
            except Exception:
                continue
    return "Helvetica"


def _create_map_image(map_df):
    if map_df is None or len(map_df) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        font_file = Path(__file__).resolve().parent / "ipaexg.ttf"
        if font_file.exists():
            font_prop = font_manager.FontProperties(fname=str(font_file))
            plt.rcParams['font.family'] = font_prop.get_name()
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


def generate_valuation_pdf(address, property_type, area_input, building_age, valuation, avg_unit_price, correction, adjusted_unit_price, transaction_count, df_reference, map_df, price_chart=None, **kwargs):
    try:
        return _generate_valuation_pdf_impl(address, property_type, area_input, building_age, valuation, avg_unit_price, correction, adjusted_unit_price, transaction_count, df_reference, map_df, price_chart, **kwargs)
    except Exception:
        return _generate_valuation_pdf_minimal(address, property_type, valuation, building_age, **kwargs)


def _generate_valuation_pdf_minimal(address, property_type, valuation, building_age, **kwargs):
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
    elements.append(Spacer(1, 6))
    elements.append(Paragraph(FOLLOWUP_NOTICE, s_style))
    doc.build(elements)
    buf.seek(0)
    return buf.read()


def _generate_valuation_pdf_impl(address, property_type, area_input, building_age, valuation, avg_unit_price, correction, adjusted_unit_price, transaction_count, df_reference, map_df, price_chart, **kwargs):
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
    page_w = 297*mm - 16*mm
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
    info_data = [["住所", address], ["種別", property_type], ["面積", area_str], ["築年数", f"{building_age}年" if building_age > 0 else "未入力"]]
    info_table = Table(info_data, colWidths=[28*mm, page_w - 28*mm])
    info_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")), ("FONT", (0, 0), (-1, -1), font_name, 9), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    elements.append(info_table)
    elements.append(Spacer(1, 6))
    val_man = valuation / 10000
    val_style = ParagraphStyle(name="Val", fontName=font_name, fontSize=18, textColor=colors.HexColor("#1a5276"), alignment=0, leftIndent=0, rightIndent=0, spaceBefore=4, spaceAfter=8)
    left_cell_contents = [Paragraph("■ 査定結果", heading_style), Paragraph(f"査定額：{val_man:,.0f} 万円", val_style)]
    left_cell_contents.append(Spacer(1, 4))
    left_cell_contents.append(Paragraph(FOLLOWUP_NOTICE, small_style))
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
        breakdown_data = [["内訳", "金額"], ["土地価格", f"{ld:,.0f}円"], ["建物評価", f"{bd:,.0f}円"]]
        bd_table = Table(breakdown_data, colWidths=[30*mm, 35*mm])
        bd_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")), ("FONT", (0, 0), (-1, -1), font_name, 8), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
        left_cell_contents.append(Spacer(1, 4))
        left_cell_contents.append(bd_table)
    kakuti_rate = kwargs.get("kakuti_rate", 0.0)
    corner_rate = get_corner_correction_rate(kwargs.get("corner_check", False))
    left_cell_contents.append(Spacer(1, 4))
    left_cell_contents.append(Paragraph("■ 画地補正の内訳", heading_style))
    kakuti_data = [["項目", "適用率"], ["角地・準角地", f"{corner_rate*100:+.0f}%"], ["合計画地補正率", f"{kakuti_rate*100:+.0f}%"]]
    kakuti_table = Table(kakuti_data, colWidths=[40*mm, 30*mm])
    kakuti_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3498db")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#fafafa")), ("FONT", (0, 0), (-1, -1), font_name, 8), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    left_cell_contents.append(kakuti_table)
    # csv_features_for_chart が渡されていれば kaleido 不要の matplotlib 版を優先
    csv_feats_chart = kwargs.get("csv_features_for_chart")
    if csv_feats_chart:
        chart_img = _price_trend_png_for_pdf(csv_feats_chart)
    else:
        chart_img = _plotly_fig_to_png(price_chart) if price_chart is not None else None
    right_cell_contents = []
    if chart_img:
        right_cell_contents.append(Paragraph("■ 価格トレンドグラフ", heading_style))
        chart_w = page_w * 0.58
        chart_h = 55*mm
        right_cell_contents.append(Image(io.BytesIO(chart_img), width=chart_w, height=chart_h))
    else:
        right_cell_contents.append(Paragraph("■ 価格トレンドグラフ", heading_style))
        right_cell_contents.append(Paragraph("（データなし）", body_style))
    left_w = page_w * 0.40
    right_w = page_w * 0.60
    two_col_table = Table([[left_cell_contents, right_cell_contents]], colWidths=[left_w, right_w])
    two_col_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (0, -1), 5*mm), ("RIGHTPADDING", (0, 0), (0, -1), 4*mm), ("LEFTPADDING", (1, 0), (1, -1), 6*mm), ("RIGHTPADDING", (1, 0), (1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 2*mm), ("BOTTOMPADDING", (0, 0), (-1, -1), 2*mm)]))
    elements.append(two_col_table)
    elements.append(Spacer(1, 10))
    # ── フッター：ロゴ＋会社情報 ─────────────────────────────────
    from reportlab.platypus import HRFlowable
    elements.append(HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#cccccc")))
    elements.append(Spacer(1, 4))
    company_info_style = ParagraphStyle(name="CompanyInfo", fontName=font_name, fontSize=10, textColor=colors.HexColor("#444444"), leading=14)
    company_texts = [
        Paragraph("〒079-8412　旭川市永山2条19丁目4－1　TEL: 0166-48-2349", company_info_style),
    ]
    # ロゴ画像を読み込む（横長優先）
    _logo_candidates = [
        Path(__file__).parent / "assets" / "company_logo_large.png",
        Path(__file__).parent / "assets" / "company_logo.png",
        Path(__file__).parent / "assets" / "company_logo_resized.png",
    ]
    _logo_path = next((p for p in _logo_candidates if p.exists()), None)
    if _logo_path:
        # 既知の寸法から高さ25mmに固定してアスペクト比を計算
        _known_sizes = {
            "company_logo_large.png": (3368, 2382),  # 横長
            "company_logo.png":       (1191, 1684),  # 縦長
            "company_logo_resized.png": (141, 200),  # 縦長
        }
        _fname = _logo_path.name
        _pw, _ph = _known_sizes.get(_fname, (1, 1))
        _logo_h = 50 * mm
        _logo_w = _logo_h * (_pw / _ph) if _ph > 0 else 70 * mm
        logo_img = Image(str(_logo_path), width=_logo_w, height=_logo_h)
        logo_img.hAlign = "LEFT"
        _col_w = _logo_w + 8 * mm
        footer_table = Table(
            [[logo_img, company_texts]],
            colWidths=[_col_w, page_w - _col_w],
        )
        footer_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, -1), 6 * mm),
            ("LEFTPADDING", (1, 0), (1, -1), 0),
            ("RIGHTPADDING", (1, 0), (1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        elements.append(footer_table)
    else:
        elements.append(Paragraph("株式会社 杏栄", company_info_style))
        elements.append(Paragraph("〒078-8367　旭川市永山2条19丁目4－1　TEL: 0166-48-2349", company_info_style))
    # ────────────────────────────────────────────────────────────
    doc.build(elements)
    buf.seek(0)
    return buf.read()


import base64
from PIL import Image

def get_optimized_image_base64(img_path, width=400):
    if img_path.exists():
        try:
            img = Image.open(img_path)
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

# ── URLパラメータから自動入力 ──────────────────────────────
def _init_from_query_params():
    try:
        params = st.query_params
        if not params or st.session_state.get("_params_loaded"):
            return
        ptype = params.get("ptype", "")
        if ptype in ("土地", "中古住宅（戸建て）", "中古マンション"):
            st.session_state["_qp_ptype"] = ptype
        addr = params.get("address", "")
        if addr:
            st.session_state["address_value"] = addr
        try:
            v = float(params.get("land_m2", 0))
            if v > 0: st.session_state["_qp_land_m2"] = v
        except Exception: pass
        try:
            v = float(params.get("bldg_m2", 0))
            if v > 0: st.session_state["_qp_bldg_m2"] = v
        except Exception: pass
        try:
            v = float(params.get("excl_m2", 0))
            if v > 0: st.session_state["_qp_excl_m2"] = v
        except Exception: pass
        try:
            v = int(params.get("age", 0))
            if v > 0: st.session_state["_qp_age"] = v
        except Exception: pass
        for k in ("name", "phone", "email"):
            val = params.get(k, "")
            if val: st.session_state[f"_qp_{k}"] = val
        st.session_state["_params_loaded"] = True
        # form.htmlから来た場合（address + nameがある）は自動査定フラグをセット
        if params.get("address", "") and params.get("name", ""):
            st.session_state["_auto_run_satei"] = True
    except Exception:
        pass

_init_from_query_params()
# ────────────────────────────────────────────────────────────

# form.htmlから来た場合：入力ウィジェットをCSSで非表示にして確認画面化
_from_form_html = st.session_state.get("_auto_run_satei", False)
if _from_form_html:
    st.markdown("""<style>
/* 確認画面：不要な要素を非表示 */
div[data-testid="stRadio"] { display: none !important; }
div[data-testid="stNumberInput"] { display: none !important; }
div[data-testid="stTextInput"] { display: none !important; }
div[data-testid="stCheckbox"] { display: none !important; }
div[data-testid="stExpander"] { display: none !important; }
[data-testid="stFormSubmitButton"] { display: none !important; }
div[data-testid="stForm"] .stButton > button { display: none !important; }
div[data-testid="stForm"] { border: none !important; padding: 0 !important; }
/* ヒーローバナーを非表示 */
div[data-testid="stMarkdownContainer"] > div > div[style*="linear-gradient"] { display: none !important; }
</style>""", unsafe_allow_html=True)

# 起動時に一度だけCSVを読み込み
if not st.session_state.initial_load_done:
    try:
        csv_path = _ensure_reins_data_3years()
        if not csv_path.exists():
            st.error(f"CSVファイルが見つかりません: {csv_path.name}")
        else:
            csv_mtime = csv_path.stat().st_mtime
            with st.spinner("データの解析中..."):
                cases, csv_df = load_data(str(csv_path), csv_mtime)
                if not cases:
                    st.error(f"CSVファイルから有効なデータを読み込めませんでした: {csv_path.name}")
                else:
                    valid_cases = [c for c in cases if (c.get("成約価格_円") or 0) > 0]
                    st.session_state.csv_cases = valid_cases
                    st.session_state.csv_df = csv_df
                    st.session_state.initial_load_done = True
    except Exception as e:
        st.error(f"データ読み込み中にエラーが発生しました: {e}")
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

    # ── Google Chat Webhook 診断 ──────────────────────────
    st.markdown("---")
    st.markdown("### 🔔 Chat通知の確認")
    _wh_url, _wh_src = _get_webhook_url()
    if not WEBHOOK_AUTO_NOTIFY_ENABLED:
        st.info("査定完了時の自動通知: ⏸ 一時停止中（`WEBHOOK_AUTO_NOTIFY_ENABLED = False`）")
    if _wh_url:
        st.success(f"Webhook URL: ✅ あり（{_wh_src}）")
    else:
        st.error("Webhook URL: ❌ なし\n\n`WEBHOOK_URL` を Streamlit Cloud の Secrets または環境変数に設定してください。")

    if st.button("📨 テスト通知を送信", disabled=not _wh_url):
        if not _wh_url:
            st.sidebar.error("WEBHOOK_URL が未設定です。")
        else:
            with st.spinner("送信中..."):
                _test_body = {"text": "【テスト】AI査定 Webhook 動作確認メッセージです。"}
                _ok, _err = send_inquiry_to_webhook(_test_body)
            if _ok:
                st.sidebar.success("✅ Google Chat に届きました！")
            else:
                st.sidebar.error(f"❌ 送信失敗: {_err}")
    # ─────────────────────────────────────────────────────

character_path = Path(__file__).parent / "assets" / "Copilot_20260324_100708.png"

st.markdown("""
<style>
.main .block-container { padding-bottom: 200px !important; }
@media (max-width: 768px) { div[data-testid="column"] { min-width: 100% !important; } }
</style>
""", unsafe_allow_html=True)

def render_valuation_result(sr, is_previous=False):
    title_prefix = "### 📊 仮査定結果（前回の検索結果）" if is_previous else "### 📊 仮査定結果"
    st.markdown("---")
    st.markdown(title_prefix)
    valuation = sr["valuation"]
    st.markdown(f'<p style="font-size: 2.5rem; font-weight: bold; color: #1f77b4;">仮査定金額：<span style="font-size: 3rem;">{valuation/10000:,.0f}</span> 万円</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p style="font-size: 0.95rem; color: #666; margin-top: 0.5rem; margin-bottom: 0.75rem;">{html.escape(FOLLOWUP_NOTICE)}</p>',
        unsafe_allow_html=True,
    )
    lzc = sr.get("land_volume_zone_caption")
    if lzc:
        st.markdown(f'<p style="font-size: 1rem; color: #555; margin-top: 0.25rem;">（{html.escape(str(lzc))}）</p>', unsafe_allow_html=True)
    bzc = sr.get("building_volume_zone_caption")
    if bzc:
        st.markdown(f'<p style="font-size: 1rem; color: #555; margin-top: 0.25rem;">（{html.escape(str(bzc))}）</p>', unsafe_allow_html=True)
    pdf_bytes = sr.get("pdf_bytes")
    if pdf_bytes is None:
        # 査定計算完了後にここで初めてPDFを生成（kaleido不使用・matplotlib）
        with st.spinner("📄 PDFを生成しています…"):
            try:
                _sr = sr
                _csv_f = _sr.get("csv_filtered", [])
                _map_df = build_map_dataframe(_sr["lat"], _sr["lon"], _csv_f)
                _df_pdf = build_csv_reference_table(_csv_f, limit=MAX_REFERENCE_CASES, for_pdf=True)
                _pt = _sr["property_type"]
                pdf_bytes = generate_valuation_pdf(
                    address=_sr["address"], property_type=_pt,
                    area_input=_sr["area_input"],
                    building_age=int(_sr.get("building_age") or 0),
                    valuation=_sr["valuation"], avg_unit_price=_sr["avg_unit_price"],
                    correction=_sr["correction"], adjusted_unit_price=_sr["adjusted_unit_price"],
                    transaction_count=_sr["csv_count"], df_reference=_df_pdf,
                    map_df=_map_df, price_chart=_sr.get("price_chart"),
                    land_area_input=_sr.get("land_area_input", 0),
                    building_area_input=_sr.get("building_area_input", 0),
                    exclusive_area_input=_sr.get("exclusive_area_input", 0),
                    building_breakdown=_sr.get("building_breakdown") if _pt == "中古住宅（戸建て）" else None,
                    land_breakdown=_sr.get("land_breakdown") if _pt == "中古住宅（戸建て）" else None,
                    kakuti_rate=_sr.get("kakuti_rate", 0),
                    corner_check=_sr.get("corner_check", False),
                    avg_land_500m=_sr.get("avg_land_500m") if _pt == "中古住宅（戸建て）" else None,
                    land_volume_zone_caption=_sr.get("land_volume_zone_caption"),
                    building_volume_zone_caption=_sr.get("building_volume_zone_caption"),
                    csv_features_for_chart=_csv_f,  # matplotlib チャート用（kaleido不使用）
                )
                # 再生成しないようキャッシュ
                if st.session_state.get("search_result") is not None:
                    st.session_state.search_result["pdf_bytes"] = pdf_bytes
            except Exception as _pdf_e:
                logger.exception("[pdf_lazy] error: %s", _pdf_e)
    pdf_col, staff_col = st.columns(2)
    with pdf_col:
        if pdf_bytes:
            st.download_button(
                label="📄 査定書をPDFでダウンロード",
                data=pdf_bytes,
                file_name=f"査定報告書_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                key="pdf_download_prev" if is_previous else "pdf_download",
            )
    with staff_col:
        staff_key = "staff_request_sent_prev" if is_previous else "staff_request_sent"
        if sr.get("staff_request_sent"):
            st.success("✅ 担当者査定のご希望を送信しました。担当者よりご連絡いたします。")
        else:
            if st.button(
                "📞 担当者査定はこちら",
                use_container_width=True,
                key="staff_request_btn_prev" if is_previous else "staff_request_btn",
            ):
                _wh_url, _ = _get_webhook_url()
                if not _wh_url:
                    st.error("通知の送信に失敗しました。しばらくしてから再度お試しください。")
                else:
                    ok_staff, err_staff = send_inquiry_to_webhook(_build_staff_valuation_request_body(sr))
                    if ok_staff:
                        sr["staff_request_sent"] = True
                        if st.session_state.get("search_result") is not None:
                            st.session_state.search_result["staff_request_sent"] = True
                        st.session_state[staff_key] = True
                        st.rerun()
                    else:
                        st.error(f"送信に失敗しました。{err_staff or 'しばらくしてから再度お試しください。'}")
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
        st.markdown(f'<p style="font-size:0.8rem;margin:0;color:#666;">{_house_label_avg}</p><p style="font-size:1.85rem;font-weight:700;color:#1a5276;margin:0;line-height:1.2;">{tsubo_avg:,.1f}<span style="font-size:1rem;font-weight:600;"> 万円/坪</span></p><p style="font-size:0.78rem;color:#888;margin:0.35rem 0 0 0;">{_m2_unit_caption} {display_avg/10000:,.1f} 万円/㎡</p>', unsafe_allow_html=True)
    with col2:
        st.metric("築年数補正係数", f"{correction:.2f}")
    with col3:
        tsubo_adj = (display_adj / 10000) * M2_TO_TSUBO
        st.markdown(f'<p style="font-size:0.8rem;margin:0;color:#666;">{_house_label_adj}</p><p style="font-size:1.85rem;font-weight:700;color:#1a5276;margin:0;line-height:1.2;">{tsubo_adj:,.1f}<span style="font-size:1rem;font-weight:600;"> 万円/坪</span></p><p style="font-size:0.78rem;color:#888;margin:0.35rem 0 0 0;">{_m2_unit_caption} {display_adj/10000:,.1f} 万円/㎡</p>', unsafe_allow_html=True)
    with col4:
        st.metric("参考取引件数", f"{csv_count} 件")
    building_area_input = sr.get("building_area_input") or 0.0
    if property_type == "中古住宅（戸建て）" and (building_breakdown or 0) > 0 and building_area_input > 0:
        b_m2 = building_breakdown / building_area_input
        b_tsubo_man = (b_m2 / 10000) * M2_TO_TSUBO
        st.markdown(f'<div style="background-color: #f8f9fa; padding: 10px; border-radius: 6px; margin-top: 8px;"><span style="font-size:0.85rem;color:#444;">建物評価ベース：<strong style="color:#1a5276;">{b_tsubo_man:,.1f} 万円/坪</strong>　延床㎡単価 {b_m2/10000:,.1f} 万円/㎡</span></div>', unsafe_allow_html=True)
    if property_type == "中古住宅（戸建て）":
        if avg_land_500m is not None and avg_land_500m > 0:
            st.markdown(f'<div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin-top: 10px; margin-bottom: 10px;"><strong>💡 参考情報</strong><br>半径500m以内の成約ベースの土地価格平均値： <span style="font-size: 1.35rem; font-weight: bold; color: #1f77b4;">{(avg_land_500m/10000)*M2_TO_TSUBO:,.1f} 万円/坪</span> <span style="font-size: 0.95rem; color: #555;">（㎡単価 {avg_land_500m/10000:,.1f} 万円/㎡）</span></div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin-top: 10px; margin-bottom: 10px;"><strong>💡 参考情報</strong><br>半径500m以内の土地取引データがありませんでした。</div>', unsafe_allow_html=True)
    st.caption(f"※ 半径{radius_km}㎞の、過去5年の成約事例データを参考にしています。（住所: {address}）")
    if property_type == "土地":
        st.caption("※ 成約ベースの価格から、坪単価・㎡単価に20%を上乗せしています。")
    try:
        latex_f, detail_f = format_valuation_formula(property_type, valuation, display_avg, correction, sr["land_area_input"], sr["building_area_input"], sr["exclusive_area_input"], kakuti_rate=kakuti_rate, building_breakdown=building_breakdown, land_breakdown=land_breakdown)
        st.markdown(f"**算出式**: ${latex_f}$")
        st.caption(f"※ {detail_f}（参考値です）")
    except Exception:
        st.caption(f"※ 査定金額：{valuation/10000:,.0f}万円（参考値です）")
    if property_type == "中古住宅（戸建て）" and land_breakdown is not None:
        suffix = ("（昭和56年以前のため評価0・リフォームされていても）" if (building_breakdown or 0) == 0 and (building_age_val or 0) >= 44 else "（リフォーム等の状況により価格が変わる）" if (building_breakdown or 0) == 0 and (building_age_val or 0) >= 35 else "（築25年以上のため古家付き土地）" if (building_breakdown or 0) == 0 else f"（築{building_age_val or 0}年による減価後）")
        st.markdown(f"**算出根拠**: 土地価格：{land_breakdown:,.0f}円 ＋ 建物評価：{building_breakdown or 0:,.0f}円{suffix}")
    price_chart = sr.get("price_chart")
    if price_chart:
        st.subheader("📈 価格トレンドグラフ")
        # matplotlib PNG bytes として保存しているので st.image で表示
        if isinstance(price_chart, bytes):
            st.image(price_chart, use_container_width=True)
        else:
            st.plotly_chart(price_chart, use_container_width=True)
        trend_comment = get_price_trend_analysis(sr.get("csv_filtered", []))
        if trend_comment:
            st.markdown(trend_comment)
    if property_type == "中古住宅（戸建て）":
        advice = get_depreciation_advice(building_age_val, property_type)
        if advice:
            st.warning(advice)
    st.markdown("---")
    st.markdown('<p style="text-align: center; font-size: 1rem; font-weight: bold; color: #1f77b4; background: linear-gradient(135deg, #f0f8ff 0%, #e6f3ff 100%); padding: 16px; border-radius: 8px; border-left: 4px solid #1f77b4;">📞 詳しくはお問い合わせください<br><span style="font-size: 1.1rem;">株式会社　杏栄</span><br>旭川市永山2条19丁目4－1　TEL: 0166－48－2349</p>', unsafe_allow_html=True)


import base64
def get_b64(path):
    if not path.exists(): return ""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

logo_path = Path(__file__).parent / "assets" / "company_logo_large.png"
if not logo_path.exists():
    logo_path = Path(__file__).parent / "assets" / "company_logo.png"

with st.container():
    col_l1, col_l2, col_l3 = st.columns([2, 2, 2])
    with col_l2:
        if logo_path.exists():
            st.image(str(logo_path), use_container_width=True)
        else:
            st.markdown('<h3 style="text-align:center; margin-bottom: 0;">株式会社 杏栄</h3>', unsafe_allow_html=True)

# form.htmlから来た場合：確認画面表示
if st.session_state.get("_auto_run_satei", False):
    _ptype_disp = st.session_state.get("_qp_ptype", "")
    _addr_disp = st.session_state.get("address_value", "")
    _land_m2 = st.session_state.get("_qp_land_m2", 0)
    _bldg_m2 = st.session_state.get("_qp_bldg_m2", 0)
    _excl_m2 = st.session_state.get("_qp_excl_m2", 0)
    _age_disp = st.session_state.get("_qp_age", 0)
    _name_disp = st.session_state.get("_qp_name", "")
    _phone_disp = st.session_state.get("_qp_phone", "")
    _email_disp = st.session_state.get("_qp_email", "")
    if _ptype_disp == "土地":
        _area_disp = f"土地面積: {_land_m2:.1f}㎡（{_land_m2/3.30578:.1f}坪）"
    elif _ptype_disp == "中古住宅（戸建て）":
        _area_disp = f"土地: {_land_m2:.1f}㎡（{_land_m2/3.30578:.1f}坪）／建物: {_bldg_m2:.1f}㎡（{_bldg_m2/3.30578:.1f}坪）"
    else:
        _area_disp = f"専有面積: {_excl_m2:.1f}㎡"
    st.markdown(f"""
<div style="background:#fff;border:1.5px solid #c5d8ee;border-radius:12px;padding:18px 20px;margin-bottom:16px;font-size:15px;">
<div style="font-weight:700;color:#1a3a6b;font-size:16px;margin-bottom:12px;">📋 入力内容の確認</div>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:6px 8px;color:#666;width:35%;">物件種別</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_ptype_disp}</td></tr>
<tr style="background:#f8faff;"><td style="padding:6px 8px;color:#666;">住所</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_addr_disp}</td></tr>
<tr><td style="padding:6px 8px;color:#666;">面積</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_area_disp}</td></tr>
<tr style="background:#f8faff;"><td style="padding:6px 8px;color:#666;">築年数</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_age_disp}年</td></tr>
<tr><td style="padding:6px 8px;color:#666;">お名前</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_name_disp} 様</td></tr>
<tr style="background:#f8faff;"><td style="padding:6px 8px;color:#666;">電話番号</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_phone_disp}</td></tr>
<tr><td style="padding:6px 8px;color:#666;">メール</td><td style="padding:6px 8px;font-weight:600;color:#1a3a6b;">{_email_disp}</td></tr>
</table>
</div>
""", unsafe_allow_html=True)

if not _from_form_html:
 st.markdown("""
<div style="width: 100%; max-width: 800px; margin: -10px auto 0 auto; font-family: 'Helvetica Neue', Arial, sans-serif;">
<div style="background: linear-gradient(135deg, #f0faff 0%, #e6f5ff 100%); border-radius: 15px; border: 2px solid #bde0fe; box-shadow: 0 10px 25px rgba(0,0,0,0.06); text-align: center; padding: 20px; position: relative; overflow: hidden;">
<h1 style="font-size: 24px; color: #1a4f76; margin: 0 0 8px 0; font-weight: 800; line-height: 1.3;">スマホで最短1分査定！<br>旭川の家の価値、カンタン価格診断</h1>
<div style="background: white; color: #4a6fa5; display: inline-block; padding: 4px 18px; border-radius: 50px; font-weight: 800; font-size: 16px; border: 1.2px solid #d1e3f8; margin-bottom: 20px;">最短60秒・匿名OK・営業なしで安心</div>
<div style="max-width: 420px; margin: 0 auto; text-align: left;">
<div style="font-size: 16px; font-weight: 800; color: #2c3e50; margin-bottom: 10px; display: flex; align-items: center;"><span style="color: #28a745; margin-right: 10px; font-size: 20px;">✅</span> 旭川相場データをAIが自動分析</div>
<div style="font-size: 16px; font-weight: 800; color: #2c3e50; margin-bottom: 10px; display: flex; align-items: center;"><span style="color: #28a745; margin-right: 10px; font-size: 20px;">✅</span> 地域密着の安心サポート</div>
<div style="font-size: 16px; font-weight: 800; color: #2c3e50; display: flex; align-items: center;"><span style="color: #28a745; margin-right: 10px; font-size: 20px;">✅</span> 旭川の相場に最適化</div>
</div></div></div>
<div style="margin-bottom: 25px;"></div>
""", unsafe_allow_html=True)

if not _from_form_html:
    st.markdown("**物件種別**")
if not _from_form_html:
    _ptype_options = ["土地", "中古住宅（戸建て）", "中古マンション"]
    _ptype_default = st.session_state.get("_qp_ptype", "土地")
    _ptype_index = _ptype_options.index(_ptype_default) if _ptype_default in _ptype_options else 0
    property_type = st.radio(
        "種別",
        options=_ptype_options,
        horizontal=True,
        label_visibility="collapsed",
        key="property_type_selector",
        index=_ptype_index,
    )

    if property_type == "土地":
        land_unit = st.radio("土地面積の単位", ["坪", "㎡"], horizontal=True, key="land_unit_tochi")
        if land_unit == "㎡":
            land_area_input = st.number_input("土地面積（㎡）", min_value=1.0, max_value=10000.0, value=float(st.session_state.get("_qp_land_m2", 100.0)), step=1.0, key="land_area_tochi_m2")
        else:
            _land_m2_val = float(st.session_state.get("_qp_land_m2", 0))
            _land_tsubo_default = round(_land_m2_val / M2_TO_TSUBO, 1) if _land_m2_val > 0 else 30.0
            land_tsubo = st.number_input("土地面積（坪）", min_value=0.5, max_value=3500.0, value=_land_tsubo_default, step=0.5, key="land_area_tochi_tsubo")
            land_area_input = land_tsubo * M2_TO_TSUBO
        building_area_input = 0.0
        exclusive_area_input = 0.0
        building_age = 0
    elif property_type == "中古住宅（戸建て）":
        land_unit = st.radio("土地面積の単位", ["坪", "㎡"], horizontal=True, key="land_unit_house")
        if land_unit == "㎡":
            land_area_input = st.number_input("土地面積（㎡）", min_value=0.0, max_value=10000.0, value=float(st.session_state.get("_qp_land_m2", 100.0)), step=1.0, key="land_area_house_m2")
        else:
            _land_m2_h = float(st.session_state.get("_qp_land_m2", 0))
            _land_tsubo_h = round(_land_m2_h / M2_TO_TSUBO, 1) if _land_m2_h > 0 else 30.0
            land_tsubo = st.number_input("土地面積（坪）", min_value=0.0, max_value=3500.0, value=_land_tsubo_h, step=0.5, key="land_area_house_tsubo")
            land_area_input = land_tsubo * M2_TO_TSUBO
        bldg_unit = st.radio("建物延床面積の単位", ["坪", "㎡"], horizontal=True, key="bldg_unit_house")
        if bldg_unit == "㎡":
            building_area_input = st.number_input("建物延床面積（㎡）", min_value=1.0, max_value=1000.0, value=float(st.session_state.get("_qp_bldg_m2", 100.0)), step=1.0, key="bldg_area_house_m2")
        else:
            _bldg_m2_val = float(st.session_state.get("_qp_bldg_m2", 0))
            _bldg_tsubo_default = round(_bldg_m2_val / M2_TO_TSUBO, 1) if _bldg_m2_val > 0 else 30.0
            bldg_tsubo = st.number_input("建物延床面積（坪）", min_value=0.5, max_value=300.0, value=_bldg_tsubo_default, step=0.5, key="bldg_area_house_tsubo")
            building_area_input = bldg_tsubo * M2_TO_TSUBO
        exclusive_area_input = 0.0
        building_age = st.number_input("築年数（年）", min_value=0, max_value=100, value=int(st.session_state.get("_qp_age", 0)), step=1, key="building_age_input")
    else:
        land_area_input = 0.0
        building_area_input = 0.0
        exclusive_area_input = st.number_input("専有面積（㎡）", min_value=1.0, max_value=500.0, value=float(st.session_state.get("_qp_excl_m2", 50.0)), step=0.1, key="exclusive_area_mansion")
        building_age = st.number_input("築年数（年）", min_value=0, max_value=100, value=int(st.session_state.get("_qp_age", 0)), step=1, key="building_age_mansion_input")

    st.markdown("**住所**")
    st.caption("住所がわからない場合は「地図で選択」ボタンで地図から選べます")
    addr_col, map_col = st.columns([4, 1])
    with addr_col:
        if st.session_state.get("address_from_map"):
            st.session_state["address_value"] = st.session_state.pop("address_from_map")
        if "address_value" not in st.session_state:
            st.session_state["address_value"] = ""
        address = st.text_input("住所", value=st.session_state["address_value"], placeholder="例: 北海道旭川市神居一条18丁目", label_visibility="collapsed")
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

# form.htmlから来た場合、入力値をセッションから復元
if _from_form_html:
    property_type = st.session_state.get("_qp_ptype", "土地")
    _land_m2 = float(st.session_state.get("_qp_land_m2", 0))
    _bldg_m2 = float(st.session_state.get("_qp_bldg_m2", 0))
    _excl_m2 = float(st.session_state.get("_qp_excl_m2", 0))
    land_area_input = _land_m2
    building_area_input = _bldg_m2
    exclusive_area_input = _excl_m2
    building_age = int(st.session_state.get("_qp_age", 0))
    address = st.session_state.get("address_value", "")

with st.form("search_form"):
    radius_km = 1.0
    st.info(f"💡 検索半径は自動で設定されます（**半径 {radius_km}km** で検索します）")
    st.caption(f"半径{radius_km}㎞の、過去5年の成約事例データを参考にしています。")
    corner_check = False
    if not _from_form_html:
        st.markdown("---")
        st.markdown("**ご連絡情報の入力をお願いします。**")
        contact_name = st.text_input("お名前（必須）", value=st.session_state.get("_qp_name", ""), placeholder="例: 山田 太郎")
        contact_phone = st.text_input("電話番号（必須）", value=st.session_state.get("_qp_phone", ""), placeholder="例: 090-1234-5678")
        contact_email = st.text_input("メールアドレス（必須）", value=st.session_state.get("_qp_email", ""), placeholder="例: example@email.com")
        st.markdown("**個人情報の取り扱い（必須）**")
        st.markdown('<a href="https://www.kyouei-asahikawa.com/privacy.html" target="_blank" rel="noopener noreferrer">『個人情報の取り扱い等について』</a>をお読みいただき、ご同意のうえ査定してください。', unsafe_allow_html=True)
        privacy_agree = st.checkbox("同意する", value=False, key="privacy_agree")
    submitted = st.form_submit_button("査定を実行" if not _from_form_html else "")


# form.htmlから自動遷移した場合、同意済みとして自動査定
if st.session_state.get("_auto_run_satei") and not st.session_state.get("_auto_run_done"):
    st.session_state["_auto_run_done"] = True
    submitted = True
    privacy_agree = True
    contact_name = st.session_state.get("_qp_name", "")
    contact_phone = st.session_state.get("_qp_phone", "")
    contact_email = st.session_state.get("_qp_email", "")
    address = st.session_state.get("address_value", "")
    radius_km = 1.0
    corner_check = False
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
            _run_valuation_pipeline(
                address=address.strip(),
                property_type=property_type,
                land_area_input=land_area_input,
                building_area_input=building_area_input,
                exclusive_area_input=exclusive_area_input,
                building_age=building_age,
                contact_name=contact_name,
                contact_phone=contact_phone,
                contact_email=contact_email,
                radius_km=radius_km,
                corner_check=corner_check,
            )
            st.rerun()
    except Exception as e:
        st.error(f"査定計算中に予期しないエラーが発生しました: {e}")
        import traceback
        st.code(traceback.format_exc())

# GET クエリからの自動査定（チャット通知は `_run_valuation_pipeline` で実行）。`elif` 連鎖にしないことで CSV 読込待ちでも下の結果表示に到達できる。
if not submitted and (url_auto_bundle := parse_url_auto_valuation_bundle_if_present()) is not None:
    ua_sig, ua_fields = url_auto_bundle
    ua_state = st.session_state.setdefault("_url_auto_val_state", {})
    if ua_sig not in ua_state:
        if not st.session_state.get("csv_cases"):
            logger.info("[url_auto] Skip auto valuation until CSV loaded (sig=%s...)", ua_sig[:12])
        else:
            ua_ok = _run_valuation_pipeline(
                address=ua_fields["address"],
                property_type=ua_fields["ptype_jp"],
                land_area_input=ua_fields["land_area_input"],
                building_area_input=ua_fields["building_area_input"],
                exclusive_area_input=ua_fields["exclusive_area_input"],
                building_age=ua_fields["building_age"],
                contact_name=ua_fields["contact_name"],
                contact_phone=ua_fields["contact_phone"],
                contact_email=ua_fields["contact_email"],
                radius_km=1.0,
                corner_check=False,
            )
            ua_state[ua_sig] = "ok" if ua_ok else "fail"
            if ua_ok:
                st.rerun()

if not submitted and st.session_state.search_result is not None:
    sr = st.session_state.search_result
    if sr.get("has_valuation"):
        render_valuation_result(sr, is_previous=True)
    else:
        st.info(f"前回の検索: {sr.get('address')} — 査定結果を出せませんでした。")

st.markdown('<div style="height: 100px;"></div>', unsafe_allow_html=True)
