# Administration


## What the admin panel does

- 👤 **User Management** – add, edit, delete users; change passwords; assign service classes
- 🔑 **Camera Permissions** – control which users can access which cameras (Optional)
- 🤖 **Model Management** – select and configure GGUF models for multimodal, reasoning, and embedding directly from the admin panel
- 🧭 **Model Hub** – search Hugging Face for GGUF models with a GPU/RAM fit estimate before downloading, download with progress/resume, delete downloaded files, and keep working offline
- 💾 **Backup & Restore** – create and restore full or user-only backups directly from the admin interface
- 🎨 **Personalization** – brand the instance:
  - **Header logo** – upload a custom logo (PNG/JPEG/WebP, auto-scaled to 128 px with alpha preserved); deleting it restores the built-in logo.
  - **Site name** – in Russian and English (both required, max 40 chars each); shown in the header, in the browser tab title and in exported chats, with automatic font shrink on narrow screens.
  - **Chat export** – reuses the live site's footer: a short brand label that opens the same About dialog (full name, version, GitHub link, copyright), and the custom logo is embedded in both the header and that dialog.
  - **Backups** – a saved branding set is included in full backups.
- 🖥 **Hardware Overview** – first admin tab showing compute platform (`nvidia`/`amd`/`intel`/`cpu`), GPU name, VRAM and RAM (available of total), and CPU cores
- 📈 **System Monitoring** – view database sizes and system statistics
- 🔧 **CLI Tools** – admin password, upload cleanup, message format migration, SLM history import/cleanup/checkpoint reset via Flask CLI commands

---

## User management

### Admin Panel Features
| Feature | Description |
|---------|-------------|
| 👤 User Operations | Create, edit, delete user accounts |
| 🔑 Password Management | Reset passwords for any user |
| 🔐 Camera Permissions | Grant/revoke camera access per user |
| 🔢 Per-user token totals | View and sort prompt tokens sent and completion tokens received |
| 🤖 Model Management | Configure GGUF models per module type |
| 📊 System Stats | Monitor database and storage sizes |
| 🎚️ Service Classes | Set queue priority (0=highest, 2=lowest) |
| «Tasks» column in the user table — the Users table shows each user's task count; background task classes (3/4/5) are internal and never offered to admins — service classes stay 0/1/2 |
| «Storage» column in the user table — upload usage / `MAX_UPLOAD_STORAGE_MB`, placed before Sessions and sorted numerically; an over-quota value is highlighted. The System Stats line maps the legacy «User DB»/«Chat DB» labels to real PostgreSQL table sizes |

## CLI Commands
```bash
# Set admin password
docker exec flai-web flask admin-password NewPassword123

# View help
docker exec flai-web flask --help
```

---

## 💾 Backup & Restore

FLAI includes a built-in backup system accessible from the Admin Panel → **Backups** tab.

**Backup Types:**
- **Users only:** Backs up the `users` table only (user accounts, permissions, settings); chat history and token totals are not included.
- **Full:** Backs up all data: users, chat sessions, messages (including prompt and completion token counts), documents, uploaded files, and model configurations.

**Operations:**
- **Create:** Select the backup type and click «Create backup». The archive is saved to `data/db_backups/`.
- **Restore:** Click «Restore» on a backup file to replace the current database and files with the backup content. *Warning: This overwrites existing data.*
- **Download:** Download the backup archive to your local machine.
- **Delete:** Remove old backup files.

Backup files are stored as `.tar.gz` archives containing SQL dumps and file directories. Restoration requires confirmation and is logged for audit purposes.

---
