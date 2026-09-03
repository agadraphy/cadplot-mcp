from __future__ import annotations

import argparse
import os
import ssl
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import yaml
from cadplot_protocol.remote_protocol import DeviceId, ProjectId, SafeAlias, TenantId, UserId
from cadplot_protocol.worker_http_protocol import WorkerKeyId
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from cadplot_mcp.config import load_config
from cadplot_mcp.local_refs import LocalReferenceError, LocalReferenceStore
from cadplot_mcp.security import PathPolicyError, require_plain_directory_path
from cadplot_mcp.worker_crypto import (
    Ed25519Signer,
    Ed25519Verifier,
    WorkerCryptoError,
    verifier_from_pem_resolver,
)
from cadplot_mcp.worker_dispatch import DefaultLocalReadBackend, LocalReadBackend, WorkerDispatcher
from cadplot_mcp.worker_runner import WorkerRunner, WorkerRunOutcome
from cadplot_mcp.worker_transport import (
    HttpExchange,
    WorkerHttpsClient,
    WorkerTransportError,
    _canonical_https_origin,
)

MAX_WORKER_CONFIG_BYTES = 64 * 1024
MAX_KEY_FILE_BYTES = 64 * 1024
MAX_TLS_FILE_BYTES = 1024 * 1024
MAX_PROJECTS = 100
MIN_POLL_INTERVAL_SECONDS = 1
MAX_POLL_INTERVAL_SECONDS = 60
MIN_REQUEST_TIMEOUT_SECONDS = 1
MAX_REQUEST_TIMEOUT_SECONDS = 60

LocalPathText = Annotated[StrictStr, Field(min_length=1, max_length=4_096)]
PolicyVersion = Annotated[StrictInt, Field(ge=1, le=2_147_483_647)]
PollInterval = Annotated[
    StrictInt,
    Field(ge=MIN_POLL_INTERVAL_SECONDS, le=MAX_POLL_INTERVAL_SECONDS),
]
RequestTimeout = Annotated[
    StrictInt,
    Field(ge=MIN_REQUEST_TIMEOUT_SECONDS, le=MAX_REQUEST_TIMEOUT_SECONDS),
]


