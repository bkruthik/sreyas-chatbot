import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from app import app
client = app.test_client()

# Telugu test
res = client.post('/api/chat', json={'message': 'fee structure', 'language': 'te', 'mode': 'student'})
data = res.get_json()
print("=== Telugu fee structure ===")
print("Answer:", data.get('answer', '')[:500])
print()

res = client.post('/api/chat', json={'message': 'placements', 'language': 'te', 'mode': 'student'})
data = res.get_json()
print("=== Telugu placements ===")
print("Answer:", data.get('answer', '')[:500])
print()

# Check for entity fragments
res = client.post('/api/chat', json={'message': 'courses', 'language': 'te', 'mode': 'student'})
data = res.get_json()
ans = data.get('answer', '')
print("=== Telugu courses ===")
print("Answer:", ans[:500])
if '&#x' in ans or '&#' in ans:
    print("WARNING: Contains HTML entities!")
else:
    print("OK: No entity fragments")
