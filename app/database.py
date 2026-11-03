import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("v2ray_analyzer.database")

class Database:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        # Ensure parent directory exists (e.g. /data in Docker)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # Clean up corrupted 0-byte file if left behind from previous startup failure
        if os.path.exists(self.db_path) and os.path.getsize(self.db_path) == 0:
            try:
                os.remove(self.db_path)
            except Exception:
                pass

        self._lock = threading.Lock()
        self._init_db()
        logger.info("Database initialized OK: %s", self.db_path)

    def get_connection(self) -> sqlite3.Connection:
        # Use standard file path (no URI tricks) for maximum compatibility
        conn = sqlite3.connect(self.db_path, timeout=60.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Aggressive pragmas to minimize disk I/O — safe because we have a
        # single-process writer protected by self._lock and data is re-importable.
        for pragma in [
            "PRAGMA journal_mode = OFF",      # No journal file at all
            "PRAGMA synchronous = OFF",       # No fsync
            "PRAGMA locking_mode = EXCLUSIVE", # Hold lock, avoid re-acquire
            "PRAGMA temp_store = MEMORY",     # Temp tables in RAM
            "PRAGMA cache_size = -64000",     # 64 MB page cache
        ]:
            try:
                conn.execute(pragma)
            except Exception as e:
                logger.debug("PRAGMA skipped (%s): %s", pragma, e)

        return conn

    def _init_db(self):
        """Create tables using individual execute() calls — avoids executescript()
        which triggers implicit COMMIT via sqlite3_exec() and can cause disk I/O
        errors on PVE / restricted-VFS Docker environments."""
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS access_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    client_ip TEXT NOT NULL,
                    client_port INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'accepted',
                    network TEXT DEFAULT 'tcp',
                    target_host TEXT NOT NULL,
                    target_port INTEGER NOT NULL,
                    outbound_tag TEXT DEFAULT ''
                )
                """)

                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_date_time ON access_logs (date, timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_client_ip ON access_logs (client_ip, date)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_target_host ON access_logs (target_host)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_hour ON access_logs (hour)")

                conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_files (
                    filename TEXT PRIMARY KEY,
                    last_offset INTEGER DEFAULT 0,
                    last_mtime REAL DEFAULT 0,
                    total_lines INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                conn.commit()
            finally:
                conn.close()

    def insert_logs_batch(self, logs: List[Dict[str, Any]]) -> int:
        if not logs:
            return 0
        data = [
            (
                item["timestamp"],
                item["date"],
                item["hour"],
                item["client_ip"],
                item.get("client_port", 0),
                item.get("status", "accepted"),
                item.get("network", "tcp"),
                item["target_host"],
                item.get("target_port", 0),
                item.get("outbound_tag", ""),
            )
            for item in logs
        ]
        with self._lock:
            conn = self.get_connection()
            try:
                conn.executemany("""
                INSERT INTO access_logs (
                    timestamp, date, hour, client_ip, client_port,
                    status, network, target_host, target_port, outbound_tag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, data)
                conn.commit()
            finally:
                conn.close()
        return len(logs)

    def get_processed_file(self, filename: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM processed_files WHERE filename = ?", (filename,)
            ).fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()

    def update_processed_file(self, filename: str, offset: int, mtime: float, total_lines: int):
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute("""
                INSERT OR REPLACE INTO processed_files (filename, last_offset, last_mtime, total_lines, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (filename, offset, mtime, total_lines))
                conn.commit()
            finally:
                conn.close()

    def get_overview_stats(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get KPI overview metrics and charts data.
        """
        with self.get_connection() as conn:
            # Determine latest date if not specified
            if not date:
                row = conn.execute("SELECT MAX(date) AS max_date FROM access_logs").fetchone()
                date = row["max_date"] if row and row["max_date"] else ""

            if not date:
                return {
                    "date": "",
                    "total_requests": 0,
                    "unique_ips": 0,
                    "unique_domains": 0,
                    "hourly_distribution": [0] * 24,
                    "protocol_distribution": {},
                    "top_domains": [],
                    "top_ips": [],
                }

            # 1. Total requests & uniques for the selected date
            summary = conn.execute("""
            SELECT 
                COUNT(*) AS total_requests,
                COUNT(DISTINCT client_ip) AS unique_ips,
                COUNT(DISTINCT target_host) AS unique_domains
            FROM access_logs
            WHERE date = ?
            """, (date,)).fetchone()

            # 2. Hourly distribution (0-23)
            hourly_rows = conn.execute("""
            SELECT hour, COUNT(*) AS count
            FROM access_logs
            WHERE date = ?
            GROUP BY hour
            ORDER BY hour
            """, (date,)).fetchall()
            hourly = [0] * 24
            for r in hourly_rows:
                hourly[r["hour"]] = r["count"]

            # 3. Protocol distribution
            proto_rows = conn.execute("""
            SELECT network, COUNT(*) AS count
            FROM access_logs
            WHERE date = ?
            GROUP BY network
            """, (date,)).fetchall()
            protocols = {r["network"]: r["count"] for r in proto_rows}

            # 4. Outbound tag distribution
            outbound_rows = conn.execute("""
            SELECT COALESCE(NULLIF(outbound_tag, ''), 'direct') AS tag, COUNT(*) AS count
            FROM access_logs
            WHERE date = ?
            GROUP BY tag
            """, (date,)).fetchall()
            outbounds = {r["tag"]: r["count"] for r in outbound_rows}

            # 5. Top 10 target domains (excluding raw IPs)
            top_domains = conn.execute("""
            SELECT target_host, COUNT(*) AS count
            FROM access_logs
            WHERE date = ? AND NOT (target_host GLOB '[0-9]*.[0-9]*.[0-9]*.[0-9]*' OR target_host GLOB '*:*')
            GROUP BY target_host
            ORDER BY count DESC
            LIMIT 10
            """, (date,)).fetchall()

            # 6. Top 10 target IPs (raw IPv4 and IPv6)
            top_target_ips = conn.execute("""
            SELECT target_host, COUNT(*) AS count
            FROM access_logs
            WHERE date = ? AND (target_host GLOB '[0-9]*.[0-9]*.[0-9]*.[0-9]*' OR target_host GLOB '*:*')
            GROUP BY target_host
            ORDER BY count DESC
            LIMIT 10
            """, (date,)).fetchall()

            # 7. Top 10 active client IPs
            top_ips = conn.execute("""
            SELECT client_ip, COUNT(*) AS count
            FROM access_logs
            WHERE date = ?
            GROUP BY client_ip
            ORDER BY count DESC
            LIMIT 10
            """, (date,)).fetchall()

            # Available dates for dropdown
            date_rows = conn.execute("""
            SELECT DISTINCT date FROM access_logs ORDER BY date DESC LIMIT 30
            """).fetchall()
            available_dates = [r["date"] for r in date_rows]

            return {
                "date": date,
                "available_dates": available_dates,
                "total_requests": summary["total_requests"],
                "unique_ips": summary["unique_ips"],
                "unique_domains": summary["unique_domains"],
                "hourly_distribution": hourly,
                "protocol_distribution": protocols,
                "outbound_distribution": outbounds,
                "top_domains": [{"domain": r["target_host"], "count": r["count"]} for r in top_domains],
                "top_target_ips": [{"ip": r["target_host"], "count": r["count"]} for r in top_target_ips],
                "top_ips": [{"ip": r["client_ip"], "count": r["count"]} for r in top_ips],
            }

    def get_all_ips_stats(self, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all client IPs with total counts, date counts, and active timestamps.
        """
        with self.get_connection() as conn:
            if not date:
                row = conn.execute("SELECT MAX(date) AS max_date FROM access_logs").fetchone()
                date = row["max_date"] if row and row["max_date"] else ""

            query = """
            SELECT 
                client_ip,
                COUNT(*) AS total_count,
                SUM(CASE WHEN date = ? THEN 1 ELSE 0 END) AS today_count,
                MIN(timestamp) AS first_seen,
                MAX(timestamp) AS last_seen
            FROM access_logs
            GROUP BY client_ip
            ORDER BY today_count DESC, total_count DESC
            """
            rows = conn.execute(query, (date,)).fetchall()
            return [dict(r) for r in rows]

    def get_ip_detail(self, client_ip: str, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Deep-dive analytics for a single client IP.
        """
        with self.get_connection() as conn:
            # 1. Base counts
            stats = conn.execute("""
            SELECT 
                COUNT(*) AS total_count,
                MIN(timestamp) AS first_seen,
                MAX(timestamp) AS last_seen
            FROM access_logs
            WHERE client_ip = ?
            """, (client_ip,)).fetchone()

            # 2. Hourly activity
            date_cond = "AND date = ?" if date else ""
            params = [client_ip, date] if date else [client_ip]

            hourly_rows = conn.execute(f"""
            SELECT hour, COUNT(*) AS count
            FROM access_logs
            WHERE client_ip = ? {date_cond}
            GROUP BY hour
            ORDER BY hour
            """, params).fetchall()

            hourly = [0] * 24
            for r in hourly_rows:
                hourly[r["hour"]] = r["count"]

            # 3. Top visited domains
            top_domains = conn.execute(f"""
            SELECT target_host, COUNT(*) AS count
            FROM access_logs
            WHERE client_ip = ? {date_cond}
            GROUP BY target_host
            ORDER BY count DESC
            LIMIT 20
            """, params).fetchall()

            # 4. Top visited ports
            top_ports = conn.execute(f"""
            SELECT target_port, COUNT(*) AS count
            FROM access_logs
            WHERE client_ip = ? {date_cond}
            GROUP BY target_port
            ORDER BY count DESC
            LIMIT 10
            """, params).fetchall()

            # 5. Recent 30 logs for this IP
            recent_logs = conn.execute("""
            SELECT timestamp, network, target_host, target_port, outbound_tag, status
            FROM access_logs
            WHERE client_ip = ?
            ORDER BY id DESC
            LIMIT 30
            """, (client_ip,)).fetchall()

            return {
                "client_ip": client_ip,
                "date": date,
                "total_count": stats["total_count"] if stats else 0,
                "first_seen": stats["first_seen"] if stats else "",
                "last_seen": stats["last_seen"] if stats else "",
                "hourly_distribution": hourly,
                "top_domains": [{"domain": r["target_host"], "count": r["count"]} for r in top_domains],
                "top_ports": [{"port": r["target_port"], "count": r["count"]} for r in top_ports],
                "recent_logs": [dict(r) for r in recent_logs],
            }

    def search_logs(
        self,
        date: Optional[str] = None,
        client_ip: Optional[str] = None,
        target_host: Optional[str] = None,
        target_port: Optional[int] = None,
        network: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[Dict[str, Any]], int]:
        conditions = []
        params = []

        if date:
            conditions.append("date = ?")
            params.append(date)
        if client_ip:
            conditions.append("client_ip = ?")
            params.append(client_ip)
        if target_host:
            conditions.append("target_host LIKE ?")
            params.append(f"%{target_host}%")
        if target_port:
            conditions.append("target_port = ?")
            params.append(target_port)
        if network:
            conditions.append("network = ?")
            params.append(network.lower())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_connection() as conn:
            # Count total
            count_query = f"SELECT COUNT(*) AS total FROM access_logs {where_clause}"
            total = conn.execute(count_query, params).fetchone()["total"]

            # Pagination
            offset = (page - 1) * page_size
            data_query = f"""
            SELECT id, timestamp, client_ip, client_port, network, target_host, target_port, outbound_tag, status
            FROM access_logs
            {where_clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """
            rows = conn.execute(data_query, params + [page_size, offset]).fetchall()
            return [dict(r) for r in rows], total
