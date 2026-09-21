ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 nodejs npm make gcc g++ git strace util-linux \
    && dpkg-query -W -f='${Package}\t${Version}\n' > /opt/monkey-packages.txt \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/monkey /input /evidence /work \
    && chmod 0700 /evidence
ARG SUPERVISOR_SHA256
LABEL local.monkey.supervisor=${SUPERVISOR_SHA256}
COPY build_supervisor.py /opt/monkey/build_supervisor.py
RUN test "$(sha256sum /opt/monkey/build_supervisor.py | cut -d ' ' -f 1)" = "$SUPERVISOR_SHA256"
ENTRYPOINT []
CMD ["/usr/bin/python3", "-I", "/opt/monkey/build_supervisor.py"]
