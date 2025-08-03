import requests
import json
import time
import logging

from signal_generator import analyze_tf_opportunity, build_signal_format

# Konfigurasi
SYMBOL = "XAUUSD"
TIMEFRAME = "H1"
# Ganti dengan URL endpoint Flask app Anda jika berbeda
FLASK_APP_URL = "http://127.0.0.1:5000/api/internal/submit_signal"
INTERNAL_API_KEY = "c1b086d4-a681-48df-957f-6fcc35a82f6d" # Kunci rahasia internal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def run_analysis_and_submit():
    """
    Menjalankan analisis sinyal dan mengirimkannya ke server Flask.
    """
    logging.info(f"Mulai analisis untuk {SYMBOL} pada timeframe {TIMEFRAME}...")

    # Panggil fungsi analisis utama.
    # Parameter dummy/placeholder untuk yang tidak kita gunakan di contoh ini.
    analysis_result = analyze_tf_opportunity(
        symbol=SYMBOL,
        tf=TIMEFRAME,
        mt5_path="", # Path MT5 tidak relevan karena kita menggunakan data dummy
        gng_model=None,
        gng_feature_stats={},
        confidence_threshold=3.0, # Contoh ambang batas skor
        min_distance_pips_per_tf={},
        htf_bias=None
    )

    if not analysis_result:
        logging.warning("Analisis tidak menghasilkan output.")
        return

    signal = analysis_result.get("signal")
    order_type = analysis_result.get("order_type")
    entry_price = analysis_result.get("entry_price_chosen")
    sl = analysis_result.get("sl")
    tp = analysis_result.get("tp")
    score = analysis_result.get("score")

    logging.info(f"Hasil Analisis: Signal={signal}, OrderType={order_type}, Entry={entry_price}, SL={sl}, TP={tp}, Score={score}")

    # Hanya kirim sinyal jika bukan "WAIT"
    if signal != "WAIT" and order_type is not None:
        logging.info(f"Sinyal valid ditemukan ({order_type}). Mempersiapkan untuk dikirim...")

        # Bangun format sinyal JSON yang sesuai
        signal_json = build_signal_format(
            symbol=SYMBOL,
            entry_price=entry_price,
            direction=signal,
            sl=sl,
            tp=tp,
            order_type=order_type
        )

        # Siapkan payload untuk dikirim ke endpoint Flask
        payload = {
            "api_key": INTERNAL_API_KEY,
            "symbol": SYMBOL,
            "signal": signal, # 'BUY' atau 'SELL'
            "order_type": order_type, # Kirim tipe order spesifik
            "signal_json": signal_json
        }

        try:
            response = requests.post(FLASK_APP_URL, json=payload, timeout=10)
            response.raise_for_status()  # Akan raise exception untuk status 4xx/5xx
            logging.info(f"Sinyal berhasil dikirim ke server. Respons: {response.json()}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Gagal mengirim sinyal ke server: {e}")
    else:
        logging.info("Tidak ada sinyal valid untuk dikirim (Signal: WAIT).")

if __name__ == "__main__":
    # Contoh: jalankan analisis sekali.
    # Dalam implementasi nyata, ini bisa berjalan dalam loop setiap beberapa menit/jam.
    run_analysis_and_submit()
