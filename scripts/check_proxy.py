"""Check if httpx detects any proxy"""
import os
import httpx
from httpx._transports.default import AsyncHTTPTransport


print("=== Environment proxy vars ===")
for var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
    val = os.environ.get(var)
    print(f"  {var}: {val or '(not set)'}")

print()
print("=== httpx default transport ===")
client = httpx.AsyncClient()
transport = client._transport
print(f"  Transport type: {type(transport).__module__}.{type(transport).__qualname__}")
print(f"  Is proxy: {'Proxy' in type(transport).__name__}")

# Try with proxy explicitly disabled
print()
print("=== httpx with proxy=None ===")
client2 = httpx.AsyncClient(proxy=None)
transport2 = client2._transport
print(f"  Transport type: {type(transport2).__module__}.{type(transport2).__qualname__}")
print(f"  Is proxy: {'Proxy' in type(transport2).__name__}")

print()
print("Done")
