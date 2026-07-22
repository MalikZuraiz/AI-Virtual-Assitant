"""System specs, storage, battery, network, and process reporting."""
from __future__ import annotations

import logging
import platform
import subprocess
from datetime import datetime

import psutil

logger = logging.getLogger("assistant.system_info")


class SystemInfo:
    def specs(self) -> str:
        lines = [
            f"Computer name: {platform.node()}",
            f"OS: {platform.system()} {platform.release()} ({platform.version()})",
            f"Machine: {platform.machine()}",
            f"Processor: {platform.processor() or 'unknown'}",
            f"Physical cores: {psutil.cpu_count(logical=False)}",
            f"Logical cores: {psutil.cpu_count(logical=True)}",
            f"CPU usage: {psutil.cpu_percent(interval=0.3)}%",
            f"RAM used: {psutil.virtual_memory().percent}% of "
            f"{psutil.virtual_memory().total / (1024 ** 3):.1f} GB",
        ]
        battery = psutil.sensors_battery()
        if battery:
            state = "charging" if battery.power_plugged else "on battery"
            lines.append(f"Battery: {battery.percent:.0f}% ({state})")
        return "\n".join(lines)

    def storage(self) -> str:
        lines = []
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (PermissionError, OSError):
                continue
            lines.append(
                f"{partition.device} ({partition.fstype}): "
                f"{usage.used / (1024 ** 3):.1f} / {usage.total / (1024 ** 3):.1f} GB used "
                f"({usage.percent}%)"
            )
        return "\n".join(lines) if lines else "No accessible drives found."

    def uptime(self) -> str:
        boot = datetime.fromtimestamp(psutil.boot_time())
        delta = datetime.now() - boot
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f"System has been up for {hours}h {minutes}m (since {boot:%Y-%m-%d %H:%M})."

    def network_info(self) -> str:
        lines = []
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for name, snics in addrs.items():
            if name not in stats or not stats[name].isup:
                continue
            for snic in snics:
                if snic.family.name == "AF_INET":
                    lines.append(f"{name}: {snic.address}")
        return "\n".join(lines) if lines else "No active network interfaces found."

    def top_processes(self, n: int = 5) -> str:
        procs = []
        for proc in psutil.process_iter(["name", "memory_percent"]):
            try:
                procs.append(proc.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda p: p.get("memory_percent") or 0, reverse=True)
        lines = [f"Top {n} processes by memory:"]
        for info in procs[:n]:
            lines.append(f"  {info['name']}: {info['memory_percent']:.1f}% mem")
        return "\n".join(lines)

    def gpu_info(self) -> str:
        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
                ],
                capture_output=True, text=True, timeout=10,
            )
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return "GPU(s): " + ", ".join(names) if names else "Couldn't detect a GPU."
        except Exception as exc:
            logger.warning("GPU info lookup failed", exc_info=True)
            return f"Couldn't detect a GPU: {exc}"
