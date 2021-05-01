
### Requirements

- Docker
- Docker Compose

### Usage

1. Add your email address and url to the docker-compose.yml
2. Open ports 80, 443, 5556 and 5557 on the host machine for tcp traffic
3. Start the server with `sudo docker-compose up`
4. Startup worker devices, connecion messages should appear in the terminal of the server
5. go to your url you defined in step 1 to use the tracker
