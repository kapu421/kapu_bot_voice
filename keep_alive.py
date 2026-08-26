import os
import logging
from threading import Thread
from flask import Flask

logger = logging.getLogger(__name__)

app = Flask('')

# Werkzeugのアクセスログを非表示
logging.getLogger('werkzeug').setLevel(logging.ERROR)

@app.route('/')
def home():
    return "I'm alive!", 200

def run():
    # Renderが指定する PORT（なければ 10000）を取得
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()
    logger.info("keep_alive: Flask server started.")
