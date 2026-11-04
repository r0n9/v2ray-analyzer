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
        # Thread-local storage for persistent connections (avoids re-creating per call)
        self._local = threading.local()
        self._init_db()
        logger.info("Database initialized OK: %s", self.db_path)

    def get_connection(self) -> sqlite3.Connection:
        """Return a persistent per-thread connection. PRAGMA is executed only once
        per thread, eliminating the overhead of repeated connect + pragma setup."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except Exception:
                # Connection broken, recreate
                try:
                    conn.close()
                except Exception:
                    pass
                self._local.conn = None

        # Create new connection for this thread
        conn = sqlite3.connect(self.db_path, timeout=60.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Pragmas executed only once per thread connection
        for pragma in [
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA temp_store = MEMORY",
            "PRAGMA cache_size = -64000",
        ]:
            try:
                conn.execute(pragma)
            except Exception as e:
                logger.debug("PRAGMA skipped (%s): %s", pragma, e)

        self._local.conn = conn
        return conn

    def commit(self):
        """Commit the current transaction on this thread's connection."""
        with self._lock:
            conn = self.get_connection()
            try:
                conn.commit()
            except Exception as e:
                logger.debug("Commit failed or no open transaction: %s", e)

    def rollback(self):
        """Rollback the current transaction on this thread's connection."""
        with self._lock:
            conn = self.get_connection()
            try:
                conn.rollback()
            except Exception as e:
                logger.debug("Rollback failed or no open transaction: %s", e)

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
                    outbound_tag TEXT DEFAULT '',
                    is_ip INTEGER DEFAULT 0
                )
                """)

                # Check if is_ip column exists (migration for existing database)
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(access_logs)").fetchall()]
                if "is_ip" not in cols:
                    logger.info("Migrating access_logs table: adding is_ip column...")
                    conn.execute("ALTER TABLE access_logs ADD COLUMN is_ip INTEGER DEFAULT 0")
                    # Backfill existing data
                    conn.execute("""
                    UPDATE access_logs SET is_ip = 1
                    WHERE target_host GLOB '[0-9]*.[0-9]*.[0-9]*.[0-9]*' OR target_host GLOB '*:*'
                    """)
                    logger.info("is_ip migration and backfill completed.")

                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_date_time ON access_logs (date, timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_client_ip ON access_logs (client_ip, date)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_target_host ON access_logs (target_host)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_hour ON access_logs (hour)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_date_is_ip ON access_logs (date, is_ip)")

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
            except Exception:
                conn.rollback()
                raise

    def insert_logs_batch(self, logs: List[Any], commit: bool = True) -> int:
        """
        Batch insert parsed logs.
        Supports raw tuples (zero object allocation overhead) and dicts (backward-compatible).
        `commit=False` allows wrapping an entire file in a single transaction.
        """
        if not logs:
            return 0

        first = logs[0]
        if isinstance(first, tuple):
            if len(first) == 11:
                data = logs
            else:
                # 10-element tuple without is_ip: compute is_ip
                data = [
                    (
                        item[0], item[1], item[2], item[3], item[4],
                        item[5], item[6], item[7], item[8], item[9],
                        1 if (":" in item[7] or (item[7] and item[7][0].isdigit() and len(item[7].split(".")) == 4 and item[7].split(".")[-1].isdigit())) else 0
                    )
                    for item in logs
                ]
        else:
            # Dict support for backward compatibility
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
                    item.get("is_ip", 1 if (":" in item["target_host"] or (item["target_host"] and item["target_host"][0].isdigit() and len(item["target_host"].split(".")) == 4 and item["target_host"].split(".")[-1].isdigit())) else 0)
                )
                for item in logs
            ]

        with self._lock:
            conn = self.get_connection()
            try:
                conn.executemany("""
                INSERT INTO access_logs (
                    timestamp, date, hour, client_ip, client_port,
                    status, network, target_host, target_port, outbound_tag, is_ip
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, data)
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise
        return len(logs)

    def get_processed_file(self, filename: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM processed_files WHERE filename = ?", (filename,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    def update_processed_file(self, filename: str, offset: int, mtime: float, total_lines: int, commit: bool = True):
        with self._lock:
            conn = self.get_connection()
            try:
                conn.execute("""
                INSERT OR REPLACE INTO processed_files (filename, last_offset, last_mtime, total_lines, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (filename, offset, mtime, total_lines))
                if commit:
                    conn.commit()
            except Exception:
                conn.rollback()
                raise

    def clean_expired_logs(self, retention_days: int) -> int:
        """
        Delete access logs older than retention_days (e.g. 180 days / ~6 months).
        Returns the number of deleted rows.
        """
        if retention_days <= 0:
            return 0

        from datetime import datetime, timedelta
        cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")

        with self._lock:
            conn = self.get_connection()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM access_logs WHERE date < ?", (cutoff_date,)
                ).fetchone()
                count = row["count"] if row else 0

                if count > 0:
                    conn.execute("DELETE FROM access_logs WHERE date < ?", (cutoff_date,))
                    conn.commit()
                    try:
                        conn.execute("PRAGMA optimize")
                    except Exception:
                        pass
                    logger.info(
                        "Cleaned up %d expired logs older than %s (retention: %d days)",
                        count, cutoff_date, retention_days
                    )
                return count
            except Exception as e:
                conn.rollback()
                logger.error("Failed to clean expired logs: %s", e)
                return 0

    def get_overview_stats(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get KPI overview metrics and charts data.
        Optimized:
        - Merged summary + hourly + protocol + outbound into single scan
        - Fast is_ip integer comparison (eliminates expensive runtime GLOB patterns)
        """
        conn = self.get_connection()

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

        # === Merged query 1: summary + hourly + protocol + outbound in one scan ===
        merged_rows = conn.execute("""
        SELECT
            hour,
            network,
            COALESCE(NULLIF(outbound_tag, ''), 'direct') AS tag,
            COUNT(*) AS cnt
        FROM access_logs
        WHERE date = ?
        GROUP BY hour, network, tag
        """, (date,)).fetchall()

        total_requests = 0
        hourly = [0] * 24
        protocols: Dict[str, int] = {}
        outbounds: Dict[str, int] = {}

        for r in merged_rows:
            cnt = r["cnt"]
            total_requests += cnt
            hourly[r["hour"]] += cnt
            protocols[r["network"]] = protocols.get(r["network"], 0) + cnt
            outbounds[r["tag"]] = outbounds.get(r["tag"], 0) + cnt

        # Distinct counts with indexed date scan
        uniques = conn.execute("""
        SELECT
            COUNT(DISTINCT client_ip) AS unique_ips,
            COUNT(DISTINCT target_host) AS unique_domains
        FROM access_logs
        WHERE date = ?
        """, (date,)).fetchone()

        # === Query 2: Top 10 target domains (fast is_ip = 0, no GLOB pattern matching) ===
        top_domains = conn.execute("""
        SELECT target_host, COUNT(*) AS count
        FROM access_logs
        WHERE date = ? AND is_ip = 0
        GROUP BY target_host
        ORDER BY count DESC
        LIMIT 10
        """, (date,)).fetchall()

        # === Query 3: Top 10 target IPs (fast is_ip = 1, no GLOB pattern matching) ===
        top_target_ips = conn.execute("""
        SELECT target_host, COUNT(*) AS count
        FROM access_logs
        WHERE date = ? AND is_ip = 1
        GROUP BY target_host
        ORDER BY count DESC
        LIMIT 10
        """, (date,)).fetchall()

        # === Query 4: Top 10 active client IPs ===
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
            "total_requests": total_requests,
            "unique_ips": uniques["unique_ips"],
            "unique_domains": uniques["unique_domains"],
            "hourly_distribution": hourly,
            "protocol_distribution": protocols,
            "outbound_distribution": outbounds,
            "top_domains": [{"domain": r["target_host"], "count": r["count"]} for r in top_domains],
            "top_target_ips": [{"ip": r["target_host"], "count": r["count"]} for r in top_target_ips],
            "top_ips": [{"ip": r["client_ip"], "count": r["count"]} for r in top_ips],
        }

    def get_all_ips_stats(self, date: Optional[str] = None,
                          page: int = 1, page_size: int = 50) -> Tuple[List[Dict[str, Any]], int]:
        """
        List client IPs with counts and timestamps — paginated.
        Returns (rows, total_count).
        """
        conn = self.get_connection()

        if not date:
            row = conn.execute("SELECT MAX(date) AS max_date FROM access_logs").fetchone()
            date = row["max_date"] if row and row["max_date"] else ""

        # Total distinct IPs for pagination metadata
        total = conn.execute(
            "SELECT COUNT(DISTINCT client_ip) AS cnt FROM access_logs"
        ).fetchone()["cnt"]

        offset = (page - 1) * page_size
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
        LIMIT ? OFFSET ?
        """
        rows = conn.execute(query, (date, page_size, offset)).fetchall()
        return [dict(r) for r in rows], total

    def get_ip_detail(self, client_ip: str, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Deep-dive analytics for a single client IP.
        """
        conn = self.get_connection()

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
            params.append(client_ip.strip())
        if target_host:
            th = target_host.strip()
            if "%" in th or "*" in th:
                conditions.append("target_host LIKE ?")
                params.append(th.replace("*", "%"))
            else:
                conditions.append("target_host LIKE ?")
                params.append(f"%{th}%")
        if target_port:
            conditions.append("target_port = ?")
            params.append(target_port)
        if network:
            conditions.append("network = ?")
            params.append(network.lower().strip())

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        conn = self.get_connection()

        # Count total: O(1) estimate on empty filter vs filtered COUNT(*)
        if not conditions:
            row = conn.execute("SELECT MAX(id) AS total FROM access_logs").fetchone()
            total = row["total"] or 0
        else:
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
