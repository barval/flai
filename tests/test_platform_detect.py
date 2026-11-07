"""Tests for app/platform_detect.py — vendor-agnostic GPU detection."""

from unittest.mock import MagicMock, patch

from app.platform_detect import (
    GpuInfo,
    detect_platform,
    get_platform_info,
    query_free_vram_mb,
    query_vram,
)


class TestCudaVersion:
    """The CUDA version comes from the nvidia-smi header, because --query-gpu has no such field."""

    HEADER = "| NVIDIA-SMI 595.71.05   Driver Version: 595.71.05   CUDA Version: 13.2 |\n"
    QUERY = "NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n"

    def test_reads_the_header_and_caches_it(self):
        query = MagicMock(returncode=0, stdout=self.QUERY)
        header = MagicMock(returncode=0, stdout=self.HEADER)
        with (
            patch("app.platform_detect._cuda_version_cache", ""),
            patch("app.platform_detect.subprocess.run", side_effect=[query, header, query]) as run,
        ):
            first = get_platform_info("nvidia")
            second = get_platform_info("nvidia")
        assert first.cuda_version == "13.2"
        assert second.cuda_version == "13.2"
        assert run.call_count == 3, "the header probe runs once, then the answer is cached"

    def test_empty_when_the_header_carries_no_version(self):
        query = MagicMock(returncode=0, stdout=self.QUERY)
        header = MagicMock(returncode=1, stdout="", stderr="no driver")
        with (
            patch("app.platform_detect._cuda_version_cache", ""),
            patch("app.platform_detect.subprocess.run", side_effect=[query, header]),
        ):
            info = get_platform_info("nvidia")
        assert info.cuda_version == ""

    def test_non_nvidia_platforms_have_no_cuda_version(self):
        assert GpuInfo(platform="cpu").cuda_version == ""
        assert GpuInfo(platform="amd", gpu_name="Radeon").cuda_version == ""


class TestProbeNvidia:
    def test_success(self):
        with patch(
            "app.platform_detect.subprocess.run",
            return_value=MagicMock(
                returncode=0,
                stdout="NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n",
            ),
        ):
            info = get_platform_info("nvidia")
        assert info.platform == "nvidia"
        assert info.gpu_name == "NVIDIA GeForce RTX 4090"
        assert info.total_vram_mb == 24564
        assert info.used_vram_mb == 2048
        assert info.available_vram_mb == 22516
        assert info.cuda_detected is True

    def test_no_gpu(self):
        with patch(
            "app.platform_detect.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="no GPU"),
        ):
            assert get_platform_info("nvidia") is not None

    def test_file_not_found(self):
        with patch("app.platform_detect.subprocess.run", side_effect=FileNotFoundError):
            assert get_platform_info("nvidia") is not None


class TestProbeAmd:
    def test_success_with_json(self):
        fake_json = '{"card0": {"VRAM Total Memory (B)": "17526401024", "VRAM Used Memory (B)": "1097728", "VRAM Free Memory (B)": "17525303296"}}'
        with patch(
            "app.platform_detect.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=fake_json),
        ):
            info = get_platform_info("amd")
        assert info.platform == "amd"
        # Values below 1 GB are treated as plain MB by the heuristic converter;
        # parity and rounding for exact byte/GB values is refined with the
        # real ROCm backend in a later phase.
        assert info.total_vram_mb == 17526401024 // 1024**2
        assert info.available_vram_mb == 17525303296 // 1024**2
        assert info.cuda_detected is False

    def test_unavailable(self):
        with patch("app.platform_detect.subprocess.run", side_effect=FileNotFoundError):
            assert detect_platform() != "amd"


class TestProbeIntel:
    def test_success(self):
        fake = "GPU0:\n  deviceName    = Intel(R) Arc(TM) A770 Graphics\n  deviceType    = DISCRETE_GPU\n"
        with patch(
            "app.platform_detect.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=fake),
        ):
            info = get_platform_info("intel")
        assert info.platform == "intel"
        assert "A770" in info.gpu_name
        assert info.cuda_detected is False

    def test_llvmpipe_rejected(self):
        fake = "GPU0:\n  deviceName    = llvmpipe\n  deviceType    = CPU\n"
        with patch(
            "app.platform_detect.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=fake),
        ):
            assert get_platform_info("intel") is not None
        assert detect_platform() != "intel"


class TestDetectPlatform:
    def test_env_override(self):
        with (
            patch.dict("os.environ", {"FLAI_PLATFORM": "cpu"}, clear=False),
            patch("app.platform_detect._probe_nvidia", return_value=GpuInfo(platform="nvidia")),
        ):
            assert detect_platform() == "cpu"

    def test_invalid_env_falls_through(self):
        with (
            patch.dict("os.environ", {"FLAI_PLATFORM": "banana"}, clear=False),
            patch("app.platform_detect._probe_nvidia", return_value=None),
            patch("app.platform_detect._probe_amd", return_value=None),
            patch("app.platform_detect._probe_intel", return_value=None),
        ):
            assert detect_platform() == "cpu"

    def test_detects_nvidia(self):
        with (
            patch("app.platform_detect._probe_nvidia", return_value=GpuInfo(platform="nvidia")),
            patch("app.platform_detect._probe_amd", return_value=None),
            patch("app.platform_detect._probe_intel", return_value=None),
        ):
            assert detect_platform() == "nvidia"


