#!/usr/bin/env python3
"""Day Planner server with REST API and email reminders."""

import json
import os
import smtplib
import threading
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 6900
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks.json")
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
MAX_STORAGE_BYTES = 500 * 1024 * 1024  # 500 MB

# Email config
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
EMAIL_ADDRESS = "joshlees1@gmail.com"
EMAIL_APP_PASSWORD = "emia eory tfhl ebxj"

# Thread-safe file access
file_lock = threading.Lock()

# Market quotes cache
_quotes_cache = {"data": None, "ts": 0}
_QUOTES_TTL = 120  # seconds
_QUOTE_SYMBOLS = {"SPX": "^GSPC", "QQQ": "QQQ", "Gold": "GC=F", "BTC": "BTC-USD"}

# Weather cache (keyed by location)
_weather_cache = {"data": None, "ts": 0, "loc": None}
_WEATHER_TTL = 600  # seconds


def fetch_quotes():
    now = time.time()
    if _quotes_cache["data"] and now - _quotes_cache["ts"] < _QUOTES_TTL:
        return _quotes_cache["data"]

    results = {}
    for label, symbol in _QUOTE_SYMBOLS.items():
        try:
            url = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1d&interval=1d".format(
                urllib.parse.quote(symbol, safe="")
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            meta = data["chart"]["result"][0]["meta"]
            price = meta["regularMarketPrice"]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if prev and prev != 0:
                pct = ((price - prev) / prev) * 100
            else:
                pct = 0
            results[label] = {"price": round(price, 2), "change_pct": round(pct, 2)}
        except Exception as e:
            print(f"[Quotes] Failed to fetch {symbol}: {e}")
            results[label] = {"price": None, "change_pct": None}

    _quotes_cache["data"] = results
    _quotes_cache["ts"] = now
    return results


def fetch_weather(location):
    now = time.time()
    if (_weather_cache["data"] and _weather_cache["loc"] == location
            and now - _weather_cache["ts"] < _WEATHER_TTL):
        return _weather_cache["data"]

    try:
        url = "https://wttr.in/{}?format=j1".format(urllib.parse.quote(location))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        current = data["current_condition"][0]
        result = {
            "location": data.get("nearest_area", [{}])[0].get("areaName", [{}])[0].get("value", location),
            "temp_c": current.get("temp_C"),
            "temp_f": current.get("temp_F"),
            "desc": current.get("weatherDesc", [{}])[0].get("value", ""),
            "icon": current.get("weatherCode", ""),
        }
        _weather_cache["data"] = result
        _weather_cache["ts"] = now
        _weather_cache["loc"] = location
        return result
    except Exception as e:
        print(f"[Weather] Failed to fetch for {location}: {e}")
        return {"location": location, "temp_c": None, "desc": "Unavailable", "icon": ""}

# Track sent reminders: set of "YYYY-MM-DD-HH"
sent_reminders = set()


def load_all_tasks():
    with file_lock:
        if not os.path.exists(DATA_FILE):
            return {}
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}


def save_all_tasks(data):
    with file_lock:
        # Enforce 500MB storage cap by dropping oldest dates first
        content = json.dumps(data, indent=2)
        while len(content.encode("utf-8")) > MAX_STORAGE_BYTES and data:
            oldest_date = min(data.keys())
            del data[oldest_date]
            content = json.dumps(data, indent=2)
            print(f"[Storage] Dropped {oldest_date} to stay under 500MB limit")
        with open(DATA_FILE, "w") as f:
            f.write(content)


def format_hour(h):
    h = int(h)
    if h == 0:
        return "12 AM"
    if h == 12:
        return "12 PM"
    if h < 12:
        return f"{h} AM"
    return f"{h - 12} PM"


class DayPlannerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quieter logging
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_index()
        elif self.path.startswith("/api/tasks/"):
            self._get_tasks()
        elif self.path == "/api/quotes":
            self._get_quotes()
        elif self.path.startswith("/api/weather"):
            self._get_weather()
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/tasks/"):
            self._put_tasks()
        else:
            self.send_error(404)

    def _serve_index(self):
        try:
            with open(INDEX_FILE, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "index.html not found")

    def _get_tasks(self):
        date_str = self.path.split("/api/tasks/")[1]
        all_tasks = load_all_tasks()
        tasks = all_tasks.get(date_str, {})
        body = json.dumps(tasks).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _get_quotes(self):
        quotes = fetch_quotes()
        body = json.dumps(quotes).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _get_weather(self):
        # Parse ?location=... query param
        loc = "Auckland"
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for param in qs.split("&"):
                if param.startswith("location="):
                    loc = urllib.parse.unquote(param.split("=", 1)[1])
        weather = fetch_weather(loc)
        body = json.dumps(weather).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _put_tasks(self):
        date_str = self.path.split("/api/tasks/")[1]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            tasks = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        all_tasks = load_all_tasks()
        if tasks:
            all_tasks[date_str] = tasks
        else:
            all_tasks.pop(date_str, None)
        save_all_tasks(all_tasks)

        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def send_reminder_email(task_text, hour, date_str):
    time_str = format_hour(hour)
    subject = f"Day Planner Reminder: {task_text} at {time_str}"
    body = f"Reminder: You have \"{task_text}\" scheduled at {time_str} on {date_str}."

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = EMAIL_ADDRESS

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, EMAIL_ADDRESS, msg.as_string())
        print(f"[Reminder] Sent email for '{task_text}' at {time_str} on {date_str}")
    except Exception as e:
        print(f"[Reminder] Failed to send email: {e}")


def reminder_loop():
    global sent_reminders
    last_cleanup_date = None

    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            # Clean old entries once per day
            if last_cleanup_date != today_str:
                sent_reminders = {k for k in sent_reminders if k.startswith(today_str)}
                last_cleanup_date = today_str

            target_hour = now.hour + 1
            if target_hour <= 23:
                all_tasks = load_all_tasks()
                day_tasks = all_tasks.get(today_str, {})

                for hour_str, task in day_tasks.items():
                    if int(hour_str) == target_hour:
                        key = f"{today_str}-{hour_str}"
                        if key not in sent_reminders:
                            sent_reminders.add(key)
                            send_reminder_email(task["text"], int(hour_str), today_str)
        except Exception as e:
            print(f"[Reminder] Error in loop: {e}")

        time.sleep(60)


def main():
    # Start reminder thread
    t = threading.Thread(target=reminder_loop, daemon=True)
    t.start()
    print(f"[Reminder] Background thread started")

    server = HTTPServer(("0.0.0.0", PORT), DayPlannerHandler)
    print(f"[Server] Running on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
