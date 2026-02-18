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
        locationEl.textContent = this.getLocationText(status.location);
        locationEl.className = `service-location ${status.location}`;
        
        // Индикатор здоровья
        const healthEl = document.getElementById('ollama-health');
        healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
        
        // Режим работы
        document.getElementById('ollama-mode').textContent = this.getModeText(status.mode);
        
        // URL
        document.getElementById('ollama-url').textContent = status.url || 'Не настроен';
        
        // Модель
        document.getElementById('ollama-model').textContent = status.current_model || 'Нет активной модели';
        
        // Память
        this.updateMemoryIndicator('ollama', status.memory);
        
        // Показываем/скрываем детали
        const details = document.getElementById('ollama-details');
        if (status.health === 'down') {
            details.style.opacity = '0.5';
        } else {
            details.style.opacity = '1';
        }
    }
    
    updateAutomatic1111Status(status) {
        const card = document.getElementById('automatic1111-status');
        if (!card) return;
        
        card.className = `service-card ${status.health}`;
        
        const locationEl = document.getElementById('automatic1111-location');
        locationEl.textContent = this.getLocationText(status.location);
        locationEl.className = `service-location ${status.location}`;
        
        const healthEl = document.getElementById('automatic1111-health');
        healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
        
        document.getElementById('automatic1111-mode').textContent = this.getModeText(status.mode);
        document.getElementById('automatic1111-url').textContent = status.url || 'Не настроен';
        document.getElementById('automatic1111-model').textContent = status.current_model || 'Неизвестно';
        
        // Загрузка
        if (status.load > 0) {
            document.getElementById('automatic1111-load-row').style.display = 'flex';
            document.getElementById('automatic1111-load').textContent = `${status.load.toFixed(0)}%`;
        } else {
            document.getElementById('automatic1111-load-row').style.display = 'none';
        }
        
        this.updateMemoryIndicator('automatic1111', status.memory);
        
        const details = document.getElementById('automatic1111-details');
        details.style.opacity = status.health === 'down' ? '0.5' : '1';
    }
    
    updateCameraStatus(status) {
        const card = document.getElementById('camera-status');
        if (!card) return;
        
        card.className = `service-card ${status.health}`;
        
        const locationEl = document.getElementById('camera-location');
        locationEl.textContent = this.getLocationText(status.location);
        locationEl.className = `service-location ${status.location}`;
        
        const healthEl = document.getElementById('camera-health');
        healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
        
        document.getElementById('camera-url').textContent = status.url || 'Не настроен';
        
        // Комнаты
        if (status.details && status.details.rooms) {
            document.getElementById('camera-rooms').textContent = status.details.rooms.join(', ');
        } else {
            document.getElementById('camera-rooms').textContent = 'Нет данных';
        }
    }
    
    updateSystemInfo(data) {
        // GPU
        let gpuText = 'Нет GPU';
        if (data.gpu.available) {
            gpuText = `${data.gpu.type} (${data.gpu.count}x)`;
            if (data.gpu.memory_mb && data.gpu.memory_mb.length > 0) {
                const totalGB = data.gpu.memory_mb[0] / 1024;
                gpuText += ` ${totalGB.toFixed(1)}GB`;
            }
        }
        document.getElementById('system-gpu').textContent = gpuText;
        
        // CPU
        document.getElementById('system-cpu').textContent = `Загрузка: ${data.system.cpu_load.toFixed(0)}%`;
        
        // RAM
        document.getElementById('system-ram').textContent = `${data.system.ram_available_gb.toFixed(1)}GB свободно`;
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
    
    getModeText(mode) {
        const texts = {
            'gpu_only': '⚡ GPU',
            'cpu_only': '🐢 CPU',
            'hybrid': '🔄 Гибрид',
            'unknown': '❓'
        };
        return texts[mode] || mode;
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    window.serviceMonitor = new ServiceStatusMonitor();
});