class TestQuery:
    def test_query_free_cpu_returns_none(self):
        with patch("app.platform_detect._probe_cpu", return_value=GpuInfo(platform="cpu")):
            assert query_free_vram_mb("cpu") is None

    def test_query_free_nvidia(self):
        with patch(
            "app.platform_detect._probe_nvidia",
            return_value=GpuInfo(platform="nvidia", total_vram_mb=16384, available_vram_mb=12000),
        ):
            assert query_free_vram_mb("nvidia") == 12000

    def test_query_vram(self):
        with patch(
            "app.platform_detect._probe_nvidia",
            return_value=GpuInfo(platform="nvidia", total_vram_mb=16384, used_vram_mb=3000),
        ):
            assert query_vram("nvidia") == (3000, 16384)

    def test_query_vram_cpu_none(self):
        with patch("app.platform_detect._probe_cpu", return_value=GpuInfo(platform="cpu")):
            assert query_vram("cpu") == (None, None)


class TestAccelerator:
    """A GPU is not necessarily CUDA: the two flags must stay distinct."""

    def test_nvidia_is_cuda(self):
        query = MagicMock(returncode=0, stdout="RTX 4090, 24564, 2048, 22516\n")
        header = MagicMock(returncode=0, stdout="")
        with (
            patch("app.platform_detect._cuda_version_cache", ""),
            patch("app.platform_detect.subprocess.run", side_effect=[query, header]),
        ):
            info = get_platform_info("nvidia")
        assert info.accelerator == "cuda"
        assert info.cuda_detected is True

    def test_cpu_has_no_accelerator(self):
        assert GpuInfo(platform="cpu").accelerator == "none"
        assert GpuInfo(platform="cpu").cuda_detected is False

    def test_amd_and_intel_are_vulkan_without_cuda(self):
        assert GpuInfo(platform="amd", accelerator="vulkan").cuda_detected is False
        assert GpuInfo(platform="intel", accelerator="vulkan").cuda_detected is False


class TestAmdSysfs:
    """Consumer AMD cards have no ROCm userspace; the kernel still reports VRAM."""

    def _card(self, tmp_path, total_bytes, used_bytes, name="Radeon RX 7800 XT"):
        device = tmp_path / "card0" / "device"
        device.mkdir(parents=True)
        (device / "mem_info_vram_total").write_text(str(total_bytes))
        (device / "mem_info_vram_used").write_text(str(used_bytes))
        (device / "product_name").write_text(name + "\n")
        return str(tmp_path)

    def test_reads_total_and_used(self, tmp_path):
        from app.platform_detect import _amd_sysfs_vram

        root = self._card(tmp_path, 16 * 1024**3, 2 * 1024**3)
        assert _amd_sysfs_vram(root) == (16384, 2048, "Radeon RX 7800 XT")

    def test_no_amd_card_is_empty(self, tmp_path):
        from app.platform_detect import _amd_sysfs_vram

        assert _amd_sysfs_vram(str(tmp_path)) == (0, 0, "")

    def test_probe_falls_back_to_sysfs_without_rocm(self):
        """No rocm-smi binary: the kernel's own numbers are used instead."""
        with (
            patch("app.platform_detect.subprocess.run", side_effect=FileNotFoundError),
            patch("app.platform_detect._amd_sysfs_vram", return_value=(8192, 1024, "Radeon RX 7800 XT")),
        ):
            info = get_platform_info("amd")
        assert info.platform == "amd"
        assert info.total_vram_mb == 8192
        assert info.available_vram_mb == 7168
        assert info.accelerator == "vulkan"


class TestIntelVulkanMemory:
    VULKANINFO = """
VkPhysicalDeviceMemoryProperties:
=================================
memoryHeaps: count = 2
        memoryHeaps[0]:
                size   = 17179869184 (0x400000000)
                budget = 12884901888 (0x300000000)
                usage  = 0 (0x00000000)
                flags: count = 1
                        MEMORY_HEAP_DEVICE_LOCAL_BIT
        memoryHeaps[1]:
                size   = 16777216 (0x1000000)
                budget = 16777216 (0x1000000)
"""

    def test_the_largest_heap_is_the_card(self):
        from app.platform_detect import _intel_vulkan_memory

        with patch("app.platform_detect.subprocess.run", return_value=MagicMock(returncode=0, stdout=self.VULKANINFO)):
            assert _intel_vulkan_memory() == (16384, 4096)

    def test_unreadable_report_is_zero(self):
        from app.platform_detect import _intel_vulkan_memory

        with patch("app.platform_detect.subprocess.run", side_effect=FileNotFoundError):
            assert _intel_vulkan_memory() == (0, 0)
