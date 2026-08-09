using CadPlotMcp.Core;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using Xunit;

namespace CadPlotMcp.Core.Tests;

public sealed class PublishJobTests : IDisposable
{
    private readonly string _workspaceRoot;
    private readonly string _stateRoot;
    private readonly string _authenticationKeyPath;
    private readonly string _jobRoot;
    private readonly PublishJobRequest _request;

    public PublishJobTests()
    {
        _workspaceRoot = Path.Combine(
            Path.GetTempPath(),
            "cadplot-tests-" + Guid.NewGuid().ToString("N")
        );
        _stateRoot = Path.Combine(
            Path.GetTempPath(),
            "cadplot-state-tests-" + Guid.NewGuid().ToString("N")
        );
        Directory.CreateDirectory(_stateRoot);
        _authenticationKeyPath = Path.Combine(_stateRoot, "queue-auth-key-v1.bin");
        _jobRoot = Path.Combine(
            _workspaceRoot,
            "job-20260809T120000000000Z-" + Guid.NewGuid().ToString("N")[..12]
        );
        var source = Path.Combine(_jobRoot, "source");
        var output = Path.Combine(_jobRoot, "output");
        Directory.CreateDirectory(source);
        Directory.CreateDirectory(output);
        var manifest = Path.Combine(_jobRoot, "manifest.json");
        var drawing = Path.Combine(source, "sheet.dwg");
        File.WriteAllText(drawing, "synthetic");
        _request = new PublishJobRequest
        {
            PlanId = "sha256:" + new string('a', 64),
            ManifestPath = manifest,
            StagedDrawing = drawing,
            OutputDirectory = output,
            SheetCount = 2,
        };
        WriteManifest();
    }

    [Fact]
    public void ValidJobMovesThroughExpectedStates()
    {
        var queue = new PublishJobQueue(_workspaceRoot, 5, _authenticationKeyPath);

        Assert.True(queue.TryEnqueue(_request, out var error));
        Assert.Null(error);
        Assert.Equal(5, queue.Capacity);
        Assert.Equal(1, queue.PendingCount);
        Assert.Equal(0, queue.RunningCount);
        Assert.Equal(4, queue.AvailableCount);
        Assert.Equal(0, queue.GetTelemetry().RecoveredOnStartup);
        Assert.Equal(0, queue.GetTelemetry().InterruptedOnStartup);
        Assert.Equal(
            PublishQueueJournal.AuthenticationScheme,
            queue.GetTelemetry().Authentication
        );
        Assert.Equal(PublishJobState.Pending, queue.GetStatus(_request.PlanId)!.State);
        Assert.True(File.Exists(Path.Combine(_jobRoot, PublishQueueJournal.PendingFileName)));
        using (var pending = JsonDocument.Parse(
            File.ReadAllText(Path.Combine(_jobRoot, PublishQueueJournal.PendingFileName))
        ))
        {
            Assert.Equal(1, pending.RootElement.GetProperty("authentication_version").GetInt32());
            Assert.Equal(
                32,
                Convert.FromBase64String(
                    pending.RootElement.GetProperty("authentication_tag").GetString()!
                ).Length
            );
        }
        Assert.False(File.Exists(Path.Combine(_jobRoot, PublishQueueJournal.StartedFileName)));
        Assert.True(queue.TryStartNext(out var started, out var startError));
        Assert.Null(startError);
        Assert.Same(_request, started);
        Assert.True(File.Exists(Path.Combine(_jobRoot, PublishQueueJournal.StartedFileName)));
        Assert.Equal(0, queue.PendingCount);
        Assert.Equal(1, queue.RunningCount);
        Assert.Equal(5, queue.AvailableCount);
        Assert.Equal(PublishJobState.Running, queue.GetStatus(_request.PlanId)!.State);

        queue.Complete(_request.PlanId, succeeded: true);

        Assert.Equal(0, queue.RunningCount);
        Assert.Equal(PublishJobState.Succeeded, queue.GetStatus(_request.PlanId)!.State);
    }

