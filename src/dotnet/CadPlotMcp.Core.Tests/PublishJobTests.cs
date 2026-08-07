using CadPlotMcp.Core;
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
        File.WriteAllText(manifest, "{}");
        File.WriteAllText(drawing, "synthetic");
        _request = new PublishJobRequest
        {
            PlanId = "sha256:" + new string('a', 64),
            ManifestPath = manifest,
            StagedDrawing = drawing,
            OutputDirectory = output,
            SheetCount = 2,
        };
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
        var second = CloneWithPlan("sha256:" + new string('b', 64));

        Assert.False(queue.TryEnqueue(second, out var error));
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

    private PublishJobRequest CloneWithPlan(string planId) =>
        new()
        {
            PlanId = planId,
            ManifestPath = _request.ManifestPath,
            StagedDrawing = _request.StagedDrawing,
            OutputDirectory = _request.OutputDirectory,
            SheetCount = _request.SheetCount,
        };

    public void Dispose()
    {
        Directory.Delete(_jobRoot, recursive: true);
    }
}
