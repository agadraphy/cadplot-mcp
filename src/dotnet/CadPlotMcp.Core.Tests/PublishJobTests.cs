using CadPlotMcp.Core;
using System.Security.Cryptography;
using System.Text.Json;
using Xunit;

namespace CadPlotMcp.Core.Tests;

public sealed class PublishJobTests : IDisposable
{
    private readonly string _jobRoot;
    private readonly PublishJobRequest _request;

    public PublishJobTests()
    {
        _jobRoot = Path.Combine(Path.GetTempPath(), "cadplot-tests-" + Guid.NewGuid().ToString("N"));
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
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 5);

        Assert.True(queue.TryEnqueue(_request, out var error));
        Assert.Null(error);
        Assert.Equal(PublishJobState.Pending, queue.GetStatus(_request.PlanId)!.State);
        Assert.True(queue.TryStartNext(out var started));
        Assert.Same(_request, started);
        Assert.Equal(PublishJobState.Running, queue.GetStatus(_request.PlanId)!.State);

        queue.Complete(_request.PlanId, succeeded: true);

        Assert.Equal(PublishJobState.Succeeded, queue.GetStatus(_request.PlanId)!.State);
    }

    [Fact]
    public void DuplicatePlanIsRejected()
    {
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 5);
        Assert.True(queue.TryEnqueue(_request, out _));

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("duplicate_plan", error);
    }

    [Fact]
    public void QueueCapacityIsEnforced()
    {
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 1);
        Assert.True(queue.TryEnqueue(_request, out _));

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("queue_full", error);
    }

    [Fact]
    public void DrawingOutsideJobIsRejected()
    {
        _request.StagedDrawing = Path.Combine(Path.GetTempPath(), "outside.dwg");
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 5);

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("drawing_outside_job", error);
    }

    [Fact]
    public void FailedJobKeepsAuditStatusAndCannotBeRepeated()
    {
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 5);
        Assert.True(queue.TryEnqueue(_request, out _));
        Assert.True(queue.TryStartNext(out _));

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
            var queue = new PublishJobQueue(otherWorkspace, 5);

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

        Assert.Throws<ArgumentException>(() => new PublishJobQueue(missing, 5));
    }

    [Fact]
    public void ManifestPlanMismatchIsRejected()
    {
        _request.PlanId = "sha256:" + new string('b', 64);
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 5);

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("manifest_plan_mismatch", error);
    }

    [Fact]
    public void ManifestPdfEscapeIsRejected()
    {
        WriteManifest(firstPdf: Path.Combine(Path.GetTempPath(), "outside.pdf"));
        var queue = new PublishJobQueue(Path.GetDirectoryName(_jobRoot)!, 5);

        Assert.False(queue.TryEnqueue(_request, out var error));
        Assert.Equal("pdf_outside_job", error);
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
            Path.GetDirectoryName(_jobRoot)
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
            Path.GetDirectoryName(_jobRoot),
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
            Path.GetDirectoryName(_jobRoot),
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
        Assert.True(status.Ok);
        Assert.True(status.ReadOnly);
        Assert.Equal("Pending", status.JobState);
    }

    [Fact]
    public void StatusSeparatesCompletedJobFailureFromProtocolFailure()
    {
        var queue = NewQueue();
        Assert.True(queue.TryEnqueue(_request, out _));
        Assert.True(queue.TryStartNext(out _));
        queue.Complete(_request.PlanId, succeeded: false, error: "plot_failed");
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            Path.GetDirectoryName(_jobRoot),
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

    private PublishJobQueue NewQueue() =>
        new(Path.GetDirectoryName(_jobRoot)!, 5);

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

    private void WriteManifest(string? firstPdf = null)
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
            plot_geometry = new
            {
                window = new { min_x = 0.0, min_y = 0.0, max_x = 297.0, max_y = 210.0 },
                rotation_degrees = 90,
                scale_denominator = 1.0,
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
        _request.ManifestSha256 = Convert.ToHexString(
            SHA256.HashData(File.ReadAllBytes(_request.ManifestPath))
        ).ToLowerInvariant();
    }

    public void Dispose()
    {
        Directory.Delete(_jobRoot, recursive: true);
    }
}
