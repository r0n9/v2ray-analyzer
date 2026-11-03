import os
import secrets
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from .database import Database
from .collector import LogCollector
from .geo import GeoResolver

router = APIRouter(prefix="/api")
security = HTTPBasic(auto_error=False)

class AliasRequest(BaseModel):
    ip: str
    alias: str
    note: Optional[str] = ""

def create_api_router(db: Database, collector: LogCollector, geo: GeoResolver, config: Dict[str, Any]) -> APIRouter:
    server_cfg = config.get("server", {})
    auth_enabled = server_cfg.get("auth_enabled", False)
    auth_user = os.environ.get("ADMIN_USER", server_cfg.get("username", "admin"))
    auth_pass = os.environ.get("ADMIN_PASSWORD", server_cfg.get("password", "changeme888"))

    def verify_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
        if not auth_enabled:
            return True
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        correct_user = secrets.compare_digest(credentials.username, auth_user)
        correct_pass = secrets.compare_digest(credentials.password, auth_pass)
        if not (correct_user and correct_pass):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Basic"},
            )
        return True

    @router.get("/auth/status")
    def get_auth_status():
        return {"auth_enabled": auth_enabled}

    @router.get("/stats/overview")
    def get_overview(date: Optional[str] = Query(None), _auth=Depends(verify_auth)):
        stats = db.get_overview_stats(date)
        # Enrich top IPs with geo & alias
        for item in stats.get("top_ips", []):
            info = geo.get_ip_info(item["ip"])
            item["location"] = info["location"]
            item["isp"] = info["isp"]
            item["alias"] = info["alias"]
        return stats

    @router.get("/stats/ips")
    def get_all_ips(date: Optional[str] = Query(None), _auth=Depends(verify_auth)):
        ips = db.get_all_ips_stats(date)
        for item in ips:
            info = geo.get_ip_info(item["client_ip"])
            item["location"] = info["location"]
            item["isp"] = info["isp"]
            item["alias"] = info["alias"]
        return {"ips": ips}

    @router.get("/stats/ip/{client_ip}")
    def get_ip_detail(client_ip: str, date: Optional[str] = Query(None), _auth=Depends(verify_auth)):
        detail = db.get_ip_detail(client_ip, date)
        info = geo.get_ip_info(client_ip)
        detail["location"] = info["location"]
        detail["isp"] = info["isp"]
        detail["alias"] = info["alias"]
        return detail

    @router.post("/alias")
    def update_alias(req: AliasRequest, _auth=Depends(verify_auth)):
        geo.set_alias(req.ip, req.alias, req.note or "")
        return {"status": "success", "ip": req.ip, "alias": req.alias}

    @router.get("/logs")
    def search_logs(
        date: Optional[str] = Query(None),
        client_ip: Optional[str] = Query(None),
        target_host: Optional[str] = Query(None),
        target_port: Optional[int] = Query(None),
        network: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=10, le=200),
        _auth=Depends(verify_auth),
    ):
        logs, total = db.search_logs(
            date=date,
            client_ip=client_ip,
            target_host=target_host,
            target_port=target_port,
            network=network,
            page=page,
            page_size=page_size,
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "logs": logs,
        }

    @router.post("/sync")
    def trigger_sync(_auth=Depends(verify_auth)):
        result = collector.sync_all()
        return {"status": "success", "result": result}

    return router
