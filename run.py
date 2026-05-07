"""Production entry point: binds to PORT from the environment (Railway, Docker, etc.).

Local dev with reload: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
