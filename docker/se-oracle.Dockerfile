FROM python:3.12-slim
WORKDIR /oracle
COPY se_tasks /oracle/se_tasks
CMD ["sleep", "infinity"]
