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
