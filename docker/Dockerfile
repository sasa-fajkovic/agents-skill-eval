FROM ubuntu:26.04@sha256:d66b366dac61b575bee2aef07e9c26e8070096fbe84a3d4d3eb0da069b5d76db

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
	ca-certificates \
	python3 \
	python3-pip \
	&& rm -rf /var/lib/apt/lists/*

COPY .claude/skills/skill-evaluation/requirements.txt /tmp/skill-evaluation-requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/skill-evaluation-requirements.txt

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

COPY --chown=evaluser:evaluser .claude/skills/skill-evaluation /app/skill-evaluation

ENTRYPOINT ["python3", "/app/skill-evaluation/scripts/eval.py"]
