FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
	python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install anthropic PyPDF2 --break-system-packages

RUN useradd -m -u 1001 evaluser
USER evaluser

WORKDIR /input

COPY --chown=evaluser:evaluser eval/run_eval.py /app/run_eval.py

ENTRYPOINT ["python3", "/app/run_eval.py"]
