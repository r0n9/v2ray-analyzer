import ipaddress
import json
import logging
import os
import sqlite3
import threading
import urllib.request
from typing import Dict, Optional, Tuple

logger = logging.getLogger("v2ray_analyzer.geo")

class GeoResolver:
    """
    Lightweight IP Geolocation resolver with local SQLite caching
    and non-blocking background query capability.
    """

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.uri = f"file:{self.db_path}?nolock=1"
        self._lock = threading.Lock()
        self._init_table()

    def _get_connection(self):
        conn = sqlite3.connect(self.uri, uri=True, timeout=60.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = DELETE")
        except Exception:
            pass
        return conn

    def _init_table(self):
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS ip_geo_cache (
                    ip TEXT PRIMARY KEY,
                    country TEXT DEFAULT '',
                    region TEXT DEFAULT '',
                    city TEXT DEFAULT '',
                    isp TEXT DEFAULT '',
                    display_location TEXT DEFAULT '',
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS client_aliases (
                    ip TEXT PRIMARY KEY,
                    alias TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def is_private_ip(ip: str) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip)
            return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved
        except ValueError:
            return False

    def get_ip_info(self, ip: str) -> Dict[str, str]:
        """
        Get cached geo location and alias for an IP.
        """
        if self.is_private_ip(ip):
            return {
                "ip": ip,
                "location": "局域网/私有IP",
                "isp": "内部网络",
                "alias": self.get_alias(ip) or ""
            }

        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM ip_geo_cache WHERE ip = ?", (ip,)).fetchone()
            alias = self.get_alias(ip) or ""

            if row:
                loc = row["display_location"] or f"{row['region']} {row['city']}".strip() or row["country"] or "未知"
                return {
                    "ip": ip,
                    "location": loc,
                    "isp": row["isp"] or "",
                    "alias": alias
                }

        # If not cached, launch background task to resolve
        threading.Thread(target=self._resolve_ip_background, args=(ip,), daemon=True).start()
        return {
            "ip": ip,
            "location": "解析中...",
            "isp": "",
            "alias": self.get_alias(ip) or ""
        }

    def _resolve_ip_background(self, ip: str):
        with self._lock:
            # Double check cache
            with self._get_connection() as conn:
                row = conn.execute("SELECT ip FROM ip_geo_cache WHERE ip = ?", (ip,)).fetchone()
                if row:
                    return

            location, isp, country, region, city = self._fetch_ip_info(ip)
            with self._get_connection() as conn:
                conn.execute("""
                INSERT OR REPLACE INTO ip_geo_cache (ip, country, region, city, isp, display_location, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (ip, country, region, city, isp, location))
                conn.commit()

    def _fetch_ip_info(self, ip: str) -> Tuple[str, str, str, str, str]:
        """
        Fetch IP info from free public API with fallback.
        """
        # Primary: pconline (Very accurate for China IPs)
        try:
            url = f"https://whois.pconline.com.cn/ipJson.jsp?ip={ip}&json=true"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("gbk", errors="ignore"))
                    region = data.get("pro", "")
                    city = data.get("city", "")
                    addr = data.get("addr", "")
                    isp = ""
                    if " " in addr:
                        parts = addr.split()
                        if len(parts) > 1:
                            isp = parts[-1].lstrip("/")
                    elif addr:
                        isp = addr.lstrip("/")
                    
                    # Clean up duplicate names (e.g. 上海市 上海市)
                    if region and city:
                        if region == city:
                            display = region
                        else:
                            display = f"{region} {city}".strip()
                    else:
                        display = region or city or addr or "中国"

                    if addr and not isp:
                        isp = addr
                    return display, isp, "中国", region, city
        except Exception as e:
            logger.debug(f"Pconline lookup failed for {ip}: {e}")

        # Fallback: ip-api.com
        try:
            url = f"http://ip-api.com/json/{ip}?lang=zh-CN"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8", errors="ignore"))
                    if data.get("status") == "success":
                        country = data.get("country", "")
                        region = data.get("regionName", "")
                        city = data.get("city", "")
                        isp = data.get("isp", "")
                        display = f"{country} {region} {city}".strip()
                        return display, isp, country, region, city
        except Exception as e:
            logger.debug(f"ip-api lookup failed for {ip}: {e}")

        return "未知位置", "", "", "", ""

    def get_alias(self, ip: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT alias FROM client_aliases WHERE ip = ?", (ip,)).fetchone()
            if row and row["alias"]:
                return row["alias"]
        return None

    def set_alias(self, ip: str, alias: str, note: str = ""):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO client_aliases (ip, alias, note, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (ip, alias, note))
            conn.commit()

    def get_all_aliases(self) -> Dict[str, Dict[str, str]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT ip, alias, note FROM client_aliases").fetchall()
            return {r["ip"]: {"alias": r["alias"], "note": r["note"]} for r in rows}
