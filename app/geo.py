import ipaddress
import json
import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

logger = logging.getLogger("v2ray_analyzer.geo")


class GeoResolver:
    """
    Lightweight IP Geolocation resolver with local SQLite caching
    and non-blocking background query capability.

    Optimized:
    - Accepts a Database instance to reuse connection management
    - Uses a bounded ThreadPoolExecutor instead of unlimited Thread spawning
    - In-memory cache for aliases to reduce DB reads in hot loops
    """

    def __init__(self, db):
        """
        Args:
            db: a Database instance whose get_connection() returns a thread-local
                persistent SQLite connection with WAL/PRAGMA already configured.
        """
        self.db = db
        self._lock = threading.Lock()
        # Bounded thread pool for background IP resolution (max 3 concurrent)
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="geo")
        # In-flight set to avoid submitting duplicate resolution tasks
        self._pending: set = set()
        self._pending_lock = threading.Lock()
        self._init_table()

    def _get_connection(self):
        """Reuse the Database's per-thread connection pool."""
        return self.db.get_connection()

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
            except Exception:
                conn.rollback()
                raise

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

        conn = self._get_connection()
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

        # If not cached, submit to bounded thread pool (deduplicated)
        with self._pending_lock:
            if ip not in self._pending:
                self._pending.add(ip)
                self._executor.submit(self._resolve_ip_background, ip)

        return {
            "ip": ip,
            "location": "解析中...",
            "isp": "",
            "alias": alias
        }

    def get_ip_info_batch(self, ips: list) -> Dict[str, Dict[str, str]]:
        """
        Batch lookup for multiple IPs — single DB query for geo cache + aliases,
        much more efficient than calling get_ip_info() in a loop.
        """
        if not ips:
            return {}

        conn = self._get_connection()
        result: Dict[str, Dict[str, str]] = {}
        unique_ips = list(set(ips))

        # Batch fetch geo cache
        placeholders = ",".join("?" * len(unique_ips))
        geo_rows = conn.execute(
            f"SELECT * FROM ip_geo_cache WHERE ip IN ({placeholders})", unique_ips
        ).fetchall()
        geo_map = {r["ip"]: r for r in geo_rows}

        # Batch fetch aliases
        alias_rows = conn.execute(
            f"SELECT ip, alias, note FROM client_aliases WHERE ip IN ({placeholders})", unique_ips
        ).fetchall()
        alias_map = {r["ip"]: (r["alias"] or r["note"] or "") for r in alias_rows if (r["alias"] or r["note"])}

        for ip in unique_ips:
            if self.is_private_ip(ip):
                result[ip] = {
                    "ip": ip,
                    "location": "局域网/私有IP",
                    "isp": "内部网络",
                    "alias": alias_map.get(ip, "")
                }
                continue

            alias = alias_map.get(ip, "")
            geo = geo_map.get(ip)
            if geo:
                loc = geo["display_location"] or f"{geo['region']} {geo['city']}".strip() or geo["country"] or "未知"
                result[ip] = {
                    "ip": ip,
                    "location": loc,
                    "isp": geo["isp"] or "",
                    "alias": alias
                }
            else:
                # Submit background resolution (deduplicated)
                with self._pending_lock:
                    if ip not in self._pending:
                        self._pending.add(ip)
                        self._executor.submit(self._resolve_ip_background, ip)
                result[ip] = {
                    "ip": ip,
                    "location": "解析中...",
                    "isp": "",
                    "alias": alias
                }

        return result

    def _resolve_ip_background(self, ip: str):
        try:
            conn = self._get_connection()
            # Double check cache
            row = conn.execute("SELECT ip FROM ip_geo_cache WHERE ip = ?", (ip,)).fetchone()
            if row:
                return

            location, isp, country, region, city = self._fetch_ip_info(ip)
            with self._lock:
                conn = self._get_connection()
                try:
                    conn.execute("""
                    INSERT OR REPLACE INTO ip_geo_cache (ip, country, region, city, isp, display_location, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (ip, country, region, city, isp, location))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        finally:
            with self._pending_lock:
                self._pending.discard(ip)

    def _fetch_ip_info(self, ip: str) -> Tuple[str, str, str, str, str]:
        """
        Fetch IP info from free public API with fallback.
        """
        import urllib.request

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
        conn = self._get_connection()
        row = conn.execute("SELECT alias, note FROM client_aliases WHERE ip = ?", (ip,)).fetchone()
        if row:
            val = row["alias"] or row["note"]
            if val:
                return val
        return None

    def set_alias(self, ip: str, alias: str, note: str = ""):
        conn = self._get_connection()
        conn.execute("""
        INSERT OR REPLACE INTO client_aliases (ip, alias, note, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (ip, alias, note))
        conn.commit()

    def get_all_aliases(self) -> Dict[str, Dict[str, str]]:
        conn = self._get_connection()
        rows = conn.execute("SELECT ip, alias, note FROM client_aliases").fetchall()
        return {r["ip"]: {"alias": r["alias"], "note": r["note"]} for r in rows}
