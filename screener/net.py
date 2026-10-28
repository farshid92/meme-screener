"""Wait for the internet before starting (e.g. a laptop that just woke up and has no Wi-Fi yet)."""
import socket
import time

HOSTS = ("api.dexscreener.com", "lite-api.jup.ag", "api.telegram.org")


def wait_for_network(timeout_s=300, interval_s=10, hosts=HOSTS, sleep=time.sleep, resolve=socket.gethostbyname):
    """Return True once any host resolves, False after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while True:
        for h in hosts:
            try:
                resolve(h)
                return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        sleep(interval_s)