class WorkerCliError(RuntimeError):
    """Bounded CLI failure whose text never contains configuration or secret material."""

    __slots__ = ("code",)

    _ALLOWED_CODES = frozenset(
        {
            "worker_config_invalid",
            "worker_config_unavailable",
            "worker_iteration_failed",
            "worker_start_failed",
            "windows_worker_required",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._ALLOWED_CODES else "worker_start_failed"
        super().__init__(self.code)


class _ClosedConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class WorkerProjectConfiguration(_ClosedConfig):
    project_id: ProjectId
    alias: SafeAlias
    root: LocalPathText

    @field_validator("root")
    @classmethod
    def validate_path_text(cls, value: str) -> str:
        return _validate_local_path_text(value)


class WorkerTlsConfiguration(_ClosedConfig):
    ca_bundle_path: LocalPathText | None = None
    client_certificate_path: LocalPathText | None = None
    client_private_key_path: LocalPathText | None = None

    @field_validator(
        "ca_bundle_path",
        "client_certificate_path",
        "client_private_key_path",
    )
    @classmethod
    def validate_path_text(cls, value: str | None) -> str | None:
        return None if value is None else _validate_local_path_text(value)

    @model_validator(mode="after")
    def require_client_certificate_pair(self) -> WorkerTlsConfiguration:
        if (self.client_certificate_path is None) != (self.client_private_key_path is None):
            raise ValueError("client_certificate_pair_required")
        return self


class WorkerConfiguration(_ClosedConfig):
    version: Literal[1]
    gateway_origin: Annotated[StrictStr, Field(min_length=9, max_length=512)]
    tenant_id: TenantId
    user_id: UserId
    device_id: DeviceId
    key_id: WorkerKeyId
    cadplot_config_path: LocalPathText
    state_database_path: LocalPathText
    request_private_key_pem_path: LocalPathText
    gateway_dispatch_public_key_pem_path: LocalPathText
    policy_version: PolicyVersion
    poll_interval_seconds: PollInterval = 5
    request_timeout_seconds: RequestTimeout = 30
    tls: WorkerTlsConfiguration = Field(default_factory=WorkerTlsConfiguration)
    projects: tuple[WorkerProjectConfiguration, ...] = Field(
        default=(),
        max_length=MAX_PROJECTS,
    )

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("worker_config_version_invalid")
        return value

    @field_validator("gateway_origin")
    @classmethod
    def validate_gateway_origin(cls, value: str) -> str:
        try:
            return _canonical_https_origin(value)
        except WorkerTransportError:
            raise ValueError("gateway_origin_invalid") from None

    @field_validator(
        "cadplot_config_path",
        "state_database_path",
        "request_private_key_pem_path",
        "gateway_dispatch_public_key_pem_path",
    )
    @classmethod
    def validate_path_text(cls, value: str) -> str:
        return _validate_local_path_text(value)

    @model_validator(mode="after")
    def require_unique_projects(self) -> WorkerConfiguration:
        project_ids = [project.project_id for project in self.projects]
        aliases = [project.alias.casefold() for project in self.projects]
        if len(project_ids) != len(set(project_ids)) or len(aliases) != len(set(aliases)):
            raise ValueError("project_configuration_conflict")
        return self


@dataclass(frozen=True, slots=True)
class ResolvedWorkerProject:
    project_id: str
    alias: str
    root: Path


@dataclass(frozen=True, slots=True)
class ResolvedWorkerTls:
    ca_bundle: Path | None
    client_certificate: Path | None
    client_private_key: Path | None


@dataclass(frozen=True, slots=True)
class LoadedWorkerConfiguration:
    values: WorkerConfiguration
    source: Path
    cadplot_config: Path
    state_database: Path
    request_private_key_pem: Path
    gateway_dispatch_public_key_pem: Path
    tls: ResolvedWorkerTls
    projects: tuple[ResolvedWorkerProject, ...]


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    runner: WorkerRunner
    poll_interval_seconds: int


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.exit(2, "cadplot-worker: arguments_invalid\n")


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[str, object]:
    loader.flatten_mapping(node)
    mapping: dict[str, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise yaml.YAMLError("worker_configuration_mapping_invalid")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_worker_configuration(path: str | Path) -> LoadedWorkerConfiguration:
    """Load and fully validate a bounded local-only worker configuration."""

    try:
        source = _plain_existing_file(_local_candidate(Path.cwd(), str(path)))
        if source.stat().st_size > MAX_WORKER_CONFIG_BYTES:
            raise WorkerCliError("worker_config_invalid")
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise WorkerCliError("worker_config_unavailable") from None
        try:
            document = yaml.load(text, Loader=_UniqueKeySafeLoader)
        except yaml.YAMLError:
            raise WorkerCliError("worker_config_invalid") from None
        if not isinstance(document, dict):
            raise WorkerCliError("worker_config_invalid")
        try:
            values = WorkerConfiguration.model_validate(document)
        except ValidationError:
            raise WorkerCliError("worker_config_invalid") from None

        base = source.parent
        cadplot_config_path = _plain_existing_file(
            _local_candidate(base, values.cadplot_config_path)
        )
        request_private_key = _plain_existing_file(
            _local_candidate(base, values.request_private_key_pem_path)
        )
        gateway_dispatch_public_key = _plain_existing_file(
            _local_candidate(base, values.gateway_dispatch_public_key_pem_path)
        )
        state_database = _plain_state_database(_local_candidate(base, values.state_database_path))
        tls = _resolve_tls(base, values.tls)

        request_material = _read_bounded_file(request_private_key, MAX_KEY_FILE_BYTES)
        gateway_material = _read_bounded_file(
            gateway_dispatch_public_key,
            MAX_KEY_FILE_BYTES,
        )
        Ed25519Signer.from_pem(request_material)
        Ed25519Verifier.from_pem(gateway_material)

        cadplot_configuration = load_config(cadplot_config_path)
        resolved_projects: list[ResolvedWorkerProject] = []
        root_keys: set[str] = set()
        for project in values.projects:
            root = _plain_existing_directory(_local_candidate(base, project.root))
            allowed = cadplot_configuration.path_policy.require_allowed(root, must_exist=True)
            if not _same_path(root, allowed):
                raise WorkerCliError("worker_config_invalid")
            root_key = os.path.normcase(str(root))
            if root_key in root_keys:
                raise WorkerCliError("worker_config_invalid")
            root_keys.add(root_key)
            resolved_projects.append(
                ResolvedWorkerProject(
                    project_id=project.project_id,
                    alias=project.alias,
                    root=root,
                )
            )
        return LoadedWorkerConfiguration(
            values=values,
            source=source,
            cadplot_config=cadplot_config_path,
            state_database=state_database,
            request_private_key_pem=request_private_key,
            gateway_dispatch_public_key_pem=gateway_dispatch_public_key,
            tls=tls,
            projects=tuple(resolved_projects),
        )
    except WorkerCliError:
        raise
    except (OSError, PathPolicyError, WorkerCryptoError, ValueError):
        raise WorkerCliError("worker_config_invalid") from None
    except Exception:
        raise WorkerCliError("worker_config_invalid") from None


def build_worker_runtime(
    configuration: LoadedWorkerConfiguration,
    *,
    platform_name: str | None = None,
    exchange: HttpExchange | None = None,
    backend: LocalReadBackend | None = None,
) -> WorkerRuntime:
    """Compose the outbound-only phase-1 worker from validated local dependencies."""

    if platform_name is None:
        platform_name = sys.platform
    if platform_name != "win32":
        raise WorkerCliError("windows_worker_required")
    if not isinstance(configuration, LoadedWorkerConfiguration):
        raise WorkerCliError("worker_config_invalid")
    try:
        values = configuration.values
        references = LocalReferenceStore(configuration.state_database)
        for project in configuration.projects:
            references.ensure_project(
                tenant_id=values.tenant_id,
                device_id=values.device_id,
                project_id=project.project_id,
                alias=project.alias,
                root=project.root,
            )

        request_signer = Ed25519Signer.from_pem(
            _read_bounded_file(configuration.request_private_key_pem, MAX_KEY_FILE_BYTES)
        )
        verify_gateway_signature = verifier_from_pem_resolver(
            lambda: _read_bounded_file(
                configuration.gateway_dispatch_public_key_pem,
                MAX_KEY_FILE_BYTES,
            )
        )
        tls_context = None if exchange is not None else _build_tls_context(configuration.tls)
        client = WorkerHttpsClient(
            gateway_origin=values.gateway_origin,
            tenant_id=values.tenant_id,
            user_id=values.user_id,
            device_id=values.device_id,
            key_id=values.key_id,
            sign_request=request_signer,
            exchange=exchange,
            tls_context=tls_context,
            timeout_seconds=values.request_timeout_seconds,
        )
        selected_backend = (
            backend
            if backend is not None
            else DefaultLocalReadBackend(configuration.cadplot_config)
        )
        dispatcher = WorkerDispatcher(
            tenant_id=values.tenant_id,
            user_id=values.user_id,
            device_id=values.device_id,
            policy_version=values.policy_version,
            references=references,
            backend=selected_backend,
        )
        runner = WorkerRunner(
            tenant_id=values.tenant_id,
            device_id=values.device_id,
            client=client,
            dispatcher=dispatcher,
            references=references,
            verify_gateway_signature=verify_gateway_signature,
            sign_worker_result=request_signer,
        )
        return WorkerRuntime(
            runner=runner,
            poll_interval_seconds=values.poll_interval_seconds,
        )
    except WorkerCliError:
        raise
    except Exception:
        raise WorkerCliError("worker_start_failed") from None


def run_worker_loop(
    runtime: WorkerRuntime,
    *,
    once: bool,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] | None = None,
) -> int:
    """Run once or poll forever with a configuration-bounded interval."""

    if (
        not isinstance(runtime, WorkerRuntime)
        or not isinstance(once, bool)
        or not MIN_POLL_INTERVAL_SECONDS
        <= runtime.poll_interval_seconds
        <= MAX_POLL_INTERVAL_SECONDS
        or not callable(sleep)
        or (emit is not None and not callable(emit))
    ):
        raise WorkerCliError("worker_start_failed")
    while True:
        try:
            outcome = runtime.runner.run_once()
        except (LocalReferenceError, WorkerCryptoError, WorkerTransportError, ValueError):
            if once:
                raise WorkerCliError("worker_iteration_failed") from None
            if emit is not None:
                emit("worker_iteration_failed")
        else:
            if emit is not None and (once or outcome is not WorkerRunOutcome.IDLE):
                emit(outcome.value)
        if once:
            return 0
        sleep(runtime.poll_interval_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeArgumentParser(
        prog="cadplot-worker",
        description="Run the outbound-only CadPlot Windows workstation worker.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, help="Local worker YAML configuration file")
    parser.add_argument("--once", action="store_true", help="Run one poll/delivery iteration")
    arguments = parser.parse_args(argv)
    try:
        if sys.platform != "win32":
            raise WorkerCliError("windows_worker_required")
        configuration = load_worker_configuration(arguments.config)
        runtime = build_worker_runtime(configuration)
        return run_worker_loop(runtime, once=arguments.once, emit=_emit_safe_event)
    except KeyboardInterrupt:
        return 0
    except WorkerCliError as exc:
        print(f"cadplot-worker: {exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("cadplot-worker: worker_start_failed", file=sys.stderr)
        return 1


def _resolve_tls(base: Path, values: WorkerTlsConfiguration) -> ResolvedWorkerTls:
    ca_bundle = (
        _plain_existing_file(_local_candidate(base, values.ca_bundle_path))
        if values.ca_bundle_path is not None
        else None
    )
    client_certificate = (
        _plain_existing_file(_local_candidate(base, values.client_certificate_path))
        if values.client_certificate_path is not None
        else None
    )
    client_private_key = (
        _plain_existing_file(_local_candidate(base, values.client_private_key_path))
        if values.client_private_key_path is not None
        else None
    )
    for path in (ca_bundle, client_certificate, client_private_key):
        if path is not None and path.stat().st_size > MAX_TLS_FILE_BYTES:
            raise WorkerCliError("worker_config_invalid")
    if client_private_key is not None:
        material = _read_bounded_file(client_private_key, MAX_TLS_FILE_BYTES)
        if b"ENCRYPTED" in material.upper():
            raise WorkerCliError("worker_config_invalid")
    return ResolvedWorkerTls(
        ca_bundle=ca_bundle,
        client_certificate=client_certificate,
        client_private_key=client_private_key,
    )


def _build_tls_context(values: ResolvedWorkerTls) -> ssl.SSLContext:
    try:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(values.ca_bundle) if values.ca_bundle is not None else None,
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        if hasattr(context, "keylog_filename"):
            context.keylog_filename = None
        if values.client_certificate is not None and values.client_private_key is not None:
            context.load_cert_chain(
                certfile=str(values.client_certificate),
                keyfile=str(values.client_private_key),
            )
        return context
    except (OSError, ssl.SSLError, ValueError):
        raise WorkerCliError("worker_start_failed") from None


def _read_bounded_file(path: Path, limit: int) -> bytes:
    try:
        current = _plain_existing_file(path)
        if current.stat().st_size > limit:
            raise WorkerCliError("worker_config_invalid")
        payload = current.read_bytes()
        if (
            not payload
            or len(payload) > limit
            or not _same_path(current, _plain_existing_file(path))
        ):
            raise WorkerCliError("worker_config_invalid")
        return payload
    except WorkerCliError:
        raise
    except OSError:
        raise WorkerCliError("worker_config_unavailable") from None


def _plain_existing_file(path: Path) -> Path:
    try:
        plain = require_plain_directory_path(path)
        resolved = plain.resolve(strict=True)
        if not resolved.is_file() or not _same_path(plain, resolved):
            raise WorkerCliError("worker_config_invalid")
        return resolved
    except WorkerCliError:
        raise
    except (OSError, PathPolicyError, RuntimeError, ValueError):
        raise WorkerCliError("worker_config_unavailable") from None


def _plain_existing_directory(path: Path) -> Path:
    try:
        plain = require_plain_directory_path(path)
        resolved = plain.resolve(strict=True)
        if not resolved.is_dir() or not _same_path(plain, resolved):
            raise WorkerCliError("worker_config_invalid")
        return resolved
    except WorkerCliError:
        raise
    except (OSError, PathPolicyError, RuntimeError, ValueError):
        raise WorkerCliError("worker_config_invalid") from None


def _plain_state_database(path: Path) -> Path:
    try:
        plain = require_plain_directory_path(path)
        parent = plain.parent.resolve(strict=True)
        candidate = parent / plain.name
        if not parent.is_dir() or not _same_path(plain.parent, parent):
            raise WorkerCliError("worker_config_invalid")
        if candidate.exists():
            resolved = candidate.resolve(strict=True)
            if not resolved.is_file() or not _same_path(candidate, resolved):
                raise WorkerCliError("worker_config_invalid")
        return candidate
    except WorkerCliError:
        raise
    except (OSError, PathPolicyError, RuntimeError, ValueError):
        raise WorkerCliError("worker_config_invalid") from None


def _local_candidate(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.absolute()


def _validate_local_path_text(value: str) -> str:
    if (
        value != value.strip()
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("local_path_invalid")
    return value


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _emit_safe_event(code: str) -> None:
    safe = {
        WorkerRunOutcome.IDLE.value,
        WorkerRunOutcome.DELIVERED_PENDING.value,
        WorkerRunOutcome.COMPLETED.value,
        "worker_iteration_failed",
    }
    selected = code if code in safe else "worker_iteration_failed"
    print(f"cadplot-worker: {selected}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
