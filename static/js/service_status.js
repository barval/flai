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
            
            // Обновляем глобальный индикатор
            this.updateGlobalIndicator(data);
            
        } catch (error) {
            console.error('Error updating service status:', error);
        }
    }
    
    updateGlobalIndicator(statuses) {
        const indicator = document.getElementById('services-global-indicator');
        if (!indicator) return;
        
        // Проверяем все сервисы
        const allHealthy = Object.values(statuses).every(s => s.health === 'healthy');
        
        if (allHealthy) {
            indicator.innerHTML = '🟢';
            indicator.title = 'Все сервисы работают';
        } else {
            indicator.innerHTML = '🔴';
            
            // Собираем список проблемных сервисов для подсказки
            const problematic = [];
            for (const [name, status] of Object.entries(statuses)) {
                if (status.health !== 'healthy') {
                    let displayName = name === 'automatic1111' ? 'Automatic1111' : 
                                     name === 'camera_api' ? 'Камеры' : 'Ollama';
                    problematic.push(`${displayName}: ${status.health}`);
                }
            }
            indicator.title = `Проблемы: ${problematic.join(', ')}`;
        }
    }
    
    updateOllamaStatus(status) {
        const card = document.getElementById('ollama-status');
        if (!card) return;
        
        card.className = `service-card ${status.health}`;
        
        const locationEl = document.getElementById('ollama-location');
        if (locationEl) {
            locationEl.textContent = this.getLocationText(status.location);
            locationEl.className = `service-location ${status.location}`;
        }
        
        const healthEl = document.getElementById('ollama-health');
        if (healthEl) {
            healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
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
        
        const healthEl = document.getElementById('automatic1111-health');
        if (healthEl) {
            healthEl.innerHTML = `<span class="health-icon ${status.health}"></span>${this.getHealthText(status.health)}`;
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
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    window.serviceMonitor = new ServiceStatusMonitor();
});