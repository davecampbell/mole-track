PI      = dave@192.168.86.30
PI_PATH = /home/dave/mole-track

.PHONY: deploy deploy-restart run-pi logs-pi status-pi install-pi setup-service

## Sync files to Pi (no restart)
deploy:
	bash deploy.sh

## Sync files to Pi and restart the service
deploy-restart:
	bash deploy.sh --restart

## Run the server directly on the Pi (useful for first-run / debugging)
run-pi:
	ssh $(PI) "cd $(PI_PATH) && python3 -m uvicorn mole_track.main:app --host 0.0.0.0 --port 8000 --log-level info"

## Tail the systemd service logs
logs-pi:
	ssh $(PI) "sudo journalctl -u mole-track.service -f --no-pager"

## Show service status
status-pi:
	ssh $(PI) "sudo systemctl status mole-track.service --no-pager"

## Install system Python packages on Pi (run once)
install-pi:
	ssh $(PI) "sudo apt-get install -y python3-picamera2 python3-opencv python3-fastapi python3-uvicorn"

## Install and enable the systemd service on Pi (run once)
setup-service:
	scp mole-track.service $(PI):/tmp/mole-track.service
	ssh $(PI) "sudo mv /tmp/mole-track.service /etc/systemd/system/mole-track.service && sudo systemctl daemon-reload && sudo systemctl enable mole-track.service && echo 'Service enabled'"
