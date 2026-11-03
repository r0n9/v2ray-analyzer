import re
from typing import Optional, Dict, Any

class LogParser:
    """
    Parser for V2Ray access log entries.
    Standard format:
    2026/11/03 15:36:47 116.237.189.87:0 accepted tcp:fa000000110.resources.office.net:443 [direct]
    or split logs format:
    2026/11/03 00:00:01 116.237.189.87:0 accepted tcp:ipv4.myexternalip.com:80
    """

    @staticmethod
    def parse_line(line: str) -> Optional[Dict[str, Any]]:
        line = line.strip()
        if not line or line.startswith("#"):
            return None

        # Check for error.log lines like: 2024/10/17 09:08:11 [Warning] V2Ray 5.16.1 started
        if "[Warning]" in line or "[Info]" in line or "[Error]" in line:
            return None

        parts = line.split()
        if len(parts) < 5:
            return None

        date_raw = parts[0]  # 2026/11/03
        time_raw = parts[1]  # 15:36:47
        client_raw = parts[2]  # 116.237.189.87:0
        status = parts[3].lower()  # accepted / rejected
        target_raw = parts[4]  # tcp:example.com:443 or udp:[2403:...]:443

        # Parse date and hour
        # Normalise date from 2026/11/03 to 2026-11-03
        date_str = date_raw.replace("/", "-")
        timestamp_str = f"{date_str} {time_raw}"

        try:
            hour = int(time_raw.split(":")[0])
        except (ValueError, IndexError):
            hour = 0

        # Parse client IP and port
        if ":" in client_raw:
            client_ip, client_port_str = client_raw.rsplit(":", 1)
            try:
                client_port = int(client_port_str)
            except ValueError:
                client_port = 0
        else:
            client_ip = client_raw
            client_port = 0

        client_ip = client_ip.strip("[]")

        # Parse target: protocol:host:port
        # Note: protocol is before first ':', port is after last ':'
        if ":" in target_raw:
            proto_split = target_raw.split(":", 1)
            protocol = proto_split[0].lower()
            rest = proto_split[1]

            if ":" in rest:
                host_str, port_str = rest.rsplit(":", 1)
                try:
                    target_port = int(port_str)
                except ValueError:
                    target_port = 0
                target_host = host_str.strip("[]")
            else:
                target_host = rest.strip("[]")
                target_port = 0
        else:
            protocol = "unknown"
            target_host = target_raw.strip("[]")
            target_port = 0

        # Parse optional outbound tag and comments
        outbound_tag = ""
        if len(parts) > 5:
            for extra in parts[5:]:
                if extra.startswith("[") and extra.endswith("]"):
                    outbound_tag = extra.strip("[]")
                    break

        return {
            "timestamp": timestamp_str,
            "date": date_str,
            "hour": hour,
            "client_ip": client_ip,
            "client_port": client_port,
            "status": status,
            "network": protocol,
            "target_host": target_host,
            "target_port": target_port,
            "outbound_tag": outbound_tag,
        }
