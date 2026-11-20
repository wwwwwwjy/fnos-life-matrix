FROM scratch
COPY docker/runtime/ /
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
COPY --chmod=644 panel/server.py sampler.py
COPY --chmod=644 docker/bridge.py bridge.py
USER 65534:65534
RUN ["/usr/bin/python3", "-c", "import bridge"]
CMD ["/usr/bin/python3", "-u", "bridge.py"]
