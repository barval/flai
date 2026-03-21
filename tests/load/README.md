# Load Testing with Locust

To run load tests on the FLAI application:

## Prerequisites

- Python 3.9 or higher
- The FLAI application must be running (e.g., via `docker-compose up`)

## Setup (Virtual Environment)

To avoid the "externally-managed-environment" error that occurs on modern Linux distributions, it is strongly recommended to use a Python virtual environment.

### Step-by-step setup

1. **Navigate to the project root directory** (where `docker-compose.yml` is located).

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

## Test User
The test script uses a user named `testuser` with password `testpass`. This user must exist in the FLAI system before running the test. You can create it via the admin panel (login as admin) or using the API.

## Notes
The test creates a new session for each simulated user and uses it for subsequent requests.
The test assumes the application is running at `http://localhost:5000`. Adjust the `--host` parameter if needed.
The results are shown in the Locust web UI or printed in the console when running headless.