import base64
import json
import os
import socket
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from apps.supervisor.evidence import (
    DependencyEvidence,
    FirewallProfile,
    FirewallRule,
    Listener,
    dependency_evidence,
    network_evidence,
)
from apps.supervisor.manifest import RestartPolicy, Service
from apps.supervisor.release import RLS_TABLES, ReleasePins
from apps.supervisor.runtime import (
    READ_DATA_RIGHT,
    SUPERVISOR_JOB_SDDL,
    SUPERVISOR_MUTEX_SDDL,
    ChildReadinessError,
    JobObject,
    ManagedProcess,
    ProcessCleanupError,
    RestartBudget,
    RuntimeError,
    SingleInstance,
    Supervisor,
    load_identity_password,
    load_service_environment,
    startup_failure_payload,
    validate_windows_secret_acl,
    windows_tcp_listener_owned_by,
)

PINNED_CADDY = r"C:\Program Files\LocalRAG\caddy\caddy.exe"


class FakeWin32Adapter:
    def __init__(
        self,
        *,
        mutex_exists: bool = False,
        fail_assign: bool = False,
        fail_resume: bool = False,
        fail_terminate: bool = False,
        fail_close: bool = False,
        terminate_failures: int | None = None,
    ) -> None:
        self.mutex_exists = mutex_exists
        self.fail_assign = fail_assign
        self.fail_resume = fail_resume
        self.fail_terminate = fail_terminate
        self.fail_close = fail_close
        self.terminate_failures = (
            3
            if fail_terminate and terminate_failures is None
            else terminate_failures or 0
        )
        self.terminated: set[int] = set()
        self.events: list[object] = []

    def create_mutex(self, name: str, sddl: str) -> tuple[int, bool]:
        self.events.append(("mutex", name, sddl))
        return 10, self.mutex_exists

    def create_job(self, name: str, sddl: str) -> int:
        self.events.append(("job", name, sddl))
        return 20

    def assign_to_job(self, job_handle: int, process_handle: int) -> None:
        self.events.append(("assign", job_handle, process_handle))
        if self.fail_assign:
            raise OSError("assign failed")

    def create_suspended(
        self,
        command: list[str],
        *,
        cwd: str,
        environment: dict[str, str],
        identity_token: int,
    ) -> ManagedProcess:
        self.terminated.discard(30)
        self.events.append(
            ("create_suspended", command, cwd, environment, identity_token)
        )
        return ManagedProcess(30, 31, 32)

    def logon_service(self, identity: str, password: str) -> int:
        self.events.append(("logon_service", identity, password))
        return 40

    def resume(self, process: ManagedProcess) -> None:
        self.events.append(("resume", process.process_handle))
        if self.fail_resume:
            raise OSError("resume failed")

    def poll(self, process: ManagedProcess) -> int | None:
        return 1 if process.process_handle in self.terminated else None

    def terminate(self, process: ManagedProcess) -> None:
        self.events.append(("terminate", process.process_handle))
        if self.terminate_failures:
            self.terminate_failures -= 1
            raise OSError("terminate failed")
        self.terminated.add(process.process_handle)

    def wait(self, process: ManagedProcess, timeout_milliseconds: int) -> bool:
        self.events.append(("wait", process.process_handle, timeout_milliseconds))
        return process.process_handle in self.terminated

    def close_process(self, process: ManagedProcess) -> None:
        self.events.append(("close_process", process.process_handle))
        if self.fail_close:
            raise OSError("close failed")

    def close_handle(self, handle: int) -> None:
        self.events.append(("close_handle", handle))


class RuntimeEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.environment_file = Path(self.temporary.name) / "worker.env"
        self.environment_file.write_text("", encoding="utf-8")
        self.identity_secret_file = Path(self.temporary.name) / "worker.logon.env"
        self.identity_secret_file.write_text(
            "RAG_WINDOWS_ACCOUNT_PASSWORD=long-test-password\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _worker_service(self, arguments: tuple[str, ...] = ()) -> Service:
        return Service(
            "worker",
            "worker.exe",
            arguments,
            ".",
            r".\RagWorkerSvc",
            None,
            None,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
        )

    def _supervisor(
        self,
        service: Service,
        adapter: FakeWin32Adapter,
        *,
        sleeper=lambda _: None,
    ) -> Supervisor:
        return Supervisor(
            (service,),
            RestartPolicy(1, 60, (1,)),
            adapter=adapter,
            sleeper=sleeper,
            acl_validator=lambda *_: None,
            readiness_checker=lambda *_: True,
        )

    def test_restart_budget_is_bounded_and_resets_after_window(self) -> None:
        budget = RestartBudget(RestartPolicy(2, 60, (1, 5)))
        self.assertEqual(budget.record_failure(100).delay_seconds, 1)
        self.assertEqual(budget.record_failure(101).delay_seconds, 5)
        self.assertFalse(budget.record_failure(102).allowed)
        reset = budget.record_failure(161)
        self.assertTrue(reset.allowed)
        self.assertEqual(reset.delay_seconds, 1)

    def test_windows_listener_probe_rejects_stale_wrong_pid(self) -> None:
        if os.name != "nt":
            self.skipTest("native listener ownership is Windows-only")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            address, port = listener.getsockname()
            self.assertFalse(
                windows_tcp_listener_owned_by(address, port, os.getpid() + 1)
            )
            self.assertTrue(windows_tcp_listener_owned_by(address, port, os.getpid()))

    def test_http_readiness_rejects_decoy_listener_not_owned_by_child(self) -> None:
        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                return None

        service = Service(
            "inference",
            "python.exe",
            (),
            ".",
            r".\RagInferenceSvc",
            "127.0.0.1",
            8100,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
            (),
            "http://127.0.0.1:8100/health",
            None,
            3,
        )
        times = iter((0.0, 0.0, 1.0, 2.0, 3.0))
        supervisor = Supervisor(
            (service,),
            RestartPolicy(1, 60, (1,)),
            adapter=FakeWin32Adapter(),
            sleeper=lambda _: None,
            clock=lambda: next(times),
            acl_validator=lambda *_: None,
            listener_owner_checker=lambda *_: False,
        )
        with patch(
            "apps.supervisor.runtime.urllib.request.urlopen", return_value=Response()
        ):
            self.assertFalse(
                supervisor._check_readiness(
                    service,
                    ManagedProcess(30, 0, 555),
                    {},
                    FakeWin32Adapter(),
                )
            )

    def test_listener_owned_by_accepts_direct_child_interpreter(self) -> None:
        if os.name != "nt":
            self.skipTest("native listener ownership is Windows-only")
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import socket,time;"
                    "s=socket.socket();"
                    "s.bind(('127.0.0.1',0));"
                    "s.listen();"
                    "print(s.getsockname()[1],flush=True);"
                    "time.sleep(30)"
                ),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout is not None
            port = int(child.stdout.readline().strip())
            self.assertTrue(
                windows_tcp_listener_owned_by("127.0.0.1", port, os.getpid())
            )
        finally:
            child.terminate()
            child.wait(timeout=10)
            if child.stdout is not None:
                child.stdout.close()

    def test_api_readiness_uses_canonical_host_header(self) -> None:
        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_: object) -> None:
                return None

        service = Service(
            "api",
            "python.exe",
            (),
            ".",
            r".\RagApiSvc",
            "127.0.0.1",
            8443,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
            ("CANONICAL_HOST", "COORDINATOR_SERVICE_TOKEN"),
            "https://127.0.0.1:8443/ready",
            "COORDINATOR_SERVICE_TOKEN",
            3,
        )
        request_headers: dict[str, str | None] = {}

        def open_request(request: object, **_: object) -> Response:
            request_headers["host"] = getattr(request, "get_header")("Host")
            return Response()

        supervisor = Supervisor(
            (service,),
            RestartPolicy(1, 60, (1,)),
            adapter=FakeWin32Adapter(),
            sleeper=lambda _: None,
            clock=lambda: 0.0,
            acl_validator=lambda *_: None,
            listener_owner_checker=lambda *_: True,
        )
        with (
            patch(
                "apps.supervisor.runtime.ssl.create_default_context"
            ) as create_context,
            patch(
                "apps.supervisor.runtime.urllib.request.urlopen",
                side_effect=open_request,
            ),
        ):
            create_context.return_value.load_cert_chain.return_value = None
            self.assertTrue(
                supervisor._check_readiness(
                    service,
                    ManagedProcess(30, 0, 555),
                    {
                        "CANONICAL_HOST": "rag.home.arpa",
                        "COORDINATOR_SERVICE_TOKEN": "t" * 32,
                        "RAG_API_CLIENT_CA_PATH": "ca.crt",
                        "RAG_SUPERVISOR_API_CLIENT_CERT_PATH": "client.crt",
                        "RAG_SUPERVISOR_API_CLIENT_KEY_PATH": "client.key",
                    },
                    FakeWin32Adapter(),
                )
            )
        self.assertEqual(request_headers["host"], "rag.home.arpa")

    def test_caddy_without_http_readiness_rejects_stale_listener(self) -> None:
        service = Service(
            "caddy",
            "caddy.exe",
            ("run", "--config", "Caddyfile"),
            ".",
            r".\RagProxySvc",
            "0.0.0.0",
            443,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
            ("RAG_LAN_IPV4", "RAG_LAN_IPV6"),
            None,
            None,
            2,
        )
        times = iter((0.0, 0.0, 1.0, 2.0))
        supervisor = Supervisor(
            (service,),
            RestartPolicy(1, 60, (1,)),
            adapter=FakeWin32Adapter(),
            sleeper=lambda _: None,
            clock=lambda: next(times),
            acl_validator=lambda *_: None,
            listener_owner_checker=lambda *_: False,
        )
        self.assertFalse(
            supervisor._check_readiness(
                service,
                ManagedProcess(30, 0, 555),
                {
                    "RAG_LAN_IPV4": "192.0.2.10",
                    "RAG_LAN_IPV6": "2001:db8::10",
                },
                FakeWin32Adapter(),
            )
        )

    def test_caddy_listener_rejects_duplicate_wrong_family_and_non_lan_values(
        self,
    ) -> None:
        service = Service(
            "caddy",
            "caddy.exe",
            ("run", "--config", "Caddyfile"),
            ".",
            r".\RagProxySvc",
            "0.0.0.0",
            443,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
            ("RAG_LAN_IPV4", "RAG_LAN_IPV6"),
        )
        for environment, error in (
            (
                {
                    "RAG_LAN_IPV4": "192.168.1.10",
                    "RAG_LAN_IPV6": "192.168.1.10",
                },
                "distinct",
            ),
            (
                {
                    "RAG_LAN_IPV4": "fd00::10",
                    "RAG_LAN_IPV6": "192.168.1.10",
                },
                "IPv4 then IPv6",
            ),
            (
                {
                    "RAG_LAN_IPV4": "8.8.8.8",
                    "RAG_LAN_IPV6": "fd00::10",
                },
                "private non-wildcard",
            ),
            (
                {
                    "RAG_LAN_IPV4": "0.0.0.0",
                    "RAG_LAN_IPV6": "::",
                },
                "private non-wildcard",
            ),
        ):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(RuntimeError, error):
                    Supervisor._listener_addresses(service, environment)

    def test_secret_acl_rejects_create_files_and_append_data_rights(self) -> None:
        child = f"{os.environ['COMPUTERNAME']}\\RagWorkerSvc"

        def evidence(child_rights: int) -> SimpleNamespace:
            rules = [
                {
                    "Identity": "NT AUTHORITY\\SYSTEM",
                    "Type": "Allow",
                    "Inherited": False,
                    "RightsValue": 1,
                },
                {
                    "Identity": "BUILTIN\\Administrators",
                    "Type": "Allow",
                    "Inherited": False,
                    "RightsValue": 0x1F01FF,
                },
                {
                    "Identity": child,
                    "Type": "Allow",
                    "Inherited": False,
                    "RightsValue": child_rights,
                },
            ]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "Owner": "NT AUTHORITY\\SYSTEM",
                        "Protected": True,
                        "Rules": rules,
                        "ParentOwner": "BUILTIN\\Administrators",
                        "ParentProtected": True,
                        "ParentRules": rules,
                    }
                ),
            )

        for dangerous in (0x2, 0x4):
            with (
                self.subTest(right=dangerous),
                patch(
                    "apps.supervisor.runtime.subprocess.run",
                    return_value=evidence(READ_DATA_RIGHT | dangerous),
                ),
                self.assertRaisesRegex(RuntimeError, "write-capable"),
            ):
                validate_windows_secret_acl(
                    self.environment_file,
                    r".\RagWorkerSvc",
                )

    def test_secret_acl_powershell_command_encodes_the_path(self) -> None:
        with (
            patch(
                "apps.supervisor.runtime.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stdout=""),
            ) as run,
            self.assertRaisesRegex(RuntimeError, "inspection failed"),
        ):
            validate_windows_secret_acl(
                self.environment_file,
                r".\RagWorkerSvc",
            )
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-2], "-EncodedCommand")
        self.assertEqual(len(arguments), 5)
        command = base64.b64decode(arguments[-1]).decode("utf-16-le")
        self.assertIn("[Convert]::FromBase64String(", command)
        self.assertNotIn(str(self.environment_file), command)
        self.assertNotIn("$args[0]", command)

    def test_environment_file_is_allowlisted_sanitized_and_identity_bound(self) -> None:
        self.environment_file.write_text(
            "ALLOWED=value\n",
            encoding="utf-8",
        )
        service = Service(
            "worker",
            "worker.exe",
            (),
            ".",
            r".\RagWorkerSvc",
            None,
            None,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
            ("ALLOWED",),
        )
        observed: list[tuple[Path, str]] = []
        environment, password = load_service_environment(
            service,
            acl_validator=lambda path, identity: observed.append((path, identity)),
            inherited={"SystemRoot": r"C:\Windows", "UNSAFE_PARENT": "secret"},
        )
        self.assertEqual(password, "")
        self.assertEqual(
            load_identity_password(service, acl_validator=lambda *_: None),
            "long-test-password",
        )
        self.assertEqual(environment["ALLOWED"], "value")
        self.assertEqual(environment["SystemRoot"], r"C:\Windows")
        self.assertNotIn("UNSAFE_PARENT", environment)
        self.assertNotIn("RAG_WINDOWS_ACCOUNT_PASSWORD", environment)
        self.assertEqual(observed[0][1], r".\RagWorkerSvc")

    def test_environment_file_rejects_unknown_duplicate_and_oversize_values(
        self,
    ) -> None:
        service = Service(
            "worker",
            "worker.exe",
            (),
            ".",
            r".\RagWorkerSvc",
            None,
            None,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
            ("ALLOWED",),
        )
        for content, error in (
            (
                "ALLOWED=1\nUNKNOWN=2\n",
                "unknown key",
            ),
            (
                "ALLOWED=1\nALLOWED=2\n",
                "duplicate key",
            ),
        ):
            with self.subTest(error=error):
                self.environment_file.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, error):
                    load_service_environment(
                        service,
                        acl_validator=lambda *_: None,
                        inherited={},
                    )
        self.environment_file.write_bytes(b"A" * (64 * 1024 + 1))
        with self.assertRaisesRegex(RuntimeError, "bounded"):
            load_service_environment(
                service,
                acl_validator=lambda *_: None,
                inherited={},
            )

    def test_environment_file_rejects_reparse_points(self) -> None:
        target = Path(self.temporary.name) / "target.env"
        target.write_text(
            "",
            encoding="utf-8",
        )
        link = Path(self.temporary.name) / "link.env"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        service = Service(
            "worker",
            "worker.exe",
            (),
            ".",
            r".\RagWorkerSvc",
            None,
            None,
            (),
            str(link),
            str(self.identity_secret_file),
        )
        with self.assertRaisesRegex(RuntimeError, "non-reparse"):
            load_service_environment(
                service,
                acl_validator=lambda *_: None,
                inherited={},
            )

    def test_dependency_cycle_is_rejected(self) -> None:
        first = Service("a", "a.exe", (), ".", "a", None, None, ("b",), None)
        second = Service("b", "b.exe", (), ".", "b", None, None, ("a",), None)
        supervisor = Supervisor((first, second), RestartPolicy(1, 60, (1,)))
        with self.assertRaisesRegex(RuntimeError, "cycle"):
            supervisor._ordered_services()

    def test_global_mutex_uses_safe_name_and_explicit_acl(self) -> None:
        adapter = FakeWin32Adapter()
        with SingleInstance("rag-v4", adapter) as instance:
            self.assertEqual(instance.name, r"Global\LocalRagSupervisor-rag-v4")
        self.assertEqual(
            adapter.events[0],
            ("mutex", r"Global\LocalRagSupervisor-rag-v4", SUPERVISOR_MUTEX_SDDL),
        )
        self.assertEqual(adapter.events[-1], ("close_handle", 10))
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            SingleInstance(r"..\unsafe", adapter)

    def test_existing_global_mutex_fails_and_closes_handle(self) -> None:
        adapter = FakeWin32Adapter(mutex_exists=True)
        with self.assertRaisesRegex(RuntimeError, "machine-wide"):
            with SingleInstance("rag-v4", adapter):
                self.fail("mutex should not be acquired")
        self.assertEqual(adapter.events[-1], ("close_handle", 10))

    def test_global_job_uses_query_only_administrator_acl(self) -> None:
        adapter = FakeWin32Adapter()
        with JobObject("rag-v4", adapter) as job:
            self.assertEqual(
                job.name,
                r"Global\LocalRagSupervisorJob-rag-v4",
            )
        self.assertEqual(
            adapter.events[0],
            (
                "job",
                r"Global\LocalRagSupervisorJob-rag-v4",
                SUPERVISOR_JOB_SDDL,
            ),
        )
        self.assertIn("(A;;GA;;;SY)", SUPERVISOR_JOB_SDDL)
        self.assertIn("(A;;0x0004;;;BA)", SUPERVISOR_JOB_SDDL)
        self.assertNotIn("(A;;GA;;;BA)", SUPERVISOR_JOB_SDDL)
        self.assertEqual(adapter.events[-1], ("close_handle", 20))

    def test_process_is_assigned_while_suspended_before_resume(self) -> None:
        adapter = FakeWin32Adapter()
        service = self._worker_service(("--run",))
        supervisor = self._supervisor(service, adapter)
        with JobObject("rag-v4", adapter) as job:
            process = supervisor._start(service, adapter, job)
        self.assertEqual(process.process_handle, 30)
        event_names = [event[0] for event in adapter.events if isinstance(event, tuple)]
        self.assertLess(
            event_names.index("create_suspended"), event_names.index("assign")
        )
        self.assertLess(event_names.index("assign"), event_names.index("resume"))
        created = next(
            event
            for event in adapter.events
            if isinstance(event, tuple) and event[0] == "create_suspended"
        )
        self.assertEqual(created[-1], 40)
        self.assertNotIn("RAG_WINDOWS_ACCOUNT_PASSWORD", created[3])
        self.assertIn(
            ("logon_service", r".\RagWorkerSvc", "long-test-password"),
            adapter.events,
        )

    def test_assign_or_resume_failure_terminates_and_closes_suspended_process(
        self,
    ) -> None:
        service = self._worker_service()
        for failure in ("assign", "resume"):
            with self.subTest(failure=failure):
                adapter = FakeWin32Adapter(
                    fail_assign=failure == "assign",
                    fail_resume=failure == "resume",
                )
                supervisor = self._supervisor(service, adapter)
                with JobObject("rag-v4", adapter) as job:
                    with self.assertRaises(OSError):
                        supervisor._start(service, adapter, job)
                names = [
                    event[0] for event in adapter.events if isinstance(event, tuple)
                ]
                self.assertIn("terminate", names)
                self.assertIn("close_process", names)

    def test_cleanup_failure_retains_and_reports_suspended_process_handles(
        self,
    ) -> None:
        service = self._worker_service()
        for failure in ("terminate", "close"):
            with self.subTest(failure=failure):
                adapter = FakeWin32Adapter(
                    fail_assign=True,
                    fail_terminate=failure == "terminate",
                    fail_close=failure == "close",
                )
                supervisor = self._supervisor(service, adapter)
                with JobObject("rag-v4", adapter) as job:
                    with self.assertRaises(ProcessCleanupError) as raised:
                        supervisor._start(service, adapter, job)
                self.assertEqual(raised.exception.process.process_handle, 30)
                self.assertEqual(raised.exception.process.thread_handle, 31)
                self.assertIn("process=30", str(raised.exception))
                names = [
                    event[0] for event in adapter.events if isinstance(event, tuple)
                ]
                if failure == "terminate":
                    self.assertNotIn("close_process", names)
                    self.assertEqual(names.count("terminate"), 3)
                    adapter.terminate_failures = 0
                    supervisor.reap_retained(adapter)
                    self.assertEqual(supervisor._retained_processes, [])
                    self.assertIn(
                        "close_process",
                        [
                            event[0]
                            for event in adapter.events
                            if isinstance(event, tuple)
                        ],
                    )
                else:
                    self.assertIn("close_process", names)

    def test_failed_termination_is_retried_and_reaped_without_orphaning(self) -> None:
        adapter = FakeWin32Adapter(fail_assign=True, terminate_failures=2)
        service = self._worker_service()
        supervisor = self._supervisor(service, adapter)
        with JobObject("rag-v4", adapter) as job:
            with self.assertRaises(OSError):
                supervisor._start(service, adapter, job)
        names = [event[0] for event in adapter.events if isinstance(event, tuple)]
        self.assertEqual(names.count("terminate"), 3)
        self.assertEqual(names.count("wait"), 1)
        self.assertIn("close_process", names)
        self.assertEqual(supervisor._retained_processes, [])

    def test_dependency_failure_cascade_stops_dependants_in_reverse_order(
        self,
    ) -> None:
        adapter = FakeWin32Adapter()
        database_side = Service(
            "inference",
            "inference.exe",
            (),
            ".",
            r".\RagInferenceSvc",
            None,
            None,
            (),
            str(self.environment_file),
            str(self.identity_secret_file),
        )
        worker = Service(
            "worker",
            "worker.exe",
            (),
            ".",
            r".\RagWorkerSvc",
            None,
            None,
            ("inference",),
            str(self.environment_file),
            str(self.identity_secret_file),
        )
        supervisor = Supervisor(
            (database_side, worker),
            RestartPolicy(1, 60, (1,)),
            adapter=adapter,
            sleeper=lambda _: None,
        )
        processes = {
            "inference": ManagedProcess(50, 51, 52),
            "worker": ManagedProcess(60, 61, 62),
        }
        self.assertIsNone(supervisor._stop_cascade(processes, adapter))
        terminated = [
            event[1]
            for event in adapter.events
            if isinstance(event, tuple) and event[0] == "terminate"
        ]
        self.assertEqual(terminated, [60, 50])
        self.assertEqual(processes, {})

    def test_startup_readiness_failures_charge_service_budget_and_retry_graph(
        self,
    ) -> None:
        adapter = FakeWin32Adapter()
        service = self._worker_service()
        diagnostic = Path(self.temporary.name) / "startup-failure.json"
        supervisor = Supervisor(
            (service,),
            RestartPolicy(2, 60, (1, 2)),
            adapter=adapter,
            sleeper=lambda _: None,
            clock=iter((1.0, 2.0, 3.0)).__next__,
            acl_validator=lambda *_: None,
            readiness_checker=lambda *_: False,
            startup_diagnostic_path=diagnostic,
        )
        with self.assertRaisesRegex(RuntimeError, "startup restart budget"):
            supervisor.run("rag-v4")
        event_names = [event[0] for event in adapter.events if isinstance(event, tuple)]
        self.assertEqual(event_names.count("create_suspended"), 3)
        self.assertEqual(event_names.count("assign"), 3)
        self.assertEqual(event_names.count("resume"), 3)
        self.assertEqual(event_names.count("terminate"), 3)
        self.assertEqual(event_names.count("wait"), 3)
        self.assertEqual(
            json.loads(diagnostic.read_text(encoding="utf-8")),
            {
                "schema_version": 1,
                "service": "worker",
                "exception_chain": [{"type": "ChildReadinessError"}],
            },
        )

    def test_startup_failure_payload_omits_exception_messages(self) -> None:
        secret = "password-that-must-not-appear"
        try:
            raise OSError(13, secret)
        except OSError as cause:
            error = ChildReadinessError("inference", 1)
            error.__cause__ = cause

        payload = startup_failure_payload("inference", error)
        encoded = json.dumps(payload)

        self.assertNotIn(secret, encoded)
        self.assertEqual(
            payload["exception_chain"],
            [
                {"type": "ChildReadinessError", "exit_code": 1},
                {"type": "PermissionError", "errno": 13},
            ],
        )

    def _valid_rule(self) -> FirewallRule:
        return FirewallRule(
            "Local RAG HTTPS",
            True,
            "Inbound",
            "Allow",
            "Private",
            "TCP",
            "443",
            PINNED_CADDY,
            "Any",
            "192.168.1.10,fd00::10",
            "LocalSubnet",
            "Wired,Wireless",
            "Block",
        )

    def test_network_evidence_requires_exact_scoped_caddy_rule(self) -> None:
        result = network_evidence(
            [
                Listener("192.168.1.10", 443, "caddy", PINNED_CADDY, 42, 7, True),
                Listener("fd00::10", 443, "caddy", PINNED_CADDY, 42, 7, True),
                Listener("127.0.0.1", 3000, "node", "node.exe"),
                Listener("::1", 8443, "python", "python.exe"),
            ],
            [self._valid_rule()],
            [
                FirewallProfile("Private", True, "Block"),
                FirewallProfile("Public", True, "Block"),
            ],
            pinned_caddy_program=PINNED_CADDY,
            expected_local_addresses=("192.168.1.10", "fd00::10"),
            supervisor_process_id=7,
        )
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["second_lan_device"], "unverified")

    def test_network_evidence_fails_missing_duplicate_and_public_rules(self) -> None:
        public = FirewallRule(
            "Unsafe",
            True,
            "Inbound",
            "Allow",
            "Public",
            "TCP",
            "Any",
            "Any",
            "Any",
            "Any",
            "Any",
            "Any",
            "Allow",
        )
        result = network_evidence(
            [Listener("::", 8000, "python", "python.exe")],
            [self._valid_rule(), self._valid_rule(), public],
            [
                FirewallProfile("Private", True, "Block"),
                FirewallProfile("Public", True, "Block"),
            ],
            pinned_caddy_program=PINNED_CADDY,
            expected_local_addresses=("192.168.1.10", "fd00::10"),
        )
        self.assertEqual(result["result"], "fail")
        self.assertTrue(
            any("exactly one" in item for item in result["findings"]),
            result["findings"],
        )
        self.assertTrue(
            any("overlapping" in item for item in result["findings"]),
            result["findings"],
        )

    def test_network_evidence_rejects_private_any_and_multiport_overlap(self) -> None:
        overlapping = [
            FirewallRule(
                "Private Any Program",
                True,
                "Inbound",
                "Allow",
                "Private",
                "Any",
                "Any",
                "Any",
                "Any",
                "Any",
                "Any",
                "Any",
                "Allow",
            ),
            FirewallRule(
                "Private Multiport",
                True,
                "Inbound",
                "Allow",
                "Private",
                "TCP",
                "400-500,8443",
                r"C:\other.exe",
                "Any",
                "10.0.0.1",
                "Any",
                "Any",
                "Allow",
            ),
        ]
        result = network_evidence(
            [
                Listener("0.0.0.0", 443, "caddy", PINNED_CADDY, 42, 7, True),
                Listener("::", 443, "caddy", PINNED_CADDY, 42, 7, True),
            ],
            [self._valid_rule(), *overlapping],
            [
                FirewallProfile("Private", True, "Block"),
                FirewallProfile("Public", True, "Block"),
            ],
            pinned_caddy_program=PINNED_CADDY,
            expected_local_addresses=("192.168.1.10", "fd00::10"),
            supervisor_process_id=7,
        )
        self.assertEqual(result["result"], "fail")
        self.assertEqual(
            sum("overlapping inbound" in item for item in result["findings"]), 2
        )

    def test_network_evidence_rejects_listener_or_profile_mismatch(self) -> None:
        result = network_evidence(
            [
                Listener("0.0.0.0", 443, "caddy", PINNED_CADDY, 42, 7, True),
                Listener("::", 443, "caddy", PINNED_CADDY, 42, 7, True),
            ],
            [self._valid_rule()],
            [
                FirewallProfile("Private", True, "Block"),
                FirewallProfile("Public", False, "Allow"),
            ],
            pinned_caddy_program=PINNED_CADDY,
            expected_local_addresses=("192.168.1.10", "fd00::10"),
            supervisor_process_id=7,
        )
        self.assertEqual(result["result"], "fail")
        self.assertTrue(any("exactly match" in item for item in result["findings"]))
        self.assertTrue(any("Public firewall" in item for item in result["findings"]))

    def test_dependency_evidence_checks_exact_versions_and_security(self) -> None:
        expected_models = {
            "qwen3:8b": "a" * 64,
            "qwen3-embedding:0.6b": "b" * 64,
        }
        release = ReleasePins(
            "e" * 64,
            "sha256:" + "c" * 64,
            "sha256:" + "d" * 64,
            "rag-originals",
            "dependency-probe.bin",
            "f" * 64,
            expected_models,
            "3.7.0",
            "1" * 64,
            "2" * 64,
            "4" * 64,
            "5" * 64,
            "6" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            "f" * 64,
            "7" * 64,
            "8" * 64,
            "9" * 64,
            1,
            "a" * 64,
            "b" * 64,
            900,
        )
        captured = datetime.now(UTC)
        evidence = DependencyEvidence(
            release.manifest_sha256,
            release.docker_executable_sha256,
            "sha256:" + "c" * 64,
            True,
            "0006_versioned_claim",
            RLS_TABLES,
            RLS_TABLES,
            "sha256:" + "d" * 64,
            True,
            "f" * 64,
            True,
            True,
            True,
            True,
            expected_models,
            1024,
            "BAAI/bge-reranker-v2-m3",
            "cpu",
            True,
            "b" * 64,
            "3.7.0",
            "1.6",
            "cpu",
            True,
            "1" * 64,
            "7" * 64,
            "8" * 64,
            "9" * 64,
            1,
            "a" * 64,
            captured,
            "2" * 64,
            "4" * 64,
            "5" * 64,
        )
        result = dependency_evidence(
            evidence,
            release=release,
            now=captured + timedelta(seconds=30),
        )
        self.assertEqual(result["result"], "pass")
        failed = dependency_evidence(
            DependencyEvidence(
                "0" * 64,
                "0" * 64,
                evidence.postgres_image_digest,
                False,
                evidence.alembic_revision,
                (),
                (),
                evidence.rustfs_image_digest,
                True,
                False,
                False,
                False,
                False,
                False,
                expected_models,
                768,
                "wrong",
                "gpu",
                False,
                "0" * 64,
                evidence.paddleocr_version,
                "1.5",
                "gpu",
                False,
                "0" * 64,
                "invalid",
                "0" * 64,
                "0" * 64,
                0,
                "0" * 64,
                captured - timedelta(hours=1),
                "0" * 64,
                "0" * 64,
                "0" * 64,
            ),
            release=release,
            now=captured,
        )
        self.assertEqual(failed["result"], "fail")
        self.assertGreaterEqual(len(failed["findings"]), 6)
