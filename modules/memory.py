"""
=========================================================
SREYAS ASSISTANT
Conversation Memory Module
=========================================================
"""

from collections import deque
from datetime import datetime, timedelta


class ConversationMemory:

    MAX_HISTORY = 5
    SESSION_TIMEOUT = 30  # minutes

    # session_id -> session data
    sessions = {}

    FOLLOW_UP_WORDS = {
        "it", "that", "this",
        "also", "and",
        "what about",
        "then",
        "next",
        "its",
        "their",
        "those",
        "these",
        "more",
        "tell me more"
    }

    # -------------------------------------------------
    # Create Session
    # -------------------------------------------------

    @classmethod
    def create_session(cls, session_id):

        if session_id not in cls.sessions:

            cls.sessions[session_id] = {

                "history": deque(maxlen=cls.MAX_HISTORY),

                "last_category": "GENERAL",

                "last_updated": datetime.now()

            }

    # -------------------------------------------------
    # Save Message
    # -------------------------------------------------

    @classmethod
    def add_message(cls,
                    session_id,
                    user_message,
                    bot_response,
                    category):

        cls.create_session(session_id)

        cls.sessions[session_id]["history"].append({

            "user": user_message,

            "bot": bot_response,

            "category": category

        })

        cls.sessions[session_id]["last_category"] = category

        cls.sessions[session_id]["last_updated"] = datetime.now()

    # -------------------------------------------------
    # Detect Follow-up
    # -------------------------------------------------

    @classmethod
    def is_follow_up(cls, message):

        message = message.lower().strip()

        if len(message.split()) <= 4:
            return True

        for word in cls.FOLLOW_UP_WORDS:

            if word in message:
                return True

        return False

    # -------------------------------------------------
    # Build Context
    # -------------------------------------------------

    @classmethod
    def get_context(cls,
                    session_id,
                    current_question):

        cls.create_session(session_id)

        session = cls.sessions[session_id]

        follow_up = cls.is_follow_up(current_question)

        context = ""

        if follow_up:

            for item in session["history"]:

                context += (
                    f"User: {item['user']}\n"
                    f"Bot: {item['bot']}\n"
                )

        return {

            "follow_up": follow_up,

            "context": context,

            "last_category": session["last_category"]

        }

    # -------------------------------------------------
    # Cleanup Old Sessions
    # -------------------------------------------------

    @classmethod
    def cleanup(cls):

        now = datetime.now()

        remove = []

        for sid, data in cls.sessions.items():

            if now - data["last_updated"] > timedelta(minutes=cls.SESSION_TIMEOUT):

                remove.append(sid)

        for sid in remove:

            del cls.sessions[sid]

    # -------------------------------------------------
    # Session Count
    # -------------------------------------------------

    @classmethod
    def session_count(cls):

        cls.cleanup()

        return len(cls.sessions)


# =====================================================
# TEST
# =====================================================

if __name__ == "__main__":

    sid = "demo123"

    ConversationMemory.add_message(

        sid,

        "Tell me about placements",

        "Placements information...",

        "PLACEMENTS"

    )

    ConversationMemory.add_message(

        sid,

        "Highest package?",

        "21.18 LPA",

        "PLACEMENTS"

    )

    print("\nSession Count")

    print(ConversationMemory.session_count())

    print("\nContext")

    print(

        ConversationMemory.get_context(

            sid,

            "What about internships?"

        )

    )