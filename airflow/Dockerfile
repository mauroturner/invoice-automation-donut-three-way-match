# Airflow with the libraries of the pipeline, at the exact versions of requirements.txt and requirements-ml.txt (the same == pins as the command line, RNF01, D35). The official
# pattern: reinstall apache-airflow at the same version so that pip never changes it while it resolves the other packages; if a pin ever conflicts with Airflow's own constraints
# the build fails instead of installing another version. The build context is the project folder (see docker-compose.yaml), and .dockerignore lets only the requirements files,
# src/, sql/ and config/ into it (src/, sql/ and config/ are mounted over /opt/invoiceflow at run time).
# The base image is pinned by its version and by its digest, the one projects 04 to 06 pinned (D26): the tag alone can be moved to other bytes.
FROM apache/airflow:3.4.0@sha256:a8d9d19c563c768b2bc605ef26440f28a9164ea886064e99c599b057432ff572

ARG AIRFLOW_VERSION=3.4.0

# The native library of pyzbar (D07). The package of Debian 13 is libzbar0t64 (the name before the 64-bit time transition, libzbar0, no longer exists); its version is pinned.
USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y libzbar0t64=0.23.93-8+dhi2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
USER airflow

# torch for the CPU first, from the PyTorch index (requirements-ml.txt says so): the pin of requirements-ml.txt is then already satisfied and pip does not look for the CUDA wheel.
RUN pip install --no-cache-dir torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt requirements-ml.txt /
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /requirements-ml.txt
