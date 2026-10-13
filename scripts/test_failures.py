import sys
import io

# Force stdout to utf-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from app import app
client = app.test_client()

queries = [
    "Which college bus goes to Kukatpally? Show the route and available timings.",
    "Wat is the schlarship facility available for students?",
    "I'm interested in the electronics branch. What does the college offer?",
    "How are you today?",
    "Does the college guarantee a job with a ₹20 LPA package to every CSE student?",
    "Under R25, how are internal and external examination marks divided?"
]

for q in queries:
    res = client.post('/api/chat', json={'message': q, 'language': 'en', 'mode': 'student'})
    data = res.get_json()
    print("=" * 60)
    print("QUERY:", q)
    print("Category:", data.get('category'))
    print("Show Form:", data.get('show_form'))
    ans = data.get('answer', '') or ''
    print("Response len:", len(ans))
    print("Response:\n", ans[:400])
