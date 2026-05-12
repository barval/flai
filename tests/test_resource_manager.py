"""Tests for Resource Manager — GPU/CPU/RAM adaptive management."""
import threading
from unittest.mock import MagicMock, mock_open, patch

from app.resource_manager import HardwareInfo, ResourceManager, get_resource_manager


class TestHardwareInfo:
    def test_defaults(self):
        hw = HardwareInfo()
        assert hw.total_vram_mb == 0
        assert hw.gpu_name == 'unknown'
        assert hw.cuda_detected is False
        assert hw.cpu_count == 0


class TestDetectHardware:
    MEMINFO = (
        'MemTotal:       16384000 kB\n'
        'MemFree:         4096000 kB\n'
        'MemAvailable:    8192000 kB\n'
    )

    def _rm_teardown(self, rm):
        if hasattr(rm, 'hardware'):
            rm.hardware = HardwareInfo()

    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open, read_data=MEMINFO)
    @patch('subprocess.run')
    def test_detect_hardware_no_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='nvidia-smi not found')
        rm = ResourceManager()
        hw = rm.detect_hardware()

        assert hw.cuda_detected is False
        assert hw.gpu_name == 'unknown'
        assert hw.total_vram_mb == 0
        assert hw.total_ram_mb == 16000
        assert hw.available_ram_mb == 8000
        assert hw.cpu_count == 8

    @patch('os.cpu_count', return_value=16)
    @patch('builtins.open', new_callable=mock_open, read_data=MEMINFO)
    @patch('subprocess.run')
    def test_detect_hardware_with_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n',
        )

        rm = ResourceManager()
        hw = rm.detect_hardware()

        assert hw.cuda_detected is True
        assert 'RTX 4090' in hw.gpu_name
        assert hw.total_vram_mb == 24564
        assert hw.available_vram_mb == 22516
        assert hw.cpu_count == 16

    @patch('os.cpu_count', return_value=4)
    @patch('builtins.open', new_callable=mock_open, read_data=MEMINFO)
    @patch('subprocess.run')
    def test_detect_hardware_gpu_empty_output(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(returncode=0, stdout='')
        rm = ResourceManager()
        hw = rm.detect_hardware()
        assert hw.cuda_detected is False

    @patch('os.cpu_count', return_value=4)
    @patch('builtins.open', new_callable=mock_open, read_data=MEMINFO)
    @patch('subprocess.run', side_effect=FileNotFoundError)
    def test_detect_hardware_nvidia_smi_not_found(self, mock_run, mock_file, mock_cpu):
        rm = ResourceManager()
        hw = rm.detect_hardware()
        assert hw.cuda_detected is False
        assert hw.gpu_name == 'unknown'


class TestComputeConfig:
    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n')
    @patch('subprocess.run')
    def test_cpu_only_mode(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(returncode=1, stdout='', stderr='nvidia-smi not found')
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config('chat')

        assert cfg['n_gpu_layers'] == 0
        assert cfg['warning'] is not None
        assert 'CPU-only' in cfg['warning']

    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n')
    @patch('subprocess.run')
    def test_24gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n',
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config('chat')

        assert cfg['n_gpu_layers'] == -1
        assert cfg['flash_attn'] is True
        assert cfg['cache_capacity'] == 8192

    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n')
    @patch('subprocess.run')
    def test_16gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce RTX 4060 Ti, 16384, 2048, 14336\n',
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config('chat')

        assert cfg['offload_kqv'] is True or cfg['n_gpu_layers'] == -1

    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n')
    @patch('subprocess.run')
    def test_8gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce RTX 3050, 8192, 2048, 6144\n',
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config('chat')

        assert cfg['offload_kqv'] is True
        assert cfg['ctx_size'] == 4096
        assert cfg['cache_capacity'] == 1024
        assert 'Limited VRAM' in (cfg['warning'] or '')

    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n')
    @patch('subprocess.run')
    def test_less_than_8gb_gpu(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce GTX 1050, 4096, 2048, 2048\n',
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config('chat')

        assert cfg['n_gpu_layers'] == 0
        assert 'Very limited' in (cfg['warning'] or '')

    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       32768000 kB\nMemAvailable:   16384000 kB\n')
    @patch('subprocess.run')
    def test_reasoning_model_large_vram(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n',
        )
        rm = ResourceManager()
        rm.detect_hardware()
        cfg = rm.compute_llamacpp_config('reasoning')

        assert cfg['n_gpu_layers'] == -1
        assert cfg['flash_attn'] is True


class TestGpuGating:
    def test_initial_not_busy(self):
        rm = ResourceManager()
        assert rm._sd_busy is False

    def test_mark_sd_busy(self):
        rm = ResourceManager()
        rm.mark_sd_busy()
        assert rm._sd_busy is True
        assert rm._sd_busy_since > 0

    def test_mark_sd_idle(self):
        rm = ResourceManager()
        rm.mark_sd_busy()
        rm.mark_sd_idle()
        assert rm._sd_busy is False

    def test_thread_safety(self):
        rm = ResourceManager()

        def toggle():
            for _ in range(100):
                rm.mark_sd_busy()
                rm.mark_sd_idle()

        threads = [threading.Thread(target=toggle) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert rm._sd_busy is False


class TestUnloadModel:
    @patch('requests.post')
    @patch('app.resource_manager.os.getenv')
    def test_unload_llama_swap(self, mock_getenv, mock_post):
        mock_getenv.return_value = 'llama-swap'
        mock_post.return_value = MagicMock(status_code=200)
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is True

    @patch('requests.post')
    @patch('requests.get')
    @patch('app.resource_manager.os.getenv')
    def test_unload_direct_no_model_loaded(self, mock_getenv, mock_get, mock_post):
        mock_getenv.return_value = 'llamacpp'
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {'data': []})
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is True

    @patch('requests.post')
    @patch('requests.get')
    @patch('app.resource_manager.os.getenv')
    def test_unload_direct_with_model(self, mock_getenv, mock_get, mock_post):
        mock_getenv.return_value = 'llamacpp'
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'data': [{'id': 'test-model', 'status': {'value': 'loaded'}}]},
        )
        mock_post.return_value = MagicMock(status_code=200)
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is True
        assert mock_post.called

    @patch('requests.post')
    @patch('requests.get')
    @patch('app.resource_manager.os.getenv')
    def test_unload_direct_failure(self, mock_getenv, mock_get, mock_post):
        mock_getenv.return_value = 'llamacpp'
        mock_get.side_effect = Exception('Connection refused')
        rm = ResourceManager()
        result = rm.unload_llamacpp_model()
        assert result is False


class TestGetStatus:
    @patch('os.cpu_count', return_value=8)
    @patch('builtins.open', new_callable=mock_open,
           read_data='MemTotal:       16384000 kB\nMemAvailable:   8192000 kB\n')
    @patch('subprocess.run')
    def test_get_status(self, mock_run, mock_file, mock_cpu):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='NVIDIA GeForce RTX 4090, 24564, 2048, 22516\n',
        )
        rm = ResourceManager()
        rm.detect_hardware()
        status = rm.get_status()

        assert status['gpu_name'] == 'NVIDIA GeForce RTX 4090'
        assert status['cuda_detected'] is True
        assert status['total_vram_mb'] == 24564
        assert status['total_ram_mb'] == 16000
        assert status['cpu_count'] == 8
        assert status['sd_busy'] is False


class TestSingleton:
    def test_get_resource_manager_returns_same_instance(self):
        rm1 = get_resource_manager()
        rm2 = get_resource_manager()
        assert rm1 is rm2
