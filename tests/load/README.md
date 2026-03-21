# Load Testing with Locust

To run load tests:

1. Install locust: `pip install locust`
2. Ensure your application is running (e.g., `docker-compose up`)
3. Run locust: `locust -f tests/load/locustfile.py --host http://localhost:5000`
4. Open browser to http://localhost:8089 and start the test.

You can adjust the number of users and spawn rate.