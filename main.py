import requests
import time

url = "https://number-details.onrender.com"

try:
    r = requests.get(url, timeout=30)
    print("Ping Success:", r.status_code)
except Exception as e:
    print("Ping Failed:", e)
