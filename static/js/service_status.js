// static/js/service_status.js

class ServiceStatusMonitor {
    constructor() {
        this.updateInterval = 5000; // 5 секунд
        this.init();
    }
    
    init() {
        this.updateStatus();
        setInterval(() => this.updateStatus(), this.updateInterval);
    }
    
    async updateStatus() {
        try {
            const response = await fetch('/api/services/status');
            const data = await response.json();
            
            this.updateOllamaStatus(data.ollama);
            this.updateAutomatic1111Status(data.automatic1111);
            this.updateCameraStatus(data.camera_api);
            
            // Получаем системную информацию
            const sysResponse = await fetch('/api/system/info');
            const sysData = await sysResponse.json();
            this.updateSystemInfo(sysData);
            
        } catch (error) {
            console.error('Error updating service status:', error);
        }
    }
    
    updateOllamaStatus(status) {
        const card = document.getElementById('ollama-status');
        if (!card) return;
        
        // Обновляем класс карточки
        card.className = `service-card ${status.health}`;
        
        // Локация
        const locationEl = document.getElementById('ollama-location');
        if (locationEl) {
            locationEl.textContent = this.getLocationText(status.location);
            locationEl.className = `service-location ${status.location}`;
        }
        
        // Режим работы в заголовке
        const modeHeaderEl = document.getElementById('ollama-mode-header');
        if (modeHeaderEl) {
            modeHeaderEl.textContent = this.getModeIcon(status.mode);
            modeHeaderEl.className = `service-mode ${this.getModeClass(status.mode)}`;
        }
        
        // Индикатор здоровья
        const healthEl = document.getElementById('ollama-health');
        if (healthEl) {
            healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
        }
        
        // Память
        this.updateMemoryIndicator('ollama', status.memory);
        
        // Показываем/скрываем детали
        const details = document.getElementById('ollama-details');
        if (details) {
            details.style.opacity = status.health === 'down' ? '0.5' : '1';
        }
    }
    
    updateAutomatic1111Status(status) {
        const card = document.getElementById('automatic1111-status');
        if (!card) return;
        
        card.className = `service-card ${status.health}`;
        
        const locationEl = document.getElementById('automatic1111-location');
        if (locationEl) {
            locationEl.textContent = this.getLocationText(status.location);
            locationEl.className = `service-location ${status.location}`;
        }
        
        // Режим работы в заголовке
        const modeHeaderEl = document.getElementById('automatic1111-mode-header');
        if (modeHeaderEl) {
            modeHeaderEl.textContent = this.getModeIcon(status.mode);
            modeHeaderEl.className = `service-mode ${this.getModeClass(status.mode)}`;
        }
        
        const healthEl = document.getElementById('automatic1111-health');
        if (healthEl) {
            healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
        }
        
        // Загрузка
        const loadRow = document.getElementById('automatic1111-load-row');
        const loadEl = document.getElementById('automatic1111-load');
        
        if (loadRow && loadEl) {
            if (status.load > 0) {
                loadRow.style.display = 'flex';
                loadEl.textContent = `${status.load.toFixed(0)}%`;
            } else {
                loadRow.style.display = 'none';
            }
        }
        
        this.updateMemoryIndicator('automatic1111', status.memory);
        
        const details = document.getElementById('automatic1111-details');
        if (details) {
            details.style.opacity = status.health === 'down' ? '0.5' : '1';
        }
    }
    
    updateCameraStatus(status) {
        const card = document.getElementById('camera-status');
        if (!card) return;
        
        card.className = `service-card ${status.health}`;
        
        const locationEl = document.getElementById('camera-location');
        if (locationEl) {
            locationEl.textContent = this.getLocationText(status.location);
            locationEl.className = `service-location ${status.location}`;
        }
        
        const healthEl = document.getElementById('camera-health');
        if (healthEl) {
            healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
        }
        
        // Комнаты
        const roomsEl = document.getElementById('camera-rooms');
        if (roomsEl) {
            if (status.details && status.details.rooms) {
                roomsEl.textContent = status.details.rooms.join(', ');
            } else {
                roomsEl.textContent = 'Нет данных';
            }
        }
    }
    
    updateSystemInfo(data) {
        // GPU
        const gpuEl = document.getElementById('system-gpu');
        if (gpuEl) {
            let gpuText = 'Нет GPU';
            if (data.gpu.available) {
                gpuText = `${data.gpu.type} (${data.gpu.count}x)`;
                if (data.gpu.memory_mb && data.gpu.memory_mb.length > 0) {
                    const totalGB = data.gpu.memory_mb[0] / 1024;
                    gpuText += ` ${totalGB.toFixed(1)}GB`;
                }
            }
            gpuEl.textContent = gpuText;
        }
        
        // CPU
        const cpuEl = document.getElementById('system-cpu');
        if (cpuEl) {
            cpuEl.textContent = `Загрузка: ${data.system.cpu_load.toFixed(0)}%`;
        }
        
        // RAM
        const ramEl = document.getElementById('system-ram');
        if (ramEl) {
            ramEl.textContent = `${data.system.ram_available_gb.toFixed(1)}GB свободно`;
        }
    }
    
    updateMemoryIndicator(service, memory) {
        const bar = document.getElementById(`${service}-memory-bar`);
        const text = document.getElementById(`${service}-memory-text`);
        
        if (!bar || !text) return;
        
        if (memory.total_gb > 0) {
            bar.style.width = `${memory.used_percent}%`;
            
            // Определяем класс для цвета
            if (memory.used_percent > memory.critical_threshold) {
                bar.className = 'memory-bar critical';
            } else if (memory.used_percent > memory.warning_threshold) {
                bar.className = 'memory-bar warning';
            } else {
                bar.className = 'memory-bar normal';
            }
            
            text.textContent = `${memory.used_gb}GB / ${memory.total_gb}GB (${memory.used_percent}%)`;
        } else {
            bar.style.width = '0%';
            text.textContent = 'Нет данных о памяти';
        }
    }
    
    getLocationText(location) {
        const texts = {
            'local': '🏠 Локальный',
            'remote': '🌐 Удалённый',
            'unknown': '❓ Неизвестно'
        };
        return texts[location] || location;
    }
    
    getHealthText(health) {
        const texts = {
            'healthy': 'Работает',
            'degraded': 'Проблемы',
            'down': 'Недоступен',
            'unknown': 'Статус неизвестен'
        };
        return texts[health] || health;
    }
    
    getModeIcon(mode) {
        const icons = {
            'gpu_only': '⚡ GPU',
            'cpu_only': '🐢 CPU',
            'hybrid': '🔄 Гибрид',
            'unknown': '❓'
        };
        return icons[mode] || mode;
    }
    
    getModeClass(mode) {
        const classes = {
            'gpu_only': 'gpu',
            'cpu_only': 'cpu',
            'hybrid': 'hybrid',
            'unknown': ''
        };
        return classes[mode] || '';
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    window.serviceMonitor = new ServiceStatusMonitor();
});