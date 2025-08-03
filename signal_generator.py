# signal_generator.py
#
# Deskripsi:
# Versi ini telah ditambahkan 'Verbose Logging' (Mode Cerewet) untuk
# menampilkan rincian perhitungan skor, membantu kita memahami kenapa
# sinyal tidak terbentuk.

from __future__ import annotations

import logging
import json
from typing import Dict, List, Tuple, Optional, Any

import MetaTrader5 as mt5

from data_fetching import get_candlestick_data
from technical_indicators import (
    detect_structure,
    detect_order_blocks_multi,
    detect_fvg_multi,
    detect_eqh_eql,
    detect_liquidity_sweep,
    calculate_optimal_trade_entry,
    detect_engulfing,
    detect_pinbar,
)
from gng_model import (
    get_gng_input_features_full,
    get_gng_context,
)

# ... (Fungsi utilitas tidak berubah) ...
def get_open_positions_per_tf(symbol: str, tf: str, mt5_path: str) -> int:
    if not mt5.initialize(path=mt5_path): return 99
    positions = mt5.positions_get(symbol=symbol)
    mt5.shutdown()
    return len(positions) if positions is not None else 0
def get_active_orders(symbol: str, mt5_path: str) -> List[float]:
    if not mt5.initialize(path=mt5_path): return []
    active_prices: List[float] = []
    try:
        positions = mt5.positions_get(symbol=symbol)
        if positions:
            for pos in positions: active_prices.append(pos.price_open)
        orders = mt5.orders_get(symbol=symbol)
        if orders:
            for order in orders: active_prices.append(order.price_open)
    except Exception as e:
        logging.error(f"Error saat mengambil order/posisi aktif: {e}")
    finally:
        mt5.shutdown()
    return active_prices
def is_far_enough(entry_price: float, existing_prices: List[float], point_value: float, min_distance_pips: float) -> bool:
    min_distance_points = min_distance_pips * point_value
    for price in existing_prices:
        if abs(entry_price - price) < min_distance_points:
            logging.warning(f"Sinyal DITOLAK: Entry {entry_price:.3f} terlalu dekat dengan order aktif di {price:.3f}.")
            return False
    return True
def build_signal_format(symbol: str, entry_price: float, direction: str, sl: float, tp: float, order_type: str) -> dict:
    """
    Membangun format JSON untuk berbagai tipe order (Market, Limit, Stop).
    Contoh order_type: 'BUY', 'SELL', 'BUY_LIMIT', 'SELL_LIMIT', 'BUY_STOP', 'SELL_STOP'
    """
    signal = {"Symbol": symbol}

    # Inisialisasi semua kunci agar struktur konsisten
    order_keys = [
        "BuyEntry", "BuySL", "BuyTP", "SellEntry", "SellSL", "SellTP",
        "BuyStop", "BuyStopSL", "BuyStopTP", "SellStop", "SellStopSL", "SellStopTP",
        "BuyLimit", "BuyLimitSL", "BuyLimitTP", "SellLimit", "SellLimitSL", "SellLimitTP"
    ]
    for key in order_keys:
        signal[key] = ""

    # Isi nilai berdasarkan tipe order yang benar
    # Menggunakan upper() untuk memastikan konsistensi (misal: 'buy' menjadi 'BUY')
    order_type_upper = order_type.upper()

    if order_type_upper == 'BUY':
        signal.update({"BuyEntry": str(entry_price), "BuySL": str(sl), "BuyTP": str(tp)})
    elif order_type_upper == 'SELL':
        signal.update({"SellEntry": str(entry_price), "SellSL": str(sl), "SellTP": str(tp)})
    elif order_type_upper == 'BUY_LIMIT':
        signal.update({"BuyLimit": str(entry_price), "BuyLimitSL": str(sl), "BuyLimitTP": str(tp)})
    elif order_type_upper == 'SELL_LIMIT':
        signal.update({"SellLimit": str(entry_price), "SellLimitSL": str(sl), "SellLimitTP": str(tp)})
    elif order_type_upper == 'BUY_STOP':
        signal.update({"BuyStop": str(entry_price), "BuyStopSL": str(sl), "BuyStopTP": str(tp)})
    elif order_type_upper == 'SELL_STOP':
        signal.update({"SellStop": str(entry_price), "SellStopSL": str(sl), "SellStopTP": str(tp)})

    return signal
def make_signal_id(signal_json: Dict[str, str]) -> str:
    return str(abs(hash(json.dumps(signal_json, sort_keys=True))))

