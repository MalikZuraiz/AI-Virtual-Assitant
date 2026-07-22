"""System specs, storage, and battery reporting."""
from __future__ import annotations

import platform

import psutil


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
