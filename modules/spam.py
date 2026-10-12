"""
=========================================================
SREYAS ASSISTANT
Smart Anti-Spam & Rate Limiter
=========================================================

Features
--------
✓ Allows unlimited genuine college questions
✓ Detects repeated messages
✓ Detects meaningless text
✓ Detects bot flooding
✓ Temporary cooldown only for bots
✓ Never blocks genuine students
"""

import re
import time
from collections import defaultdict, deque


class SpamDetector:
    FLOOD_LIMIT = 30
    FLOOD_WINDOW = 60
    DUPLICATE_LIMIT = 3
    COOLDOWN = 60

    user_requests = defaultdict(deque)
    repeated_messages = defaultdict(list)
    cooldown_users = {}

    _KEYBOARD_ROWS = (
        "qwertyuiop",
        "asdfghjkl",
        "zxcvbnm",
    )

    @classmethod
    def _is_keyboard_smash(cls, text: str) -> bool:
        """
        Detect real keyboard smashing like:
        qwertyuiop
        asdfghjkl
        zxcvbnm
        poiuytrewq
        lkjhgfdsa
        """

        word = text.lower()

        if len(word) < 6:
            return False

        for row in cls._KEYBOARD_ROWS:
            if word in row:
                return True

            if word[::-1] in row:
                return True

        return False

    @classmethod
    def is_garbage(cls, text: str) -> bool:

        text = text.strip()

        if len(text) < 2:
            return True

        # symbols only
        if re.fullmatch(r"[!@#$%^&*()_+=\-?><.,/\\|{}\[\]~`]+", text):
            return True

        # only digits
        if re.fullmatch(r"\d+", text):
            return True

        # repeated character
        if re.fullmatch(r"(.)\1{5,}", text):
            return True

        # real keyboard smash
        if text.isalpha() and cls._is_keyboard_smash(text):
            return True

        # nonsense word with almost no vowels
        if text.isalpha() and len(text) >= 12:

            vowels = sum(ch in "aeiou" for ch in text.lower())

            if vowels / len(text) < 0.15:
                return True

        return False

    @classmethod
    def check(cls, user_id: str, message: str):

        now = time.time()

        # -----------------------------
        # cooldown
        # -----------------------------

        if user_id in cls.cooldown_users:

            end = cls.cooldown_users[user_id]

            if now < end:

                return {
                    "safe": False,
                    "reason": "Cooldown",
                    "message": (
                        "You're sending requests too quickly. "
                        "Please wait a moment and try again."
                    ),
                }

            del cls.cooldown_users[user_id]

        # -----------------------------
        # garbage
        # -----------------------------

        if cls.is_garbage(message):

            return {
                "safe": False,
                "reason": "Garbage",
                "message": (
                    "I couldn't understand that message. "
                    "Please ask a college-related question."
                ),
            }

        # -----------------------------
        # duplicate
        # -----------------------------

        history = cls.repeated_messages[user_id]

        history.append(message.lower())

        if len(history) > cls.DUPLICATE_LIMIT:
            history.pop(0)

        if (
            len(history) == cls.DUPLICATE_LIMIT
            and len(set(history)) == 1
        ):
            return {
                "safe": False,
                "reason": "Repeated",
                "message": (
                    "It looks like you've sent the same question multiple times. "
                    "Please rephrase it."
                ),
            }

        # -----------------------------
        # flood
        # -----------------------------

        requests = cls.user_requests[user_id]

        requests.append(now)

        while requests and now - requests[0] > cls.FLOOD_WINDOW:
            requests.popleft()

        if len(requests) > cls.FLOOD_LIMIT:

            cls.cooldown_users[user_id] = now + cls.COOLDOWN

            return {
                "safe": False,
                "reason": "Flood",
                "message": (
                    "Too many requests detected. "
                    "Please wait one minute and try again."
                ),
            }

        return {
            "safe": True,
            "reason": "OK",
            "message": "",
        }


if __name__ == "__main__":

    user = "student"

    while True:

        q = input("\nQuestion : ")

        print(SpamDetector.check(user, q))