    [Fact]
    public void PendingApprovedIntentRecoversAfterRestartWithExactIdentity()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));

        var recovered = NewQueue();
        var telemetry = recovered.GetTelemetry();

        Assert.Equal(1, telemetry.Pending);
        Assert.Equal(1, telemetry.RecoveredOnStartup);
        Assert.Equal(0, telemetry.InterruptedOnStartup);
        Assert.Equal(PublishJobState.Pending, recovered.GetStatus(_request.PlanId)!.State);
        Assert.True(recovered.TryStartNext(out var started, out var error));
        Assert.Null(error);
        Assert.NotSame(_request, started);
        Assert.Equal(_request.PlanId, started.PlanId);
        Assert.Equal(_request.ManifestSha256, started.ManifestSha256);
        Assert.Equal(_request.ManifestPath, started.ManifestPath);
        Assert.Equal(_request.StagedDrawing, started.StagedDrawing);
        Assert.Equal(_request.OutputDirectory, started.OutputDirectory);
        Assert.Equal(_request.SheetCount, started.SheetCount);
    }

    [Fact]
    public void PendingCancellationIsDurableAndNeverReplayed()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));

        Assert.True(original.TryCancelPending(
            _request.PlanId,
            _request.ManifestSha256,
            out var cancelError
        ));
        Assert.Null(cancelError);
        Assert.Equal(0, original.PendingCount);
        Assert.Equal(PublishJobState.Cancelled, original.GetStatus(_request.PlanId)!.State);
        var markerPath = Path.Combine(_jobRoot, PublishQueueJournal.CancelledFileName);
        Assert.True(File.Exists(markerPath));
        using (var marker = JsonDocument.Parse(File.ReadAllText(markerPath)))
        {
            Assert.Equal(1, marker.RootElement.GetProperty("authentication_version").GetInt32());
            Assert.Equal(
                32,
                Convert.FromBase64String(
                    marker.RootElement.GetProperty("authentication_tag").GetString()!
                ).Length
            );
        }

        var recovered = NewQueue();
        var telemetry = recovered.GetTelemetry();
        Assert.Equal(0, telemetry.Pending);
        Assert.Equal(0, telemetry.RecoveredOnStartup);
        Assert.Equal(1, telemetry.CancelledOnStartup);
        Assert.Equal(PublishJobState.Cancelled, recovered.GetStatus(_request.PlanId)!.State);
        Assert.False(recovered.TryStartNext(out _, out var startError));
        Assert.Equal("no_pending_job", startError);
    }

    [Fact]
    public void PendingCancellationIsExactAndIdempotent()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));

        Assert.False(queue.TryCancelPending(
            _request.PlanId,
            new string('b', 64),
            out var mismatch
        ));
        Assert.Equal("manifest_mismatch", mismatch);
        Assert.Equal(1, queue.PendingCount);

        Assert.True(queue.TryCancelPending(
            _request.PlanId,
            _request.ManifestSha256,
            out var firstError
        ));
        Assert.Null(firstError);
        Assert.True(queue.TryCancelPending(
            _request.PlanId,
            _request.ManifestSha256,
            out var retryError
        ));
        Assert.Null(retryError);
        Assert.Equal(0, queue.PendingCount);
    }

    [Fact]
    public void RunningJobCannotBeCancelled()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        Assert.True(queue.TryStartNext(out _, out _));

        Assert.False(queue.TryCancelPending(
            _request.PlanId,
            _request.ManifestSha256,
            out var error
        ));
        Assert.Equal("job_not_pending", error);
        Assert.Equal(PublishJobState.Running, queue.GetStatus(_request.PlanId)!.State);
        Assert.False(File.Exists(Path.Combine(_jobRoot, PublishQueueJournal.CancelledFileName)));
    }

    [Fact]
    public void TamperedCancelledIntentDisablesRecovery()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));
        Assert.True(original.TryCancelPending(
            _request.PlanId,
            _request.ManifestSha256,
            out _
        ));
        var markerPath = Path.Combine(_jobRoot, PublishQueueJournal.CancelledFileName);
        var marker = JsonNode.Parse(File.ReadAllText(markerPath))!.AsObject();
        marker["cancelled_utc"] = DateTime.UtcNow.AddMinutes(1).ToString("o");
        File.WriteAllText(markerPath, marker.ToJsonString());

        var exception = Assert.Throws<InvalidDataException>(() => NewQueue());

        Assert.Contains("publish_queue_cancelled_authentication_failed", exception.Message);
    }

    [Fact]
    public void InterruptedRunningIntentIsNeverAutomaticallyReplayed()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));
        Assert.True(original.TryStartNext(out _, out _));

        var recovered = NewQueue();
        var telemetry = recovered.GetTelemetry();
        var status = recovered.GetStatus(_request.PlanId)!;

        Assert.Equal(0, telemetry.Pending);
        Assert.Equal(0, telemetry.RecoveredOnStartup);
        Assert.Equal(1, telemetry.InterruptedOnStartup);
        Assert.Equal(PublishJobState.Failed, status.State);
        Assert.Equal("job_interrupted", status.Error);
        Assert.False(recovered.TryStartNext(out _, out var error));
        Assert.Equal("no_pending_job", error);
    }

    [Fact]
    public void TerminalReceiptRestoresStatusAfterRestart()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));
        Assert.True(original.TryStartNext(out _, out _));
        var writer = new PublishReceiptWriter(_workspaceRoot);
        Assert.Null(writer.Write(_request, PublishExecutionResult.Success()));
        original.Complete(_request.PlanId, succeeded: true);

        var recovered = NewQueue();
        var status = recovered.GetStatus(_request.PlanId)!;

        Assert.Equal(0, recovered.PendingCount);
        Assert.Equal(PublishJobState.Succeeded, status.State);
        Assert.Null(status.Error);
        Assert.False(recovered.TryEnqueue(_request, out var duplicate));
        Assert.Equal("duplicate_plan", duplicate);
    }

    [Fact]
    public void TamperedPendingIntentDisablesRecovery()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));
        var recordPath = Path.Combine(_jobRoot, PublishQueueJournal.PendingFileName);
        var root = JsonNode.Parse(File.ReadAllText(recordPath))!.AsObject();
        root["sheet_count"] = 1;
        File.WriteAllText(recordPath, root.ToJsonString());

        var exception = Assert.Throws<InvalidDataException>(() => NewQueue());

        Assert.Contains("publish_queue_request_authentication_failed", exception.Message);
    }

    [Fact]
    public void ExistingReceiptCannotBeQueuedAsFreshWork()
    {
        var writer = new PublishReceiptWriter(_workspaceRoot);
        Assert.Null(writer.Write(_request, PublishExecutionResult.Success()));
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("job_already_completed", error);
        Assert.Equal(0, queue.PendingCount);
    }

    [Fact]
    public void TamperedStartStateFailsClosedWithoutRepeatedExecution()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        var recordPath = Path.Combine(_jobRoot, PublishQueueJournal.PendingFileName);
        var root = JsonNode.Parse(File.ReadAllText(recordPath))!.AsObject();
        root["sheet_count"] = 1;
        File.WriteAllText(recordPath, root.ToJsonString());

        Assert.False(queue.TryStartNext(out var request, out var error));
        Assert.Null(request);
        Assert.Equal("queue_state_authentication_failed", error);
        Assert.Equal(0, queue.PendingCount);
        Assert.Equal(PublishJobState.Failed, queue.GetStatus(_request.PlanId)!.State);
        Assert.Equal("queue_state_authentication_failed", queue.GetStatus(_request.PlanId)!.Error);
        Assert.False(queue.TryStartNext(out _, out var repeatedError));
        Assert.Equal("no_pending_job", repeatedError);
    }

    [Fact]
    public void ForgedUnsignedPendingIntentCannotAuthorizeRestart()
    {
        _ = NewQueue();
        var unsignedRecord = new
        {
            schema_version = 1,
            plan_id = _request.PlanId,
            manifest_path = _request.ManifestPath,
            manifest_sha256 = _request.ManifestSha256,
            staged_drawing = _request.StagedDrawing,
            output_directory = _request.OutputDirectory,
            sheet_count = _request.SheetCount,
            queued_utc = DateTime.UtcNow.ToString("o"),
            authentication_version = 1,
        };
        File.WriteAllText(
            Path.Combine(_jobRoot, PublishQueueJournal.PendingFileName),
            JsonSerializer.Serialize(unsignedRecord)
        );

        var exception = Assert.Throws<InvalidDataException>(() => NewQueue());

        Assert.Contains("publish_queue_request_authentication_failed", exception.Message);
    }

    [Fact]
    public void ForeignProtectedKeyCannotAuthorizeRestart()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));
        var foreignState = Path.Combine(
            Path.GetTempPath(),
            "cadplot-foreign-state-" + Guid.NewGuid().ToString("N")
        );
        Directory.CreateDirectory(foreignState);
        try
        {
            var foreignKey = Path.Combine(foreignState, "queue-auth-key-v1.bin");

            var exception = Assert.Throws<InvalidDataException>(() =>
                new PublishJobQueue(_workspaceRoot, 5, foreignKey)
            );

            Assert.Contains("publish_queue_request_authentication_failed", exception.Message);
        }
        finally
        {
            Directory.Delete(foreignState, recursive: true);
        }
    }

    [Fact]
    public void TamperedStartedIntentDisablesRecovery()
    {
        var original = NewQueue();
        Assert.True(original.TryEnqueue(_request, out _));
        Assert.True(original.TryStartNext(out _, out _));
        var startedPath = Path.Combine(_jobRoot, PublishQueueJournal.StartedFileName);
        var started = JsonNode.Parse(File.ReadAllText(startedPath))!.AsObject();
        started["started_utc"] = DateTime.UtcNow.AddMinutes(1).ToString("o");
        File.WriteAllText(startedPath, started.ToJsonString());

        var exception = Assert.Throws<InvalidDataException>(() => NewQueue());

        Assert.Contains("publish_queue_started_authentication_failed", exception.Message);
    }

    [Fact]
    public void AuthenticationKeyIsDpapiProtectedOutsideWorkspace()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        var keyBytes = File.ReadAllBytes(_authenticationKeyPath);
        var header = System.Text.Encoding.ASCII.GetBytes("CADPLOT_QUEUE_KEY_V1\n");

        Assert.False(
            Path.GetFullPath(_authenticationKeyPath).StartsWith(
                Path.GetFullPath(_workspaceRoot) + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase
            )
        );
        Assert.True(keyBytes.Length > header.Length + 32);
        Assert.True(keyBytes.Take(header.Length).SequenceEqual(header));
        Assert.Equal(
            PublishQueueJournal.AuthenticationScheme,
            queue.GetTelemetry().Authentication
        );
    }

    [Fact]
    public void AuthenticationKeyInsideWorkspaceIsRejected()
    {
        var insideWorkspace = Path.Combine(_workspaceRoot, "queue-auth-key-v1.bin");

        var exception = Assert.Throws<ArgumentException>(() =>
            new PublishJobQueue(_workspaceRoot, 5, insideWorkspace)
        );

        Assert.Contains("outside the trusted workspace", exception.Message);
        Assert.False(File.Exists(insideWorkspace));
    }

    [Fact]
    public void CorruptAuthenticationKeyDisablesInitialization()
    {
        _ = NewQueue();
        File.WriteAllText(_authenticationKeyPath, "corrupt");

        var exception = Assert.Throws<InvalidDataException>(() => NewQueue());

        Assert.Contains("queue_auth_key_size_invalid", exception.Message);
    }

    [Fact]
    public void DuplicatePlanIsRejected()
    {
        var queue = new PublishJobQueue(_workspaceRoot, 5, _authenticationKeyPath);
        Assert.True(queue.TryEnqueue(_request, out _));

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("duplicate_plan", error);
    }

    [Fact]
    public void QueueCapacityIsEnforced()
    {
        var queue = new PublishJobQueue(_workspaceRoot, 1, _authenticationKeyPath);
        Assert.True(queue.TryEnqueue(_request, out _));

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("queue_full", error);
    }

    [Fact]
    public void DrawingOutsideJobIsRejected()
    {
        _request.StagedDrawing = Path.Combine(Path.GetTempPath(), "outside.dwg");
        var queue = new PublishJobQueue(_workspaceRoot, 5, _authenticationKeyPath);

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("drawing_outside_job", error);
    }

    [Fact]
    public void FailedJobKeepsAuditStatusAndCannotBeRepeated()
    {
        var queue = new PublishJobQueue(_workspaceRoot, 5, _authenticationKeyPath);
        Assert.True(queue.TryEnqueue(_request, out _));
        Assert.True(queue.TryStartNext(out _, out _));

        queue.Complete(_request.PlanId, succeeded: false, error: "plot_failed");

        var status = queue.GetStatus(_request.PlanId)!;
        Assert.Equal(PublishJobState.Failed, status.State);
        Assert.Equal("plot_failed", status.Error);
        Assert.False(queue.TryEnqueue(_request, out var duplicate));
        Assert.Equal("duplicate_plan", duplicate);
    }

    [Fact]
    public void JobOutsideTrustedWorkspaceIsRejected()
    {
        var otherWorkspace = Path.Combine(Path.GetTempPath(), "cadplot-other-" + Guid.NewGuid());
        Directory.CreateDirectory(otherWorkspace);
        try
        {
            var queue = new PublishJobQueue(otherWorkspace, 5, _authenticationKeyPath);

            Assert.False(queue.TryEnqueue(_request, out var error));
            Assert.Equal("job_outside_workspace", error);
        }
        finally
        {
            Directory.Delete(otherWorkspace);
        }
    }

    [Fact]
    public void QueueRequiresExistingTrustedWorkspace()
    {
        var missing = Path.Combine(Path.GetTempPath(), "cadplot-missing-" + Guid.NewGuid());

        Assert.Throws<ArgumentException>(() =>
            new PublishJobQueue(missing, 5, _authenticationKeyPath)
        );
    }

    [Fact]
    public void ManifestPlanMismatchIsRejected()
    {
        _request.PlanId = "sha256:" + new string('b', 64);
        var queue = new PublishJobQueue(_workspaceRoot, 5, _authenticationKeyPath);

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("manifest_plan_mismatch", error);
    }

    [Fact]
    public void ManifestPdfEscapeIsRejected()
    {
        WriteManifest(firstPdf: Path.Combine(Path.GetTempPath(), "outside.pdf"));
        var queue = new PublishJobQueue(_workspaceRoot, 5, _authenticationKeyPath);

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("pdf_outside_job", error);
    }

    [Fact]
    public void ManifestRejectsTemplateLayoutThatOverwritesItsSource()
    {
        WriteManifest(templateLayout: "CADPLOT_0001_A1");
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("invalid_template_layout", error);
    }

    [Fact]
    public void ManifestAcceptsBoundedUnicodeTemplateLayoutName()
    {
        WriteManifest(templateLayout: "Şirket Paftası 70x100");
        var queue = NewQueue();

        Assert.True(queue.TryEnqueue(_request, out var error));
        Assert.Null(error);
    }

    [Fact]
    public void ManifestAcceptsHashBoundStagedExternalTemplate()
    {
        AddExternalTemplateToManifest();
        var queue = NewQueue();

        Assert.True(queue.TryEnqueue(_request, out var error));
        Assert.Null(error);
    }

    [Fact]
    public void ManifestRejectsChangedOrEscapedExternalTemplate()
    {
        var template = AddExternalTemplateToManifest();
        File.AppendAllText(template, "changed");
        var queue = NewQueue();
        Assert.False(queue.TryEnqueue(_request, out var changed));
        Assert.Equal("template_asset_changed", changed);

        WriteManifest();
        AddExternalTemplateToManifest(Path.Combine(Path.GetTempPath(), "outside.dwt"));
        queue = NewQueue();
        Assert.False(queue.TryEnqueue(_request, out var escaped));
        Assert.Equal("template_asset_outside_job", escaped);
    }

    [Fact]
    public void ManifestRejectsUnknownExternalTemplateReference()
    {
        var root = JsonNode.Parse(File.ReadAllText(_request.ManifestPath))!.AsObject();
        root["outputs"]![0]!["template_layout"] = "OFFICE_TEMPLATE";
        root["outputs"]![0]!["template_asset_id"] = "missing";
        File.WriteAllText(_request.ManifestPath, root.ToJsonString());
        RefreshManifestHash();
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("unknown_template_asset", error);
    }

    [Fact]
    public void ManifestRejectsMissingExternalTemplateReference()
    {
        AddExternalTemplateToManifest();
        var root = JsonNode.Parse(File.ReadAllText(_request.ManifestPath))!.AsObject();
        root["outputs"]![0]!["template_asset_id"] = null;
        File.WriteAllText(_request.ManifestPath, root.ToJsonString());
        RefreshManifestHash();
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("template_asset_reference_missing", error);
    }

    [Fact]
    public void ManifestRejectsRotationThatContradictsApprovedGeometry()
    {
        WriteManifest(rotationDegrees: 0);
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("plot_geometry_rotation_mismatch", error);
    }

    [Fact]
    public void ManifestRejectsSelectedScaleThatContradictsDerivedScale()
    {
        WriteManifest(scaleDenominator: 2);
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("plot_scale_mismatch", error);
    }

    [Fact]
    public void LegacyManifestWithoutReplayFieldsFailsClosed()
    {
        var root = JsonNode.Parse(File.ReadAllText(_request.ManifestPath))!.AsObject();
        foreach (var output in root["outputs"]!.AsArray())
        {
            var geometry = output!["plot_geometry"]!.AsObject();
            geometry.Remove("derived_scale_denominator");
            geometry.Remove("scale_tolerance_ratio");
        }
        File.WriteAllText(_request.ManifestPath, root.ToJsonString());
        RefreshManifestHash();
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("invalid_plot_scale", error);
    }

    [Fact]
    public void StagedDrawingChangedAfterManifestIsRejected()
    {
        File.AppendAllText(_request.StagedDrawing, "changed");
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("staged_drawing_changed", error);
    }

    [Fact]
    public void ManifestChangedAfterApprovalIsRejected()
    {
        File.AppendAllText(_request.ManifestPath, " ");
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("manifest_changed", error);
    }

    [Fact]
    public void LockedManifestReturnsBoundedAvailabilityError()
    {
        using var locked = new FileStream(
            _request.ManifestPath,
            FileMode.Open,
            FileAccess.ReadWrite,
            FileShare.None
        );
        var queue = NewQueue();

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("job_files_unavailable", error);
    }

    [Fact]
    public void DispatcherValidatesStagedJobWithoutQueueingIt()
    {
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            _workspaceRoot
        );

        var response = dispatcher.Dispatch(
            new PipeRequest
            {
                Id = "validate-1",
                Version = "1",
                Command = "validate_staged_job",
                PlanId = _request.PlanId,
                ManifestPath = _request.ManifestPath,
                ManifestSha256 = _request.ManifestSha256,
                Drawing = _request.StagedDrawing,
                OutputDirectory = _request.OutputDirectory,
                SheetCount = _request.SheetCount,
            }
        );

        Assert.True(response.Ok);
        Assert.True(response.ReadOnly);
        Assert.True(response.WorkspaceConfigured);
        Assert.Equal(_request.PlanId, response.PlanId);
        Assert.Equal(2, response.AcceptedSheetCount);
    }

    [Fact]
    public void DispatcherRequiresPluginSideWorkspaceConfiguration()
    {
        var dispatcher = new CommandDispatcher("test-adapter", () => "Test AutoCAD");

        var response = dispatcher.Dispatch(
            new PipeRequest
            {
                Id = "validate-2",
                Version = "1",
                Command = "validate_staged_job",
            }
        );

        Assert.False(response.Ok);
        Assert.Equal("workspace_not_configured", response.Error);
    }

    [Fact]
    public void DispatcherKeepsPublishMutationDisabledByDefault()
    {
        var queue = NewQueue();
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            _workspaceRoot,
            queue
        );

        var response = dispatcher.Dispatch(JobPipeRequest("queue_publish_job"));

        Assert.False(response.Ok);
        Assert.Equal("publish_disabled", response.Error);
        Assert.Equal(0, queue.PendingCount);
    }

    [Fact]
    public void EnabledDispatcherQueuesValidatedJobAndReportsStatus()
    {
        var queue = NewQueue();
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            _workspaceRoot,
            queue,
            publishEnabled: true
        );

        var queued = dispatcher.Dispatch(JobPipeRequest("queue_publish_job"));
        var status = dispatcher.Dispatch(new PipeRequest
        {
            Version = "1",
            Command = "publish_job_status",
            PlanId = _request.PlanId,
        });

        Assert.True(queued.Ok);
        Assert.False(queued.ReadOnly);
        Assert.True(queued.PublishEnabled);
        Assert.Equal("Pending", queued.JobState);
        Assert.Equal(5, queued.QueueCapacity);
        Assert.Equal(1, queued.QueuePending);
        Assert.Equal(0, queued.QueueRunning);
        Assert.Equal(4, queued.QueueAvailable);
        Assert.Equal(0, queued.QueueRecoveredOnStartup);
        Assert.Equal(0, queued.QueueInterruptedOnStartup);
        Assert.Equal(0, queued.QueueCancelledOnStartup);
        Assert.Equal(PublishQueueJournal.AuthenticationScheme, queued.QueueAuthentication);
        Assert.True(status.Ok);
        Assert.True(status.ReadOnly);
        Assert.Equal("Pending", status.JobState);
        Assert.Equal(5, status.QueueCapacity);
        Assert.Equal(1, status.QueuePending);
        Assert.Equal(0, status.QueueRunning);
        Assert.Equal(4, status.QueueAvailable);
        Assert.Equal(0, status.QueueRecoveredOnStartup);
        Assert.Equal(0, status.QueueInterruptedOnStartup);
        Assert.Equal(0, status.QueueCancelledOnStartup);
        Assert.Equal(PublishQueueJournal.AuthenticationScheme, status.QueueAuthentication);
    }

    [Fact]
    public void EnabledDispatcherDurablyCancelsOnlyExactPendingJob()
    {
        var queue = NewQueue();
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            _workspaceRoot,
            queue,
            publishEnabled: true
        );
        Assert.True(dispatcher.Dispatch(JobPipeRequest("queue_publish_job")).Ok);

        var wrong = dispatcher.Dispatch(new PipeRequest
        {
            Version = "1",
            Command = "cancel_publish_job",
            PlanId = _request.PlanId,
            ManifestSha256 = new string('b', 64),
        });
        var cancelled = dispatcher.Dispatch(new PipeRequest
        {
            Version = "1",
            Command = "cancel_publish_job",
            PlanId = _request.PlanId,
            ManifestSha256 = _request.ManifestSha256,
        });

        Assert.False(wrong.Ok);
        Assert.Equal("manifest_mismatch", wrong.Error);
        Assert.True(cancelled.Ok);
        Assert.False(cancelled.ReadOnly);
        Assert.Equal("Cancelled", cancelled.JobState);
        Assert.Equal(0, cancelled.QueuePending);
        Assert.Equal(5, cancelled.QueueAvailable);
        Assert.Equal(PublishJobState.Cancelled, queue.GetStatus(_request.PlanId)!.State);
    }

    [Fact]
    public void UnsupportedRuntimeCannotEnableOrQueuePublishing()
    {
        var queue = NewQueue();
        var dispatcher = new CommandDispatcher(
            "autocad-2025-net8",
            () => "AutoCAD 2024",
            _workspaceRoot,
            queue,
            publishEnabled: true,
            runtimeSeries: "R24.3",
            runtimeSupported: false
        );

        var status = dispatcher.Dispatch(
            new PipeRequest { Version = "1", Command = "status" }
        );
        var queued = dispatcher.Dispatch(JobPipeRequest("queue_publish_job"));

        Assert.False(status.RuntimeSupported);
        Assert.False(status.PublishEnabled);
        Assert.False(queued.Ok);
        Assert.Equal("unsupported_autocad_runtime", queued.Error);
        Assert.Equal(0, queue.PendingCount);
    }

    [Fact]
    public void StatusSeparatesCompletedJobFailureFromProtocolFailure()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        Assert.True(queue.TryStartNext(out _, out _));
        queue.Complete(_request.PlanId, succeeded: false, error: "plot_failed");
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            _workspaceRoot,
            queue,
            publishEnabled: true
        );

        var response = dispatcher.Dispatch(new PipeRequest
        {
            Version = "1",
            Command = "publish_job_status",
            PlanId = _request.PlanId,
        });

        Assert.True(response.Ok);
        Assert.Null(response.Error);
        Assert.Equal("Failed", response.JobState);
        Assert.Equal("plot_failed", response.JobError);
    }

    [Fact]
    public void WorkerCompletesOneQueuedJobSuccessfully()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        var worker = new PublishJobWorker(
            queue,
            new DelegateExecutor(_ => PublishExecutionResult.Success())
        );

        var result = worker.ProcessNext();

        Assert.True(result.Processed);
        Assert.True(result.Succeeded);
        Assert.Equal(PublishJobState.Succeeded, queue.GetStatus(_request.PlanId)!.State);
    }

    [Fact]
    public void WorkerRecordsExecutorFailure()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        var worker = new PublishJobWorker(
            queue,
            new DelegateExecutor(_ => PublishExecutionResult.Failure("plot_failed"))
        );

        var result = worker.ProcessNext();

        Assert.True(result.Processed);
        Assert.False(result.Succeeded);
        Assert.Equal("plot_failed", queue.GetStatus(_request.PlanId)!.Error);
    }

    [Fact]
    public void WorkerConvertsExecutorExceptionToSafeFailure()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        var worker = new PublishJobWorker(
            queue,
            new DelegateExecutor(_ => throw new InvalidOperationException("sensitive details"))
        );

        var result = worker.ProcessNext();

        Assert.Equal("publisher_exception:InvalidOperationException", result.Error);
        Assert.DoesNotContain("sensitive details", result.Error);
        Assert.Equal(PublishJobState.Failed, queue.GetStatus(_request.PlanId)!.State);
    }

    [Fact]
    public async Task WorkerRejectsConcurrentProcessing()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        using var entered = new ManualResetEventSlim();
        using var release = new ManualResetEventSlim();
        var worker = new PublishJobWorker(
            queue,
            new DelegateExecutor(_ =>
            {
                entered.Set();
                release.Wait(TimeSpan.FromSeconds(2));
                return PublishExecutionResult.Success();
            })
        );

        var first = Task.Run(worker.ProcessNext);
        Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        var concurrent = worker.ProcessNext();
        release.Set();
        var completed = await first;

        Assert.False(concurrent.Processed);
        Assert.Equal("worker_busy", concurrent.Error);
        Assert.True(completed.Succeeded);
    }

    [Fact]
    public void ReceiptWriterPersistsImmutableTerminalEvidence()
    {
        var writer = new PublishReceiptWriter(_workspaceRoot);

        var error = writer.Write(_request, PublishExecutionResult.Success());
        var receiptPath = Path.Combine(_jobRoot, PublishReceiptWriter.ReceiptFileName);
        using var receipt = JsonDocument.Parse(File.ReadAllText(receiptPath));

        Assert.Null(error);
        Assert.Equal(1, receipt.RootElement.GetProperty("schema_version").GetInt32());
        Assert.Equal(_request.PlanId, receipt.RootElement.GetProperty("plan_id").GetString());
        Assert.Equal(
            _request.ManifestSha256,
            receipt.RootElement.GetProperty("manifest_sha256").GetString()
        );
        Assert.Equal("succeeded", receipt.RootElement.GetProperty("state").GetString());
        Assert.False(receipt.RootElement.TryGetProperty("error", out _));
        Assert.Equal(
            "receipt_exists",
            writer.Write(_request, PublishExecutionResult.Failure("plot_failed"))
        );
    }

    [Fact]
    public void ReceiptWriterPersistsBoundedFailureEvidence()
    {
        var writer = new PublishReceiptWriter(_workspaceRoot);

        var error = writer.Write(_request, PublishExecutionResult.Failure("plot_failed"));
        using var receipt = JsonDocument.Parse(
            File.ReadAllText(Path.Combine(_jobRoot, PublishReceiptWriter.ReceiptFileName))
        );

        Assert.Null(error);
        Assert.Equal("failed", receipt.RootElement.GetProperty("state").GetString());
        Assert.Equal("plot_failed", receipt.RootElement.GetProperty("error").GetString());
    }

    [Fact]
    public void WorkerTurnsReceiptFailureIntoBoundedJobFailure()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        var worker = new PublishJobWorker(
            queue,
            new DelegateExecutor(_ => PublishExecutionResult.Success()),
            new DelegateReceiptWriter((_, _) => "receipt_write_failed")
        );

        var result = worker.ProcessNext();

        Assert.True(result.Processed);
        Assert.False(result.Succeeded);
        Assert.Equal("receipt_write_failed", result.Error);
        Assert.Equal(PublishJobState.Failed, queue.GetStatus(_request.PlanId)!.State);
    }

    private PublishJobQueue NewQueue() => new(_workspaceRoot, 5, _authenticationKeyPath);

    private PipeRequest JobPipeRequest(string command) => new()
    {
        Version = "1",
        Command = command,
        PlanId = _request.PlanId,
        ManifestPath = _request.ManifestPath,
        ManifestSha256 = _request.ManifestSha256,
        Drawing = _request.StagedDrawing,
        OutputDirectory = _request.OutputDirectory,
        SheetCount = _request.SheetCount,
    };

    private sealed class DelegateExecutor(
        Func<PublishJobRequest, PublishExecutionResult> execute
    ) : IPublishJobExecutor
    {
        public PublishExecutionResult Execute(PublishJobRequest request) => execute(request);
    }

    private sealed class DelegateReceiptWriter(
        Func<PublishJobRequest, PublishExecutionResult, string> write
    ) : IPublishReceiptWriter
    {
        public string Write(PublishJobRequest request, PublishExecutionResult execution) =>
            write(request, execution);
    }

    private void WriteManifest(
        string? firstPdf = null,
        string? templateLayout = null,
        int rotationDegrees = 90,
        double scaleDenominator = 1
    )
    {
        var outputs = Enumerable.Range(1, 2).Select(index => new
        {
            sheet_index = index,
            frame_handle = "A" + index,
            pdf = index == 1 && firstPdf is not null
                ? firstPdf
                : Path.Combine(_request.OutputDirectory, $"{index:0000}-sheet.pdf"),
            target_layout = $"CADPLOT_{index:0000}_A{index}",
            page_setup = "OFFICE_A4",
            plotter = "DWG To PDF.pc3",
            plot_style = "monochrome.ctb",
            canonical_media = "ISO_A4",
            template_layout = templateLayout,
            plot_geometry = new
            {
                window = new { min_x = 0.0, min_y = 0.0, max_x = 297.0, max_y = 210.0 },
                rotation_degrees = rotationDegrees,
                scale_denominator = scaleDenominator,
                derived_scale_denominator = 1.0,
                scale_tolerance_ratio = 0.02,
                drawing_unit_mm = 1.0,
                paper_width_mm = 210.0,
                paper_height_mm = 297.0,
            },
            status = "pending",
        });
        var manifest = new
        {
            schema_version = 1,
            job_id = Path.GetFileName(_jobRoot),
            state = "staged",
            plan_id = "sha256:" + new string('a', 64),
            staged_drawing = _request.StagedDrawing,
            output_directory = _request.OutputDirectory,
            source_fingerprint = new
            {
                sha256 = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(_request.StagedDrawing))).ToLowerInvariant(),
                size_bytes = new FileInfo(_request.StagedDrawing).Length,
            },
            outputs,
        };
        File.WriteAllText(_request.ManifestPath, JsonSerializer.Serialize(manifest));
        RefreshManifestHash();
    }

    private string AddExternalTemplateToManifest(string? manifestPath = null)
    {
        var templates = Path.Combine(_jobRoot, "source", "templates");
        Directory.CreateDirectory(templates);
        var actualTemplate = Path.Combine(templates, "office.dwt");
        if (!File.Exists(actualTemplate)) File.WriteAllText(actualTemplate, "template");
        var stagedTemplate = manifestPath ?? actualTemplate;
        var root = JsonNode.Parse(File.ReadAllText(_request.ManifestPath))!.AsObject();
        root["template_assets"] = new JsonArray
        {
            new JsonObject
            {
                ["id"] = "sheet_70x100",
                ["staged_template"] = stagedTemplate,
                ["sha256"] = Convert.ToHexString(
                    SHA256.HashData(File.ReadAllBytes(actualTemplate))
                ).ToLowerInvariant(),
                ["size_bytes"] = new FileInfo(actualTemplate).Length,
                ["layout"] = "OFFICE_TEMPLATE",
                ["page_setup"] = "OFFICE_A4",
            },
        };
        foreach (var output in root["outputs"]!.AsArray())
        {
            output!["template_layout"] = "OFFICE_TEMPLATE";
            output["template_asset_id"] = "sheet_70x100";
        }
        File.WriteAllText(_request.ManifestPath, root.ToJsonString());
        RefreshManifestHash();
        return actualTemplate;
    }

    private void RefreshManifestHash()
    {
        _request.ManifestSha256 = Convert.ToHexString(
            SHA256.HashData(File.ReadAllBytes(_request.ManifestPath))
        ).ToLowerInvariant();
    }

    public void Dispose()
    {
        Directory.Delete(_workspaceRoot, recursive: true);
        Directory.Delete(_stateRoot, recursive: true);
    }
}
