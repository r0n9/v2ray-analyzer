import glob
import logging
import os
from typing import Dict, Any, List
from .database import Database
from .parser import LogParser

logger = logging.getLogger("v2ray_analyzer.collector")


class LogCollector:
    def __init__(self, db: Database, log_dir: str, access_log_path: str):
        self.db = db
        self.log_dir = os.path.abspath(log_dir)
        self.access_log_path = os.path.abspath(access_log_path)
        self.batch_size = 5000

    def sync_all(self) -> Dict[str, Any]:
        """
        Run both historical files sync and access.log tailing.
        """
        imported_history = self.import_historical_logs()
        new_access_lines = self.sync_access_log()
        return {
            "history_lines_imported": imported_history,
            "access_lines_imported": new_access_lines,
        }

    def import_historical_logs(self) -> int:
        """
        Scan directory for historical logs like 2026_11_03.log and import unread ones.
        Optimized:
        - Entire file imported inside a single transaction (reduces fsync count from N to 1)
        - Float mtime comparison with tolerance (avoids spurious re-imports)
        - Direct raw tuple passing (avoids allocating dictionary per line)
        """
        total_imported = 0
        pattern = os.path.join(self.log_dir, "*.log")
        files = sorted(glob.glob(pattern))

        for file_path in files:
            basename = os.path.basename(file_path)
            # Skip access.log and error.log in historical sweep
            if basename in ("access.log", "error.log"):
                continue

            try:
                stat = os.stat(file_path)
            except OSError:
                continue

            record = self.db.get_processed_file(basename)
            # If already processed and mtime matches (with tolerance) and offset >= size, skip
            if record:
                mtime_diff = abs(record.get("last_mtime", 0) - stat.st_mtime)
                last_offset = record.get("last_offset", 0)
                if (mtime_diff < 0.01 or last_offset >= stat.st_size) and last_offset >= stat.st_size:
                    continue

            logger.info(f"Importing historical log: {basename}...")
            lines_in_file = 0
            batch: List[tuple] = []

            start_offset = record.get("last_offset", 0) if record else 0
            if start_offset > stat.st_size:
                start_offset = 0

            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    if start_offset > 0:
                        f.seek(start_offset)

                    for line in f:
                        parsed = LogParser.parse_line(line)
                        if parsed:
                            batch.append(parsed)
                            lines_in_file += 1

                        if len(batch) >= self.batch_size:
                            self.db.insert_logs_batch(batch, commit=False)
                            batch.clear()

                    if batch:
                        self.db.insert_logs_batch(batch, commit=False)
                        batch.clear()

                    current_offset = f.tell()

                # Atomically update processed_files record and commit in the same transaction
                self.db.update_processed_file(
                    filename=basename,
                    offset=current_offset,
                    mtime=stat.st_mtime,
                    total_lines=(record.get("total_lines", 0) if record else 0) + lines_in_file,
                    commit=False,
                )
                self.db.commit()

                total_imported += lines_in_file
                logger.info(f"Finished {basename}: {lines_in_file} new lines imported.")
            except Exception as e:
                self.db.rollback()
                logger.error(f"Error importing {basename}: {e}")
                continue

        return total_imported

    def sync_access_log(self) -> int:
        """
        Tails the live access.log file incrementally.
        Optimized:
        - Commits all batched lines and offset update in a single transaction
        - Direct raw tuple passing (avoids per-line dict instantiation)
        """
        if not os.path.exists(self.access_log_path):
            return 0

        try:
            stat = os.stat(self.access_log_path)
        except OSError:
            return 0

        basename = "access.log"
        record = self.db.get_processed_file(basename)
        last_offset = record.get("last_offset", 0) if record else 0

        # Truncation check (log rotation or manual > access.log)
        if stat.st_size < last_offset:
            logger.info("access.log was truncated or rotated, resetting offset to 0.")
            last_offset = 0

        if stat.st_size == last_offset:
            return 0  # No new data

        new_lines = 0
        batch: List[tuple] = []

        try:
            with open(self.access_log_path, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(last_offset)
                for line in f:
                    parsed = LogParser.parse_line(line)
                    if parsed:
                        batch.append(parsed)
                        new_lines += 1

                    if len(batch) >= self.batch_size:
                        self.db.insert_logs_batch(batch, commit=False)
                        batch.clear()

                if batch:
                    self.db.insert_logs_batch(batch, commit=False)
                    batch.clear()

                current_offset = f.tell()

            # Atomically update processed_files record and commit in the same transaction
            self.db.update_processed_file(
                filename=basename,
                offset=current_offset,
                mtime=stat.st_mtime,
                total_lines=(record.get("total_lines", 0) if record else 0) + new_lines,
                commit=False,
            )
            self.db.commit()

            if new_lines > 0:
                logger.info(f"Incremental sync: {new_lines} lines ingested from access.log.")

            return new_lines
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error during access.log sync: {e}")
            return 0
