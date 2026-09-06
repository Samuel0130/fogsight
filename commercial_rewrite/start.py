import os
import webbrowser
from threading import Thread
from time import sleep


def _load_env():
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    load_dotenv()


_load_env()

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8010"))


def _open_browser():
    sleep(1.2)
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    Thread(target=_open_browser, daemon=True).start()
    import uvicorn

    uvicorn.run("server:app", host=HOST, port=PORT, reload=False)

