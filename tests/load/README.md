# Load Testing with Locust

To run load tests on the FLAI application:

## Prerequisites

- Python 3.9 or higher
- The FLAI application must be running (e.g., via `docker compose -f docker-compose.gpu.yml up -d`)

## Setup (Virtual Environment)

To avoid the "externally-managed-environment" error that occurs on modern Linux distributions, it is strongly recommended to use a Python virtual environment.

### Step-by-step setup

1. **Navigate to the project root directory** (where `docker-compose.gpu.yml` is located).

2. **Create a virtual environment**:
```bash
python3 -m venv venv
```
3. **Activate the virtual environment**:  
    - On Linux/macOS:
    ```bash
    source venv/bin/activate
    ```
    - On Windows:
    ```bash
    venv\Scripts\activate
    ```
4. **Install Locust** (and any other dependencies if needed):
```bash
pip install locust
```
5. **Run Locust with the provided test script**:
```bash
locust -f tests/load/locustfile.py --host http://localhost:5000
```
6. **Open the Locust web interface** at `http://localhost:8089` and start the test.

## Alternative: Using pipx
If you prefer not to activate a virtual environment each time, you can install Locust with pipx:
```bash
sudo apt install pipx
pipx ensurepath
# Restart your terminal or source ~/.bashrc
pipx install locust
```
Then run Locust from anywhere:
```bash
locust -f tests/load/locustfile.py --host http://localhost:5000
```

## Running Headless
For automated testing, you can run Locust without the web UI:
```bash
locust -f tests/load/locustfile.py --host http://localhost:5000 --headless -u 10 -r 2 --run-time 1m
```
- `-u` : number of users
- `-r` : spawn rate (users per second)
- `--run-time` : duration of the test

## Test Users
`locustfile.py` simulates five pre-created accounts — `loaduser1` … `loaduser5` (passwords `loadpass1` … `loadpass5`). These users must exist in the FLAI system before running the test; create them via the admin panel or the API. Each simulated user logs in with the full CSRF flow (GET `/login`, extract `csrf_token`, POST credentials) and then creates its own chat session for subsequent requests.

`locustfile_public.py` contains the variant without authentication.

## Notes
- These load tests are excluded from the pytest collection (`tests/load/` is in `norecursedirs`) — run them only through Locust, e.g. `locust -f tests/load/locustfile.py --host http://localhost:5000`.
- The test assumes the application is running at `http://localhost:5000`. Adjust the `--host` parameter if needed.
- The results are shown in the Locust web UI or printed in the console when running headless.