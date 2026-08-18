"""Guards the container image against the mistakes that would make it a broken product.

Working rule 4 asks every commit for an automated check. The real proof of a Dockerfile is
`docker build`, which needs a daemon this suite cannot assume — so this reads the file and
asserts the properties that, if they regressed, would ship an image that builds and then does
the wrong thing: serving the 503 page for a client it holds, binding a port nothing can
reach, or baking a manuscript into a layer.

The build itself was run by hand and the container exercised; see the phase 4.7 note in
DECISIONS.md. This keeps the file honest between those runs.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_the_dockerfile_and_ignore_exist() -> None:
    assert DOCKERFILE.is_file()
    assert DOCKERIGNORE.is_file()


def test_it_builds_the_client_and_the_wheel_in_stages_the_runtime_discards() -> None:
    # A runtime carrying Node or the source tree is a bigger attack surface for no gain.
    text = dockerfile()
    assert "AS web" in text
    assert "AS wheel" in text
    assert "AS runtime" in text
    assert text.count("COPY --from=web") >= 1
    assert text.count("COPY --from=wheel") >= 1


def test_the_client_build_matches_the_ci_node_version() -> None:
    # The tests prove the client builds under Node 22; the image must build it the same way.
    assert "node:22" in dockerfile()


def test_it_tells_the_server_where_the_client_is() -> None:
    # The bug this env var exists to prevent: a wheel install computes the client path
    # relative to its own source file and finds nothing, then serves "not built" for a client
    # sitting in the image. See server.web_root.
    text = dockerfile()
    assert "DRAMATIS_WEB_ROOT=" in text
    assert "COPY --from=web" in text


def test_it_installs_our_wheel_by_path_not_by_name() -> None:
    # A real build caught this: there is an unrelated `dramatis` on PyPI, and installing by
    # name let pip prefer its higher version number and ship a stranger's package with no
    # `dramatis` command. Installing the wheel by file path leaves pip no name to resolve.
    text = dockerfile()
    assert "/tmp/wheels/*.whl" in text
    assert '"dramatis[serve]"' not in text, "installing by name reintroduces the collision"


def test_it_still_pulls_the_serve_extras() -> None:
    # The wheel is installed with its [serve] extra, so fastapi and uvicorn stay defined once,
    # in pyproject, rather than being named again here.
    assert "[serve]" in dockerfile()


def test_the_container_binds_all_interfaces_because_loopback_is_unreachable() -> None:
    # The serve default is 127.0.0.1 and must stay so; the container is the one place that
    # overrides it, because inside a container loopback cannot be reached from the host. The
    # user's `-p` becomes the boundary.
    assert "0.0.0.0" in dockerfile()


def test_it_runs_as_a_non_root_user() -> None:
    text = dockerfile()
    assert "useradd" in text
    assert "USER dramatis" in text


def test_the_store_lives_on_a_mounted_volume() -> None:
    # A store baked into a layer is a manuscript baked into a layer. It is mounted, not built.
    assert 'VOLUME ["/data"]' in dockerfile()


def test_it_has_a_model_free_healthcheck() -> None:
    # Reading is model-free (Invariant 6), so /api/health answers with no provider set.
    text = dockerfile()
    assert "HEALTHCHECK" in text
    assert "/api/health" in text


def test_the_ignore_keeps_stores_and_local_builds_out_of_the_context() -> None:
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    # A real store must never enter the build context.
    assert "*.sqlite" in text
    # The client is rebuilt in the image; a stale local dist must not win.
    assert "web/dist/" in text
    assert "web/node_modules/" in text
    assert ".venv/" in text