# ========== ANALYZE PELUANG UTAMA ==========
def analyze_tf_opportunity(
    symbol: str,
    tf: str,
    mt5_path: str,
    gng_model,
    gng_feature_stats: Dict[str, Dict[str, Any]],
    confidence_threshold: float,
    min_distance_pips_per_tf: Dict[str, float],
    htf_bias: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    df = get_candlestick_data(symbol, tf, 200, mt5_path)
    if df is None or len(df) < 50:
        logging.warning(f"Data TF {tf} tidak cukup untuk analisis.")
        return None

    current_price = df['close'].iloc[-1]
    structure_str, swing_points = detect_structure(df)
    atr = df['high'].sub(df['low']).rolling(14).mean().iloc[-1]

    order_blocks = detect_order_blocks_multi(df, structure_filter=structure_str)
    fvg_zones = detect_fvg_multi(df)
    liquidity_sweep = detect_liquidity_sweep(df) # FIX: Removed swing_points argument
    patterns = detect_engulfing(df) + detect_pinbar(df)

    score = 0.0
    info_list: List[str] = []

    weights = {
        "BULLISH_BOS": 3.0, "BEARISH_BOS": -3.0, "HH": 1.0, "LL": -1.0, "HL": 1.0, "LH": -1.0,
        "FVG_BULLISH": 3.0, "FVG_BEARISH": -3.0, "BULLISH_LS": 3.0, "BEARISH_LS": -3.0,
        "BULLISH_OB": 1.0, "BEARISH_OB": -1.0, "GNG_Context_Buy": 1.5, "GNG_Context_Sell": -1.5,
        "ENGULFING_BULL": 1.0, "ENGULFING_BEAR": -1.0, "PINBAR_BULL": 0.8, "PINBAR_BEAR": -0.8,
    }

    # ====================================================================
    # --- PERUBAHAN: Menambahkan Logging Detail Perhitungan Skor ---
    # ====================================================================
    logging.info(f"[{tf}] --- Analisis Skor Dimulai ---")

    # Skor Struktur
    structure_score = 0
    if "BULLISH_BOS" in structure_str: structure_score += weights["BULLISH_BOS"]
    if "BEARISH_BOS" in structure_str: structure_score += weights["BEARISH_BOS"]
    if "HH" in structure_str: structure_score += weights["HH"]
    if "LL" in structure_str: structure_score += weights["LL"]
    if "HL" in structure_str: structure_score += weights["HL"]
    if "LH" in structure_str: structure_score += weights["LH"]
    score += structure_score
    logging.info(f"[{tf}] Struktur Pasar: '{structure_str}' (Skor: {structure_score:.2f})")

    # Skor FVG
    fvg_score = 0
    if fvg_zones:
        nearest_fvg = fvg_zones[0]
        if nearest_fvg['type'] == "FVG_BULLISH": fvg_score = weights['FVG_BULLISH'] * nearest_fvg['strength']
        elif nearest_fvg['type'] == "FVG_BEARISH": fvg_score = weights['FVG_BEARISH'] * nearest_fvg['strength']
        score += fvg_score
        logging.info(f"[{tf}] FVG Terdekat: {nearest_fvg['type']} (Skor: {fvg_score:.2f})")

    # Skor Liquidity Sweep
    ls_score = 0
    if liquidity_sweep:
        if liquidity_sweep['type'] == 'BULLISH_LS':
            ls_score = weights['BULLISH_LS']
            info_list.append(f"Bullish Liquidity Sweep at {liquidity_sweep['price']:.5f}")
        elif liquidity_sweep['type'] == 'BEARISH_LS':
            ls_score = weights['BEARISH_LS']
            info_list.append(f"Bearish Liquidity Sweep at {liquidity_sweep['price']:.5f}")
        score += ls_score
        logging.info(f"[{tf}] Liquidity Sweep: {liquidity_sweep['type']} (Skor: {ls_score:.2f})")

    # Skor OB
    ob_score = 0
    if order_blocks:
        nearest_ob = order_blocks[0]
        if nearest_ob['type'] == 'BULLISH_OB': ob_score = weights['BULLISH_OB'] * nearest_ob['strength']
        elif nearest_ob['type'] == 'BEARISH_OB': ob_score = weights['BEARISH_OB'] * nearest_ob['strength']
        score += ob_score
        logging.info(f"[{tf}] OB Terdekat: {nearest_ob['type']} (Skor: {ob_score:.2f})")

    logging.info(f"[{tf}] Total Skor Sejauh Ini: {score:.2f}")
    # ====================================================================

    direction = "WAIT"
    order_type = None
    entry_price_chosen = current_price

    # --- LOGIKA PENENTUAN TIPE ORDER ---
    if score >= confidence_threshold:
        direction = "BUY"
        # Prioritas 1: Limit Order (Pullback ke zona FVG/OB Bullish di bawah harga)
        potential_limit_zones = [z for z in fvg_zones if 'BULLISH' in z['type'] and z['start'] < current_price] + \
                              [z for z in order_blocks if 'BULLISH' in z['type'] and z['high'] < current_price]

        if potential_limit_zones:
            potential_limit_zones.sort(key=lambda z: z['distance'])
            best_zone = potential_limit_zones[0]

            # FIX: Adaptasi ke fungsi OTE yang baru
            swing_start = best_zone.get('end', best_zone.get('low'))
            swing_end = best_zone.get('start', best_zone.get('high'))

            if swing_start and swing_end:
                ote_levels = calculate_optimal_trade_entry(swing_start, swing_end, direction)
                entry_price = ote_levels.get('mid')

                if entry_price and entry_price < current_price:
                    order_type = "BUY_LIMIT"
                    entry_price_chosen = entry_price
                    info_list.append(f"BUY_LIMIT based on {best_zone['type']} OTE")
                    logging.info(f"[{tf}] Peluang BUY_LIMIT ditemukan di zona {best_zone['type']}. Entry OTE: {entry_price_chosen:.5f}")

        # Prioritas 2: Stop Order (Breakout setelah BOS)
        if not order_type and "BULLISH_BOS" in structure_str and swing_points and swing_points.get('last_high'):
            last_swing_high = swing_points['last_high']
            if current_price > last_swing_high * 0.99 and last_swing_high > entry_price_chosen:
                order_type = "BUY_STOP"
                entry_price_chosen = last_swing_high + (atr * 0.1)
                info_list.append(f"BUY_STOP based on BULLISH_BOS near swing high {last_swing_high:.5f}")
                logging.info(f"[{tf}] Peluang BUY_STOP ditemukan setelah BOS. Entry: {entry_price_chosen:.5f}")

        # Prioritas 3: Market Order (Instant)
        if not order_type:
            order_type = "BUY"
            entry_price_chosen = current_price
            info_list.append("BUY market order based on strong bullish score.")
            logging.info(f"[{tf}] Tidak ada setup Limit/Stop, menggunakan Market Order BUY.")

    elif score <= -confidence_threshold:
        direction = "SELL"
        # Prioritas 1: Limit Order (Pullback ke zona FVG/OB Bearish di atas harga)
        potential_limit_zones = [z for z in fvg_zones if 'BEARISH' in z['type'] and z['start'] > current_price] + \
                              [z for z in order_blocks if 'BEARISH' in z['type'] and z['low'] > current_price]

        if potential_limit_zones:
            potential_limit_zones.sort(key=lambda z: z['distance'])
            best_zone = potential_limit_zones[0]

            # FIX: Adaptasi ke fungsi OTE yang baru
            swing_start = best_zone.get('start', best_zone.get('high'))
            swing_end = best_zone.get('end', best_zone.get('low'))

            if swing_start and swing_end:
                ote_levels = calculate_optimal_trade_entry(swing_start, swing_end, direction)
                entry_price = ote_levels.get('mid')

                if entry_price and entry_price > current_price:
                    order_type = "SELL_LIMIT"
                    entry_price_chosen = entry_price
                    info_list.append(f"SELL_LIMIT based on {best_zone['type']} OTE")
                    logging.info(f"[{tf}] Peluang SELL_LIMIT ditemukan di zona {best_zone['type']}. Entry OTE: {entry_price_chosen:.5f}")

        # Prioritas 2: Stop Order (Breakdown setelah BOS)
        if not order_type and "BEARISH_BOS" in structure_str and swing_points and swing_points.get('last_low'):
            last_swing_low = swing_points['last_low']
            if current_price < last_swing_low * 1.01 and last_swing_low < entry_price_chosen:
                order_type = "SELL_STOP"
                entry_price_chosen = last_swing_low - (atr * 0.1)
                info_list.append(f"SELL_STOP based on BEARISH_BOS near swing low {last_swing_low:.5f}")
                logging.info(f"[{tf}] Peluang SELL_STOP ditemukan setelah BOS. Entry: {entry_price_chosen:.5f}")

        # Prioritas 3: Market Order (Instant)
        if not order_type:
            order_type = "SELL"
            entry_price_chosen = current_price
            info_list.append("SELL market order based on strong bearish score.")
            logging.info(f"[{tf}] Tidak ada setup Limit/Stop, menggunakan Market Order SELL.")

    if direction == "WAIT":
        order_type = None

    sl, tp = 0.0, 0.0
    if direction != "WAIT":
        if direction == "BUY":
            sl = entry_price_chosen - (atr * 1.5)
            tp = entry_price_chosen + (atr * 3.0)
        elif direction == "SELL":
            sl = entry_price_chosen + (atr * 1.5)
            tp = entry_price_chosen - (atr * 3.0)

    features = get_gng_input_features_full(df, gng_feature_stats, tf) if gng_model else None

    logging.info(f"[{tf}] --- Analisis Selesai | Arah: {direction}, Tipe: {order_type}, Skor: {score:.2f} ---")
    return {
        "signal": direction, "order_type": order_type, "entry_price_chosen": entry_price_chosen,
        "sl": sl, "tp": tp, "score": score, "info": "; ".join(info_list),
        "features": features, "tf": tf, "symbol": symbol,
    }
