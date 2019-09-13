import logging
import shutil
from pathlib import PurePosixPath
from subprocess import CalledProcessError, run as subprocess_run  # nosec

import docker
from docker.errors import APIError, ContainerError, ImageLoadError
from rpdk.core.data_loaders import resource_stream
from rpdk.core.exceptions import DownstreamError, SysExitRecommendedError
from rpdk.core.init import input_with_validation
from rpdk.core.jsonutils.resolver import ContainerType, resolve_models
from rpdk.core.plugin_base import LanguagePlugin

from .resolver import models_in_properties, translate_type

LOG = logging.getLogger(__name__)

EXECUTABLE = "cfn-cli"
SUPPORT_LIB_NAME = "aws-cloudformation-rpdk-python-lib"
SUPPORT_LIB_PKG = SUPPORT_LIB_NAME.replace("-", "_")


class StandardDistNotFoundError(SysExitRecommendedError):
    pass


def validate_no(value):
    return value.lower() not in ("n", "no")


class Python36LanguagePlugin(LanguagePlugin):
    MODULE_NAME = __name__
    NAME = "python36"
    RUNTIME = "python3.6"
    ENTRY_POINT = "{}.handlers.resource"
    CODE_URI = "{}.zip"

    def __init__(self):
        self.env = self._setup_jinja_env(
            trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True
        )
        self.env.filters["translate_type"] = translate_type
        self.env.filters["models_in_properties"] = models_in_properties
        self.env.globals["ContainerType"] = ContainerType
        self.namespace = None
        self.package_name = None
        self.package_root = None
        self._use_docker = True

    def _init_from_project(self, project):
        self.namespace = tuple(s.lower() for s in project.type_info)
        self.package_name = "_".join(self.namespace)
        self._use_docker = project.settings.get("use_docker", True)
        self.package_root = project.root / "src"

    def _prompt_for_use_docker(self, project):
        self._use_docker = input_with_validation(
            "Use docker for platform-independent packaging (Y/n)?\n",
            validate_no,
            "This is highly recommended unless you are experienced \n"
            "with cross-platform Python packaging.",
        )
        project.settings["use_docker"] = self._use_docker

    def init(self, project):
        LOG.debug("Init started")

        self._init_from_project(project)
        self._prompt_for_use_docker(project)

        project.runtime = self.RUNTIME
        project.entrypoint = self.ENTRY_POINT.format(self.package_name)

        def _render_template(path, **kwargs):
            LOG.debug("Writing '%s'", path)
            template = self.env.get_template(path.name)
            contents = template.render(**kwargs)
            project.safewrite(path, contents)

        def _copy_resource(path, resource_name=None):
            LOG.debug("Writing '%s'", path)
            if not resource_name:
                resource_name = path.name
            contents = resource_stream(__name__, f"data/{resource_name}").read()
            project.safewrite(path, contents)

        # handler Python package
        handler_package_path = self.package_root / self.package_name
        LOG.debug("Making folder '%s'", handler_package_path)
        handler_package_path.mkdir(parents=True, exist_ok=True)
        _copy_resource(handler_package_path / "__init__.py")
        _render_template(
            handler_package_path / "handlers.py", support_lib_pkg=SUPPORT_LIB_PKG
        )
        # models.py produced by generate

        # project support files
        _copy_resource(project.root / ".gitignore", "Python.gitignore")
        _render_template(
            project.root / "requirements.txt", support_lib_name=SUPPORT_LIB_NAME
        )
        _render_template(
            project.root / "README.md",
            type_name=project.type_name,
            schema_path=project.schema_path,
            project_path=self.package_name,
            executable=EXECUTABLE,
            support_lib_pkg=SUPPORT_LIB_PKG,
        )

        # CloudFormation/SAM template for handler lambda
        handler_params = {
            "Handler": project.entrypoint,
            "Runtime": project.runtime,
            "CodeUri": self.CODE_URI.format(project.hypenated_name),
        }
        _render_template(
            project.root / "template.yml",
            resource_type=project.type_name,
            functions={
                "TypeFunction": handler_params,
                "TestEntrypoint": {
                    **handler_params,
                    "Handler": handler_params["Handler"].replace(
                        ".resource", ".test_entrypoint"
                    ),
                },
            },
        )

        LOG.debug("Init complete")

    def generate(self, project):
        LOG.debug("Generate started")

        self._init_from_project(project)

        models = resolve_models(project.schema)

        path = self.package_root / self.package_name / "models.py"
        LOG.debug("Writing file: %s", path)
        template = self.env.get_template("models.py")
        contents = template.render(support_lib_pkg=SUPPORT_LIB_PKG, models=models)
        project.overwrite(path, contents)

        LOG.debug("Generate complete")

    def package(self, project, zip_file):
        LOG.debug("Package started")

        self._init_from_project(project)

        def recursive_relative_write(src_path, base_path):
            for path in src_path.rglob("*"):
                if path.is_file() and path.suffix != ".pyc":
                    relative = path.relative_to(base_path)
                    zip_file.write(path.resolve(), str(relative))

        handler_package_path = self.package_root / self.package_name
        deps_path = project.root / "build"

        self._remove_build_artifacts(deps_path)
        self._build(project.root)

        recursive_relative_write(handler_package_path, self.package_root)
        recursive_relative_write(deps_path, deps_path)

        LOG.debug("Package complete")

    @staticmethod
    def _remove_build_artifacts(deps_path):
        try:
            shutil.rmtree(deps_path)
        except FileNotFoundError:
            LOG.debug("'%s' not found, skipping removal", deps_path, exc_info=True)

    def _build(self, base_path):
        LOG.debug("Dependencies build started from '%s'", base_path)
        if self._use_docker:
            self._docker_build(base_path)
        else:
            self._pip_build(base_path)
        LOG.debug("Dependencies build finished")

    @staticmethod
    def _check_for_support_lib_sdist(base_path):
        # TODO: remove this check (and exception) when published to PyPI
        sdist = base_path / f"{SUPPORT_LIB_NAME}-0.0.1.tar.gz"
        try:
            sdist.resolve(strict=True)
        except FileNotFoundError:
            raise StandardDistNotFoundError(
                f"Could not find packaged CloudFormation support library: {sdist}\n"
            )

    @staticmethod
    def _make_pip_command(base_path):
        return [
            "pip",
            "install",
            "--no-cache-dir",
            "--no-color",
            "--disable-pip-version-check",
            "--upgrade",
            # TODO: remove find-links when published to PyPI
            "--find-links",
            str(base_path),
            "--requirement",
            str(base_path / "requirements.txt"),
            "--target",
            str(base_path / "build"),
        ]

    @classmethod
    def _docker_build(cls, external_path):
        cls._check_for_support_lib_sdist(external_path)

        internal_path = PurePosixPath("/project")
        command = " ".join(cls._make_pip_command(internal_path))
        LOG.debug("command is '%s'", command)

        volumes = {str(external_path): {"bind": str(internal_path), "mode": "rw"}}
        image = f"lambci/lambda:build-{cls.RUNTIME}"
        LOG.warning(
            "Starting Docker build. This may take several minutes if the "
            "image '%s' needs to be pulled first.",
            image,
        )
        docker_client = docker.from_env()
        try:
            logs = docker_client.containers.run(
                image=image,
                command=command,
                auto_remove=True,
                volumes=volumes,
                stream=True,
            )
        except (ContainerError, ImageLoadError, APIError) as e:
            raise DownstreamError("Error running docker build") from e
        LOG.debug("Build running. Output:")
        for line in logs:
            LOG.debug(line.rstrip(b"\n").decode("utf-8"))

    @classmethod
    def _pip_build(cls, base_path):
        cls._check_for_support_lib_sdist(base_path)
        command = cls._make_pip_command(base_path)
        LOG.debug("command is '%s'", command)

        LOG.warning("Starting pip build.")
        try:
            completed_proc = subprocess_run(  # nosec
                command, capture_output=True, cwd=base_path, check=True
            )
        except (FileNotFoundError, CalledProcessError) as e:
            raise DownstreamError("pip build failed") from e

        LOG.debug("--- pip stdout:\n%s", completed_proc.stdout)
        LOG.debug("--- pip stderr:\n%s", completed_proc.stderr)


class Python37LanguagePlugin(Python36LanguagePlugin):
    NAME = "python37"
    RUNTIME = "python3.7"
