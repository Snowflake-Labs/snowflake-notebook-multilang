# Flat build context for `snow spcs service build-image` (single directory, no subfolders).
# Set REGISTRY_LOCAL_URL at build time (in-account registry host for Image Builder).
#
# Example:
#   export REGISTRY_LOCAL_URL=yourorg-youracct.registry-local.snowflakecomputing.com
#   docker build --build-arg REGISTRY_LOCAL_URL=... -f Dockerfile .

ARG REGISTRY_LOCAL_URL
ARG SNOWBOOKS_TAG=2.7.2
FROM ${REGISTRY_LOCAL_URL}/snowflake/images/snowflake_images/container_runtime/cpu_x86_64:${SNOWBOOKS_TAG}

ENV DASHBOARD_PORT=12003
ENV SFNB_CUSTOM_RUNTIME=1
ENV TZ=UTC
ENV SFNB_ENV_NAME=workspace_env
ENV SFNB_MICROMAMBA_ROOT=/home/jupyter/micromamba
ENV SFNB_CONFIG_PATH=/opt/sfnb/config/cre_multilang_r.yaml
ENV SFNB_HELPERS_PATH=/opt/sfnb/helpers

USER root

RUN (command -v bzip2 >/dev/null || (apt-get update && apt-get install -y --no-install-recommends bzip2 unzip && rm -rf /var/lib/apt/lists/*)) \
    && command -v curl >/dev/null \
    && (command -v unzip >/dev/null || (apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*))

COPY install_prebaked_r.sh /tmp/install_prebaked_r.sh
COPY cre_extra_install.sh /tmp/cre_extra_install.sh
COPY cre_multilang_r.yaml /tmp/cre_multilang_r.yaml
COPY 00-sfnb-enable-r.py /tmp/00-sfnb-enable-r.py
COPY r_helpers.py /tmp/r_helpers.py
COPY sfnb_setup.py /tmp/sfnb_setup.py
ARG SFNB_CRE_IMAGE_TAG=v1
ARG SFNB_CRE_ADBC=1
RUN chmod +x /tmp/install_prebaked_r.sh /tmp/cre_extra_install.sh \
    && SFNB_CRE_IMAGE_TAG="${SFNB_CRE_IMAGE_TAG}" SFNB_CRE_ADBC="${SFNB_CRE_ADBC}" bash /tmp/install_prebaked_r.sh \
    && rm -f /tmp/install_prebaked_r.sh /tmp/cre_extra_install.sh /tmp/cre_multilang_r.yaml /tmp/00-sfnb-enable-r.py \
       /tmp/r_helpers.py /tmp/sfnb_setup.py
