using CadPlotMcp.Core;
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
            plot_geometry = new
            {
                window = new { min_x = 0.0, min_y = 0.0, max_x = 297.0, max_y = 210.0 },
                rotation_degrees = 90,
                scale_denominator = 1.0,
                drawing_unit_mm = 1.0,
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
            outputs,
        };
        File.WriteAllText(_request.ManifestPath, JsonSerializer.Serialize(manifest));
    }

    public void Dispose()
    {
        Directory.Delete(_jobRoot, recursive: true);
    }
}
