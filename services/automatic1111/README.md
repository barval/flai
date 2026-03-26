# Automatic1111 - Standalone Deployment

## Quick Start

```bash
# 1. Create directories
mkdir -p models outputs

# 2. Download Stable Diffusion model
# Example: RealVisXL_V4.0
wget -O models/RealVisXL_V4.0.safetensors \
  "https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors"

# 3. Copy environment file
cp .env.example .env

# 4. Start the service
docker-compose up -d

# 5. Check status
docker-compose logs -f

# 6. Open web interface
# http://<server-ip>:7860