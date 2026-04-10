import requests
import time

urls = [
    "https://number-details.onrender.com",
    "https://test-joyi.onrender.com"
]

while True:
    for url in urls:
        try:
            r = requests.get(url, timeout=30)
            print(f"{url} -> {r.status_code}")
        except Exception as e:
            print(f"{url} -> ERROR: {e}")

    print("Waiting 120 seconds...\n")
    time.sleep(120)
