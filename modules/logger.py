"""
=========================================================
SREYAS ASSISTANT
Logging Module
=========================================================
"""

import os
from datetime import datetime


class ChatLogger:

    LOG_FOLDER = "data/logs"

    ERROR_FILE = os.path.join(LOG_FOLDER, "errors.txt")

    @classmethod
    def initialize(cls):

        os.makedirs(cls.LOG_FOLDER, exist_ok=True)

    # ---------------------------------------------------

    @classmethod
    def log_chat(
        cls,
        session_id,
        question,
        category,
        confidence,
        answer,
        response_time,
        language="en",
        fact_shortcut_used=False,
        kb_chunk="GENERAL",
        suggestions=None,
        enquiry_triggered=False,
        email_triggered=False,
        email_delivery_status="N/A",
        feedback="Pending"
    ):

        cls.initialize()

        today = datetime.now().strftime("%Y-%m-%d")

        filename = os.path.join(

            cls.LOG_FOLDER,

            f"chat_{today}.txt"

        )

        with open(filename, "a", encoding="utf-8") as f:

            f.write("=" * 70 + "\n")

            f.write(f"Timestamp            : {datetime.now()}\n")

            f.write(f"Session ID           : {session_id}\n")

            f.write(f"Language             : {language}\n")

            f.write(f"Question             : {question}\n")

            f.write(f"Category             : {category}\n")

            f.write(f"Confidence           : {confidence}\n")

            f.write(f"Fact Shortcut Used   : {fact_shortcut_used}\n")

            f.write(f"KB Chunk             : {kb_chunk}\n")

            f.write(f"Suggestions          : {suggestions or []}\n")

            f.write(f"Enquiry Triggered    : {enquiry_triggered}\n")

            f.write(f"Response Time        : {response_time} ms\n")

            f.write(f"Email Triggered      : {email_triggered}\n")

            f.write(f"Email Delivery Status: {email_delivery_status}\n")

            f.write(f"Feedback             : {feedback}\n")

            f.write(f"Answer:\n{answer}\n")

            f.write("=" * 70 + "\n\n")

    # ---------------------------------------------------

    @classmethod

    def log_error(cls, error):

        cls.initialize()

        with open(cls.ERROR_FILE, "a", encoding="utf-8") as f:

            f.write(

                f"[{datetime.now()}] {str(error)}\n"

            )

    # ---------------------------------------------------

    @classmethod

    def log_feedback(

        cls,

        session_id,

        feedback

    ):

        cls.initialize()

        today = datetime.now().strftime("%Y-%m-%d")

        filename = os.path.join(

            cls.LOG_FOLDER,

            f"feedback_{today}.txt"

        )

        with open(filename, "a", encoding="utf-8") as f:

            f.write(

                f"{datetime.now()} | "

                f"{session_id} | "

                f"{feedback}\n"

            )


# =====================================================
# TEST
# =====================================================

if __name__ == "__main__":

    ChatLogger.log_chat(

        session_id="student123",

        question="Placement package?",

        category="PLACEMENTS",

        confidence=0.91,

        answer="Highest package is 21.18 LPA.",

        response_time=37

    )

    print("Chat logged successfully.")