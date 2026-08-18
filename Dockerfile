# Dramatis as a container: the API, the built client, and every prompt the pipeline needs,
# in one image that runs `dramatis serve` against a mounted project store.
#
# Three stages, so the runtime image carries none of the toolchain that built it. Node and
# the TypeScript compiler live only in the web stage; the source tree and build backend live
# only in the wheel stage; the runtime is a slim Python image with a wheel and a folder of
# static files. Nothing about the manuscript's privacy is weakened by running in a container
# — the model is still whichever provider the user points at, and a fully local analysis
# still reaches nothing but the Ollama on the host.

# --- stage 1: build the client -------------------------------------------------------------
# Node 22 matches the CI web job, so the container builds the client the same way the tests
# prove it builds.
FROM node:22-slim AS web
WORKDIR /web

# Dependencies first, on their own layer: package.json changes far less often than source, so
# a source edit does not re-run `npm ci`.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# `npm run build` is `tsc --noEmit && vite build`; a client that fails to typecheck fails the
# image build rather than shipping broken.
RUN npm run build

# --- stage 2: build the wheel --------------------------------------------------------------
FROM python:3.12-slim AS wheel
WORKDIR /build

RUN pip install --no-cache-dir build

# Only what the wheel is built from. The wheel carries the prompts and the schema as package
# data (verified: `dramatis/prompts/*.md` and `dramatis/schema/*.json` are inside it), so the
# runtime needs none of this source.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m build --wheel --outdir /dist

# --- stage 3: the runtime ------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Install our wheel by its file path, with the serve extras. The path matters: there is an
# unrelated package also called `dramatis` on PyPI, and installing by *name* — even with
# --find-links pointing here — let pip prefer that stranger's higher version number and ship
# it instead of this application. Giving pip the file leaves it no name to resolve, so it
# installs exactly what stage 2 built; the [serve] extra still pulls fastapi and uvicorn from
# the index, so the runtime deps stay defined in one place (pyproject).
COPY --from=wheel /dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir "$(ls /tmp/wheels/*.whl)[serve]" \
    && rm -rf /tmp/wheels

# The built client, and the environment variable that tells the server where it is. Without
# this the server computes the client path relative to its own source file — correct in a
# checkout, wrong for a wheel in site-packages — and would serve the 503 "not built" page for
# a client the image is holding.
COPY --from=web /web/dist /opt/dramatis/web
ENV DRAMATIS_WEB_ROOT=/opt/dramatis/web

# A non-root user owns nothing it should not. The store is mounted at /data, which this user
# can write; the code and client under /opt and site-packages are read-only to it.
RUN useradd --create-home --uid 10001 dramatis \
    && mkdir /data \
    && chown dramatis /data
USER dramatis
WORKDIR /data
VOLUME ["/data"]

EXPOSE 7373

# Bind 0.0.0.0, deliberately and only here. The `serve` default is 127.0.0.1, because a
# manuscript should not reach the LAN just because someone ran a command — but inside a
# container loopback is unreachable from the host, so an image that kept the default would
# never answer. The boundary moves to the user's `docker run -p`: publish to 127.0.0.1 to
# keep it on your machine, or to 0.0.0.0 knowingly. `--help` for `serve` says as much.
ENTRYPOINT ["dramatis"]
CMD ["serve", "--host", "0.0.0.0", "--store", "/data/dramatis.sqlite"]

# Answers with the same interpreter that runs the server, so the check needs no curl in the
# image. Reading is model-free (Invariant 6), so a healthy server answers /api/health with no
# provider configured.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7373/api/health', timeout=4).status==200 else 1)"]
