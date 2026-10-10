"""Walkworth tests. Run: npm test (offline-safe: no API keys, no network).

Importing this package points WALKWORTH_DATA_DIR at a throwaway folder and
blanks the API keys before any walkworth module loads its config.
"""

import os
import tempfile

TMP = tempfile.mkdtemp(prefix="walkworth-test-")
os.environ["WALKWORTH_DATA_DIR"] = TMP
for key in ("LLM_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY", "RENDER_EXTERNAL_URL", "WALKWORTH_EMBEDDED_WORKER",
            "ACCESS_CODE"):
    os.environ[key] = ""
os.environ["SUBMIT_LIMIT"] = "3"
os.environ["LLM_MAX_PER_DAY"] = "0"
os.environ["LLM_MAX_RPM"] = "0"
