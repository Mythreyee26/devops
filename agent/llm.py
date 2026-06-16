"""
The model factory: the agent uses Google's Gemini.

Needs a GOOGLE_API_KEY and the langchain-google-genai package:
    pip install langchain-google-genai python-dotenv

The key is loaded from a `.env` file in this folder (see .env.example) OR from a
GOOGLE_API_KEY environment variable. Using .env means you set it ONCE and it works
in every terminal — no need to `export` each time.

Pick a different Gemini model with the GEMINI_MODEL env var (default: gemini-2.5-flash).
"""

import os
import sys

from dotenv import load_dotenv

# Load a .env file sitting next to this script (if present). This is why you don't
# have to `export GOOGLE_API_KEY` in every new terminal.
load_dotenv()


def get_llm():
    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        sys.exit(
            "ERROR: GOOGLE_API_KEY is not set.\n"
            "Fix it one of these ways:\n"
            "  1. Create a file  devops/agent/.env  with this line:\n"
            "         GOOGLE_API_KEY=your-key-here\n"
            "  2. Or set it for this terminal:\n"
            "         export GOOGLE_API_KEY=\"your-key-here\"   (Git Bash)\n"
            "Get a key at https://aistudio.google.com/apikey"
        )

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        temperature=0,
    )
