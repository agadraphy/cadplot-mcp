using System.IO.Pipes;
using System.Text;
using CadPlotMcp.Core;
using Xunit;

namespace CadPlotMcp.Core.Tests;

public sealed class ProtocolTests
{
    [Fact]
    public void StatusCommandIsAllowedAndReadOnly()
    {
        var dispatcher = new CommandDispatcher("test-adapter", () => "Test AutoCAD");

        var response = dispatcher.Dispatch(
            new PipeRequest { Id = "request-1", Version = "1", Command = "status" }
        );

        Assert.True(response.Ok);
        Assert.True(response.ReadOnly);
        Assert.Equal("request-1", response.Id);
        Assert.Equal("test-adapter", response.Adapter);
    }

    [Fact]
    public void StatusCommandReportsExactRunningPluginBuildIdentity()
    {
        var commit = new string('a', 40);
        var pluginSha256 = new string('b', 64);
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            buildCommit: commit,
            pluginSha256: pluginSha256
        );

        var response = dispatcher.Dispatch(
            new PipeRequest { Id = "build-identity", Version = "1", Command = "status" }
        );

        Assert.True(response.Ok);
        Assert.Equal(commit, response.BuildCommit);
        Assert.Equal(pluginSha256, response.PluginSha256);
    }

    [Fact]
    public void StatusReportsNormalizedRuntimeAndExplicitFalseGates()
    {
        var dispatcher = new CommandDispatcher(
            "autocad-2025-net8",
            () => "AutoCAD 2025 (ACADVER R24.3; raw 24.3s (LMS Tech))",
            runtimeSeries: "R24.3",
            runtimeSupported: false
        );

        var response = dispatcher.Dispatch(
            new PipeRequest { Id = "runtime", Version = "1", Command = "status" }
        );
        var json = JsonLineCodec.WriteResponse(response);

        Assert.Equal("R24.3", response.RuntimeSeries);
        Assert.False(response.RuntimeSupported);
        Assert.False(response.WorkspaceConfigured);
        Assert.False(response.PublishEnabled);
        Assert.Null(response.QueueCapacity);
        Assert.Null(response.QueuePending);
        Assert.Null(response.QueueRunning);
        Assert.Null(response.QueueAvailable);
        Assert.Contains("\"runtimeSupported\":false", json, StringComparison.Ordinal);
        Assert.Contains("\"workspaceConfigured\":false", json, StringComparison.Ordinal);
        Assert.Contains("\"publishEnabled\":false", json, StringComparison.Ordinal);
    }

    [Fact]
    public void StatusReportsBoundedPublishQueueInitializationFailure()
    {
        var dispatcher = new CommandDispatcher(
            "test-adapter",
            () => "Test AutoCAD",
            trustedWorkspaceRoot: @"C:\CadPlot\jobs",
            publishEnabled: true,
            publishInitializationError: "publish_queue_initialization_failed"
        );

        var response = dispatcher.Dispatch(
            new PipeRequest { Id = "queue-init", Version = "1", Command = "status" }
        );
        var json = JsonLineCodec.WriteResponse(response);

        Assert.False(response.PublishEnabled);
        Assert.Equal("publish_queue_initialization_failed", response.PublishInitializationError);
        Assert.Contains(
            "\"publishInitializationError\":\"publish_queue_initialization_failed\"",
            json,
            StringComparison.Ordinal
        );
    }

    [Fact]
    public void UnknownCommandIsRejectedByPositiveWhitelist()
    {
        var dispatcher = new CommandDispatcher("test-adapter", () => "Test AutoCAD");

        var response = dispatcher.Dispatch(
            new PipeRequest { Id = "request-2", Version = "1", Command = "execute_lisp" }
        );

        Assert.False(response.Ok);
        Assert.Equal("command_not_allowed", response.Error);
    }

    [Fact]
    public void ValidPublishPreviewIsAcceptedButRemainsReadOnly()
    {
        var dispatcher = new CommandDispatcher("test-adapter", () => "Test AutoCAD");
        var planId = "sha256:" + new string('a', 64);

        var response = dispatcher.Dispatch(
            new PipeRequest
            {
                Id = "preview-1",
                Version = "1",
                Command = "preview_publish_plan",
                PlanId = planId,
                Drawing = @"C:\Projects\sample.dwg",
                SheetCount = 12,
            }
        );

        Assert.True(response.Ok);
        Assert.True(response.ReadOnly);
        Assert.Equal(planId, response.PlanId);
        Assert.Equal(12, response.AcceptedSheetCount);
    }

    [Theory]
    [InlineData("invalid", @"C:\Projects\sample.dwg", 1)]
    [InlineData("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "sample.dwg", 1)]
    [InlineData("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", @"C:\Projects\sample.pdf", 1)]
    [InlineData("sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", @"C:\Projects\sample.dwg", 0)]
    public void InvalidPublishPreviewIsRejected(string planId, string drawing, int sheetCount)
    {
        var dispatcher = new CommandDispatcher("test-adapter", () => "Test AutoCAD");

        var response = dispatcher.Dispatch(
            new PipeRequest
            {
                Id = "preview-invalid",
                Version = "1",
                Command = "preview_publish_plan",
                PlanId = planId,
                Drawing = drawing,
                SheetCount = sheetCount,
            }
        );

        Assert.False(response.Ok);
        Assert.Equal("invalid_publish_plan_preview", response.Error);
    }

    [Fact]
    public void UnsupportedProtocolVersionIsRejected()
    {
        var dispatcher = new CommandDispatcher("test-adapter", () => "Test AutoCAD");

        var response = dispatcher.Dispatch(
            new PipeRequest { Id = "request-3", Version = "999", Command = "status" }
        );

        Assert.False(response.Ok);
        Assert.Equal("unsupported_protocol_version", response.Error);
    }

    [Fact]
    public void OversizedRequestIsRejectedBeforeJsonParsing()
    {
        using var reader = new StringReader(new string('x', PipeProtocol.MaxLineCharacters + 2));

        Assert.ThrowsAny<Exception>(() => JsonLineCodec.ReadLimitedLine(reader));
    }

    [Fact]
    public void NamedPipeHostCompletesStatusRoundTrip()
    {
        var pipeName = "cadplot-mcp-test-" + Guid.NewGuid().ToString("N");
        using var host = new NamedPipeCommandHost(
            pipeName,
            new CommandDispatcher("integration", () => "AutoCAD Test")
        );
        host.Start();

        using var client = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut);
        client.Connect(2_000);
        using var writer = new StreamWriter(client, new UTF8Encoding(false), leaveOpen: true)
        {
            AutoFlush = true,
        };
        using var reader = new StreamReader(client, Encoding.UTF8, leaveOpen: true);
        writer.WriteLine("{\"id\":\"pipe-1\",\"version\":\"1\",\"command\":\"status\"}");

        var response = reader.ReadLine();

        Assert.NotNull(response);
        Assert.Contains("\"ok\":true", response, StringComparison.Ordinal);
        Assert.Contains("\"readOnly\":true", response, StringComparison.Ordinal);
    }
}
