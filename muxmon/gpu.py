"""GPU utilization monitor — NVML via nvidia-ml-py."""

from __future__ import annotations

import random
from argparse import ArgumentParser, Namespace

from muxmon import register
from muxmon.base import BaseMonitor


@register
class GpuMonitor(BaseMonitor):
    name = "gpu"
    default_title = "GPU"

    def add_args(self, parser: ArgumentParser) -> None:
        parser.add_argument("--gpu-index", type=int, default=0,
                            help="Which GPU to monitor (default: 0)")
        parser.add_argument("--mock", action="store_true",
                            help="Use random test data (no GPU needed)")
        parser.add_argument("--show-temp", action="store_true",
                            help="Add GPU temperature as a third series")

    def setup(self, args: Namespace) -> None:
        self._mock = args.mock
        self._gpu_index = args.gpu_index
        self._show_temp = args.show_temp

        self.add_series("gpu", color="cyan", label_fmt="GPU {}", unit_mode="percent")
        self.add_series("mem", color="magenta", label_fmt="Mem {}", unit_mode="percent")
        if self._show_temp:
            self.add_series("temp", color="red", label_fmt="{}°C", unit_mode="fixed")

        if not self._mock:
            import pynvml
            self._pynvml = pynvml
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self._gpu_index)
        else:
            self._pynvml = None
            self._handle = None

        self.title = f"GPU {self._gpu_index}"

    def sample(self) -> dict[str, float]:
        if self._mock:
            result = {"gpu": random.uniform(20, 80), "mem": random.uniform(10, 50)}
            if self._show_temp:
                result["temp"] = random.uniform(35, 75)
            return result

        rates = self._pynvml.nvmlDeviceGetUtilizationRates(self._handle)
        mem_info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        result = {
            "gpu": float(rates.gpu),
            "mem": (mem_info.used / mem_info.total) * 100,
        }
        if self._show_temp:
            # NVML_TEMPERATURE_GPU = 0
            result["temp"] = float(self._pynvml.nvmlDeviceGetTemperature(self._handle, 0))
        return result

    def cleanup(self) -> None:
        if self._pynvml:
            self._pynvml.nvmlShutdown()

    @classmethod
    def is_available(cls) -> bool:
        try:
            import pynvml
            pynvml.nvmlInit()
            pynvml.nvmlShutdown()
            return True
        except Exception:
            return False


if __name__ == "__main__":
    GpuMonitor().run()
