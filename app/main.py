import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import Database
from .geo import GeoResolver
from .collector import LogCollector
from .api import create_api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("v2ray_analyzer")

# Load configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))

config = {
    "server": {
        "host": "0.0.0.0",
        "port": 8848,
        "auth_enabled": True,
        "username": "admin",
        "password": "changeme888"
    },
    "logs": {
        "log_dir": BASE_DIR,
        "access_log_path": os.path.join(BASE_DIR, "access.log"),
        "scan_interval_seconds": 10,
        "retention_days": 180
    },
    "database": {
        "db_path": os.path.join(BASE_DIR, "v2ray_stats.db")
    }
}

if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
            # Deep update
            for section in ["server", "logs", "database"]:
                if section in user_cfg:
                    config[section].update(user_cfg[section])
    except Exception as e:
        logger.warning(f"Failed to read config file {CONFIG_PATH}: {e}, using defaults.")

# Environment variable overrides
if os.environ.get("LOG_DIR"):
    config["logs"]["log_dir"] = os.environ["LOG_DIR"]
if os.environ.get("ACCESS_LOG_PATH"):
    config["logs"]["access_log_path"] = os.environ["ACCESS_LOG_PATH"]
if os.environ.get("DB_PATH"):
    config["database"]["db_path"] = os.environ["DB_PATH"]
if os.environ.get("PORT"):
    config["server"]["port"] = int(os.environ["PORT"])
if os.environ.get("RETENTION_DAYS"):
    try:
        config["logs"]["retention_days"] = int(os.environ["RETENTION_DAYS"])
    except ValueError:
        pass

# Initialize singletons
db = Database(config["database"]["db_path"])
geo = GeoResolver(db)
collector = LogCollector(
    db=db,
    log_dir=config["logs"]["log_dir"],
    access_log_path=config["logs"]["access_log_path"],
    retention_days=config["logs"].get("retention_days", 180),
)

# Background log scanner
stop_event = threading.Event()

def background_log_scanner():
    logger.info("Starting background log scanner thread...")
    # Initial scan of both historical files, access.log, and expired data cleanup
    try:
        collector.sync_all()
    except Exception as e:
        logger.error(f"Error during initial sync: {e}")

    interval = max(3, config["logs"].get("scan_interval_seconds", 10))
    last_cleanup_time = time.time()
    CLEANUP_INTERVAL_SECONDS = 86400  # Run log cleanup once every 24 hours

    while not stop_event.is_set():
        try:
            collector.sync_access_log()
        except Exception as e:
            logger.error(f"Error during periodic access.log sync: {e}")

        # Check daily cleanup of expired logs (> retention_days)
        now = time.time()
        if now - last_cleanup_time >= CLEANUP_INTERVAL_SECONDS:
            try:
                collector.clean_expired_logs()
                last_cleanup_time = now
            except Exception as e:
                logger.error(f"Error during periodic log cleanup: {e}")

        stop_event.wait(interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background thread
    scanner_thread = threading.Thread(target=background_log_scanner, daemon=True)
    scanner_thread.start()
    yield
    stop_event.set()
    scanner_thread.join(timeout=2)

app = FastAPI(title="V2Ray Log Analyzer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API
api_router = create_api_router(db, collector, geo, config)
app.include_router(api_router)

# Mount static frontend
STATIC_DIR = os.path.join(BASE_DIR, "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
@app.head("/")
def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "V2Ray Log Analyzer API is running. UI index.html not found."}

if __name__ == "__main__":
    import uvicorn
    host = config["server"]["host"]
    port = config["server"]["port"]
    logger.info(f"Starting server on http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
