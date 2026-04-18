FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
	ca-certificates \
	curl \
	gnupg \
	golang-go \
	python3 \
	python3-pip \
	&& mkdir -p /etc/apt/keyrings \
	&& curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
	&& echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
	&& apt-get update && apt-get install -y nodejs \
	&& npm install -g typescript \
	&& rm -rf /var/lib/apt/lists/* /root/.cache/pip /root/.npm

RUN pip3 install anthropic PyPDF2 python-docx --break-system-packages

RUN existing_user="$(getent passwd 1000 | cut -d: -f1)" \
	&& if [ -n "$existing_user" ]; then \
		group_name="$(id -gn "$existing_user")"; \
		usermod -l evaluser "$existing_user"; \
		usermod -d /home/evaluser -m evaluser; \
		if [ "$group_name" != "evaluser" ]; then groupmod -n evaluser "$group_name"; fi; \
	else \
		useradd -m -u 1000 evaluser; \
	fi
USER evaluser

WORKDIR /input

COPY --chown=evaluser:evaluser eval/run_eval.py /app/run_eval.py

ENTRYPOINT ["python3", "/app/run_eval.py"]
